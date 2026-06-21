"""
BO Action Sequence -> Natural Language Build Order Document

Pipeline:
1. Traverse 10 BO folders, randomly pick 1 victory sequence each
2. Annotate all actions with action_mapper (product, type, cost)
3. Split into steps (10-15 actions, strategic boundary aware)
4. Per-step LLM call (serial within trajectory, concurrent across)
5. Generate Summary via separate LLM call
6. Output .md + step_index.json

Usage:
    python bo_to_doc.py                    # process all 10 BOs (5 concurrent)
    python bo_to_doc.py --bot banshees     # single BO
    python bo_to_doc.py --max-workers 3    # 3 concurrent
"""

import json
import os
import random
import sys
import time
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

# Ensure Tools/ is on path for imports
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# Also add repo root for api_call import
_REPO_ROOT = os.path.abspath(os.path.join(_TOOLS_DIR, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from action_mapper import ActionMapper
from prompt_template import (
    SYSTEM_PROMPT,
    STEP_USER_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
)

# ---------------------------------------------------------------------------
# LLM caller - thin wrapper around api_call.call_openai
# ---------------------------------------------------------------------------
def _call_llm(
    messages: List[Dict[str, str]],
    model_key: str = "deepseek-v4-flash",
) -> str:
    """Call DeepSeek API via the existing api_call module. Returns cleaned text or empty string."""
    try:
        from api_call.api_call import call_openai
    except ImportError:
        print("[ERROR] Cannot import api_call.api_call. Check sys.path.")
        return ""

    return call_openai(
        messages=messages,
        model_key=model_key,
    )


# ---------------------------------------------------------------------------
# Strategic boundary detection for step splitting
# ---------------------------------------------------------------------------

# Actions that signal the START of a new strategic phase
_STRATEGIC_BOUNDARY_ACTIONS = {
    "TERRANBUILD_FACTORY",
    "TERRANBUILD_STARPORT",
    "TERRANBUILD_COMMANDCENTER",
    "TERRANBUILD_ARMORY",
    "TERRANBUILD_FUSIONCORE",
    "TERRANBUILD_ENGINEERINGBAY",
    "TERRANBUILD_GHOSTACADEMY",
    "TERRANBUILD_BARRACKS",         # second+ Barracks
    "UPGRADETOORBITAL_ORBITALCOMMAND",
    "RESEARCH_COMBATSHIELD",
    "BARRACKSTECHLABRESEARCH_STIMPACK",
    "UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS",
}

# Actions that should ideally NOT be split from their immediate predecessor
_STICKY_ACTIONS = {
    "BUILD_TECHLAB_FACTORY",
    "BUILD_TECHLAB_STARPORT",
    "BUILD_TECHLAB_BARRACKS",
    "BUILD_REACTOR_BARRACKS",
    "BUILD_REACTOR_FACTORY",
    "BUILD_REACTOR_STARPORT",
}


def _detect_boundary_scores(
    actions: List[str],
    step_sizes: Tuple[int, int] = (10, 15),
) -> List[float]:
    """Return a boundary score [0..1] for each position in the action list."""
    n = len(actions)
    scores = [0.0] * (n + 1)

    min_sz, max_sz = step_sizes
    seen_counts: Dict[str, int] = {}

    for i, action in enumerate(actions):
        if i == 0:
            scores[i] = -1.0
            continue

        if action in _STRATEGIC_BOUNDARY_ACTIONS:
            base_score = 0.8
            building_key = action
            count = seen_counts.get(building_key, 0)
            if count == 0:
                base_score = 1.0
            seen_counts[building_key] = count + 1
            scores[i] = base_score
        else:
            scores[i] = 0.2

        if action in _STICKY_ACTIONS:
            scores[i] = -0.5

    scores[n] = 0.0
    return scores


def split_into_steps(
    actions: List[str],
    min_size: int = 10,
    max_size: int = 15,
) -> List[Tuple[int, int]]:
    """Split action indices into step ranges [(start, end), ...]."""
    n = len(actions)
    scores = _detect_boundary_scores(actions, (min_size, max_size))

    steps: List[Tuple[int, int]] = []
    pos = 0

    while pos < n:
        remaining = n - pos
        if remaining <= max_size:
            steps.append((pos, n - 1))
            break
        if remaining <= max_size + 3:
            steps.append((pos, n - 1))
            break

        best_split = pos + min_size
        best_score = -999.0

        for candidate in range(pos + min_size, min(pos + max_size + 1, n)):
            if scores[candidate] > best_score:
                best_score = scores[candidate]
                best_split = candidate

        steps.append((pos, best_split - 1))
        pos = best_split

    return steps


# ---------------------------------------------------------------------------
# Action annotation
# ---------------------------------------------------------------------------
def annotate_actions(mapper: ActionMapper, actions: List[str]) -> List[Dict[str, Any]]:
    """Annotate each action with its product info."""
    annotated = []
    for i, ab in enumerate(actions):
        product = mapper.get_product(ab)
        info = mapper.get_product_info(ab)
        req = mapper.get_required_structure(ab)
        annotated.append({
            "index": i,
            "action": ab,
            "product": product,
            "type": info.get("type", "-") if info else "-",
            "minerals": info.get("minerals", 0) if info else 0,
            "gas": info.get("gas", 0) if info else 0,
            "requires": req,
            "is_structure": info.get("is_structure", False) if info else False,
            "is_addon": info.get("is_addon", False) if info else False,
            "has_product": mapper.has_product(ab),
        })
    return annotated


# ---------------------------------------------------------------------------
# Cumulative context tracking
# ---------------------------------------------------------------------------
class BuildContext:
    """Tracks cumulative build state across steps for context injection."""

    def __init__(self):
        self.buildings: Dict[str, int] = {}
        self.units: Dict[str, int] = {}
        self.upgrades: List[str] = []
        self.total_scvs = 12
        self.orbital_done = False

    def update_from_actions(self, actions: List[Dict[str, Any]]):
        """Update context by consuming annotated actions."""
        for a in actions:
            ab = a["action"]
            product = a["product"]
            ptype = a["type"]

            if product == "(no product)":
                continue

            if ptype == "Unit":
                if a["is_structure"] or a["is_addon"]:
                    self.buildings[product] = self.buildings.get(product, 0) + 1
                else:
                    self.units[product] = self.units.get(product, 0) + 1

            elif ptype == "Upgrade":
                if product not in self.upgrades:
                    self.upgrades.append(product)

            if ab == "UPGRADETOORBITAL_ORBITALCOMMAND":
                self.orbital_done = True
            if product == "SCV":
                self.total_scvs += 1

    def ordinal(self, building_name: str) -> str:
        count = self.buildings.get(building_name, 0) + 1
        ordinals = ["first", "second", "third", "fourth", "fifth", "sixth"]
        if count <= len(ordinals):
            return ordinals[count - 1]
        return f"{count}th"

    def summarize(self) -> str:
        lines = []
        bld_lines = []
        for name, count in sorted(self.buildings.items()):
            if count == 1:
                bld_lines.append(f"1st {name}")
            else:
                bld_lines.append(f"{count}x {name}")
        if bld_lines:
            lines.append("Structures: " + ", ".join(bld_lines))

        unit_lines = []
        for name, count in sorted(self.units.items()):
            unit_lines.append(f"{count}x {name}")
        if unit_lines:
            lines.append("Units: " + ", ".join(unit_lines))

        lines.append(f"SCV count: {self.total_scvs}")
        if self.upgrades:
            lines.append("Upgrades done: " + ", ".join(self.upgrades))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single trajectory processor (serial steps)
# ---------------------------------------------------------------------------
def process_trajectory(
    bot_folder: str,
    seq_path: str,
    meta: Dict[str, Any],
    order_list: List[str],
    mapper: ActionMapper,
    bo_docs_dir: str,
) -> Dict[str, Any]:
    """Process one BO trajectory: annotate, split, call LLM per step, assemble output."""
    bot_name = meta.get("bot_name", bot_folder)
    map_name = meta.get("map", "Unknown")
    total_action_count = len(order_list)

    print(f"  [{bot_folder}] {total_action_count} actions, annotating...")

    annotated = annotate_actions(mapper, order_list)
    step_ranges = split_into_steps(order_list)
    total_steps = len(step_ranges)
    print(f"  [{bot_folder}] Split into {total_steps} steps: {step_ranges}")

    context = BuildContext()
    step_outputs: List[str] = []
    step_index_entries: List[Dict[str, Any]] = []

    for step_idx, (start, end) in enumerate(step_ranges):
        step_num = step_idx + 1
        step_actions = annotated[start:end + 1]
        action_count = end - start + 1

        actions_block_lines = []
        for a in step_actions:
            line = f"  {a['index']}. {a['action']}"
            if a["has_product"]:
                ptype_label = {
                    "Unit": "建造" if a["is_structure"] else "训练",
                    "Upgrade": "升级",
                }.get(a["type"], a["type"])
                cost_str = ""
                if a["minerals"] or a["gas"]:
                    cost_str = f" ({a['minerals']}矿/{a['gas']}气)"
                line += f" -> [{ptype_label}] {a['product']}{cost_str}"
            actions_block_lines.append(line)
        actions_block = "\n".join(actions_block_lines)

        context_summary = context.summarize()

        user_prompt = STEP_USER_PROMPT_TEMPLATE.format(
            context=context_summary,
            actions_block=actions_block,
            step_num=step_num,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        print(f"  [{bot_folder}] Step {step_num}/{total_steps} (actions {start}-{end}, {action_count} actions) -> LLM...")
        llm_output = _call_llm(messages)

        context.update_from_actions(step_actions)

        if not llm_output:
            print(f"  [{bot_folder}] Step {step_num} LLM call FAILED (empty response)")
            step_outputs.append(f"[Step {step_num}] *(LLM call failed)*")
            step_index_entries.append({
                "step": step_num,
                "range": [start, end],
                "action_count": action_count,
                "llm_call_done": False,
            })
        else:
            llm_output = llm_output.strip()
            step_outputs.append(llm_output)
            step_index_entries.append({
                "step": step_num,
                "range": [start, end],
                "action_count": action_count,
                "llm_call_done": True,
            })
            print(f"  [{bot_folder}] Step {step_num} done: {llm_output[:100]}...")

    # Generate Summary via separate LLM call
    print(f"  [{bot_folder}] Generating Summary...")
    all_steps_text = "\n\n".join(step_outputs)
    summary_messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": SUMMARY_USER_PROMPT_TEMPLATE.format(
            bot_name=bot_name,
            map_name=map_name,
            all_steps=all_steps_text,
        )},
    ]
    summary = _call_llm(summary_messages)
    if not summary:
        summary = f"{bot_name} build order - {total_action_count} actions, {total_steps} steps."

    # Assemble markdown
    md_content = f"# Summary\n\n{summary.strip()}\n\n# Details\n\n"
    for s_out in step_outputs:
        md_content += f"{s_out}\n\n"

    # Write outputs
    safe_bot = bot_folder.replace("\\", "_").replace("/", "_")
    md_path = os.path.join(bo_docs_dir, f"{safe_bot}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"  [{bot_folder}] Done -> {md_path}")

    return {
        "bot_folder": bot_folder,
        "bot_name": bot_name,
        "map": map_name,
        "sequence_file": os.path.basename(seq_path),
        "total_actions": total_action_count,
        "total_steps": total_steps,
        "steps": step_index_entries,
        "summary": summary.strip(),
        "md_path": md_path,
    }


# ---------------------------------------------------------------------------
# Extract random victory sequences
# ---------------------------------------------------------------------------
def _map_linux_path(linux_path: str) -> str:
    """Convert Linux paths from results.json to local Windows paths."""
    p = linux_path.replace("/data2/SC2_2606/sharpy-sc2/", "D:/SC2/")
    p = p.replace("/", "\\")
    return p


def pick_random_victory_sequence(results_json_path: str) -> Optional[Tuple[str, Dict[str, Any], List[str]]]:
    """Given a results.json, randomly pick one victory match whose sequence file
    has a non-empty order_list, trying shuffled victories until one works."""
    if not os.path.exists(results_json_path):
        return None

    with open(results_json_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    matches = results.get("matches", [])
    victories = [m for m in matches if m.get("victory")]

    if not victories:
        victories = matches

    if not victories:
        return None

    random.shuffle(victories)

    for chosen in victories:
        seq_file_linux = chosen.get("sequence_file", "")
        seq_file = _map_linux_path(seq_file_linux)

        if not seq_file or not os.path.exists(seq_file):
            continue

        try:
            with open(seq_file, "r", encoding="utf-8") as f:
                seq_data = json.load(f)
        except Exception:
            continue

        meta = seq_data.get("meta", {})
        order_list = seq_data.get("order_list", [])

        if order_list:
            return (seq_file, meta, order_list)

    print(f"  No valid sequence (with order_list) found among victories")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="BO Action Sequence -> Natural Language Docs")
    parser.add_argument("--bot", type=str, default=None,
                        help="Process only this bot folder (e.g. banshees)")
    parser.add_argument("--max-workers", type=int, default=5,
                        help="Max concurrent trajectories (default 5)")
    parser.add_argument("--data-dir", type=str,
                        default=r"D:\SC2\bo_collection_runs\2026-06-16_terran_bo_commitfix_v5",
                        help="Path to the data directory with BO folders")
    parser.add_argument("--output-dir", type=str,
                        default=None,
                        help="Output directory for bo_docs (default: Tools/bo_docs)")
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir or os.path.join(_TOOLS_DIR, "bo_docs")
    os.makedirs(output_dir, exist_ok=True)

    # Find all BO folders (subdirectories with results.json)
    bo_folders = []
    for entry in os.listdir(data_dir):
        folder_path = os.path.join(data_dir, entry)
        results_path = os.path.join(folder_path, "results.json")
        if os.path.isdir(folder_path) and os.path.exists(results_path):
            if args.bot and entry != args.bot:
                continue
            bo_folders.append((entry, results_path))

    if not bo_folders:
        print(f"No BO folders found in {data_dir}")
        return

    print(f"Found {len(bo_folders)} BO folders. Picking random victory sequences...")

    trajectories = []
    for bot_folder, results_path in sorted(bo_folders):
        result = pick_random_victory_sequence(results_path)
        if result is None:
            print(f"  [{bot_folder}] SKIP - no valid victory sequence found")
            continue
        seq_path, meta, order_list = result
        print(f"  [{bot_folder}] Selected: {os.path.basename(seq_path)} ({len(order_list)} actions, {meta.get('enemy_race','?')} {meta.get('difficulty','?')})")
        trajectories.append((bot_folder, seq_path, meta, order_list))

    if not trajectories:
        print("No trajectories to process.")
        return

    print(f"\nProcessing {len(trajectories)} trajectories with {args.max_workers} workers...\n")

    mapper = ActionMapper()

    step_index_data: Dict[str, Any] = {}
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_bot = {}
        for bot_folder, seq_path, meta, order_list in trajectories:
            future = executor.submit(
                process_trajectory,
                bot_folder, seq_path, meta, order_list, mapper, output_dir,
            )
            future_to_bot[future] = bot_folder

        for future in as_completed(future_to_bot):
            bot_folder = future_to_bot[future]
            try:
                result = future.result()
                step_index_data[bot_folder] = {
                    "bot_name": result["bot_name"],
                    "map": result["map"],
                    "sequence_file": result["sequence_file"],
                    "total_actions": result["total_actions"],
                    "total_steps": result["total_steps"],
                    "steps": result["steps"],
                    "md_path": result["md_path"],
                }
                success_count += 1
                print(f"  [{bot_folder}] COMPLETED ({result['total_steps']} steps)")
            except Exception as exc:
                print(f"  [{bot_folder}] FAILED: {exc}")
                traceback.print_exc()
                fail_count += 1

    # Write step_index.json
    summary_data = {
        "total_trajectories": len(trajectories),
        "success": success_count,
        "failed": fail_count,
        "bo_data": step_index_data,
    }
    index_path = os.path.join(output_dir, "step_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    print(f"\n=== Done ===")
    print(f"Success: {success_count}, Failed: {fail_count}")
    print(f"Output: {output_dir}")
    print(f"Index:  {index_path}")


if __name__ == "__main__":
    main()
