"""BO Action Sequence → Natural Language Build Order Document (Precision Mode v2)

Differences from v1 (bo_to_doc.py):
- ALL quantities are exact numbers (no fuzzy descriptors)
- Each step is fully self-contained: no LLM output carried across steps
- Action aggregation per step: counts by product type
- No BuildContext, no cumulative state tracking
- Output to bo_docs_precise/

Usage:
    python bo_to_doc_v2.py                    # process all 10 BOs (5 concurrent)
    python bo_to_doc_v2.py --bot banshees     # single BO
    python bo_to_doc_v2.py --max-workers 3    # 3 concurrent
"""

import json
import os
import random
import sys
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

_REPO_ROOT = os.path.abspath(os.path.join(_TOOLS_DIR, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from action_mapper import ActionMapper
from prompt_template_v2 import (
    SYSTEM_PROMPT,
    STEP_USER_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
)


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------
def _call_llm(messages: List[Dict[str, str]], model_key: str = "deepseek-v4-flash") -> str:
    try:
        from api_call.api_call import call_openai
    except ImportError:
        print("[ERROR] Cannot import api_call.api_call.")
        return ""
    return call_openai(messages=messages, model_key=model_key)


# ---------------------------------------------------------------------------
# Step splitting – same as v1
# ---------------------------------------------------------------------------
_STRATEGIC_BOUNDARY_ACTIONS = {
    "TERRANBUILD_FACTORY", "TERRANBUILD_STARPORT", "TERRANBUILD_COMMANDCENTER",
    "TERRANBUILD_ARMORY", "TERRANBUILD_FUSIONCORE", "TERRANBUILD_ENGINEERINGBAY",
    "TERRANBUILD_GHOSTACADEMY", "TERRANBUILD_BARRACKS",
    "UPGRADETOORBITAL_ORBITALCOMMAND", "RESEARCH_COMBATSHIELD",
    "BARRACKSTECHLABRESEARCH_STIMPACK", "UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS",
}

_STICKY_ACTIONS = {
    "BUILD_TECHLAB_FACTORY", "BUILD_TECHLAB_STARPORT", "BUILD_TECHLAB_BARRACKS",
    "BUILD_REACTOR_BARRACKS", "BUILD_REACTOR_FACTORY", "BUILD_REACTOR_STARPORT",
}


def _detect_boundary_scores(actions: List[str], step_sizes: Tuple[int, int] = (10, 15)) -> List[float]:
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
            count = seen_counts.get(action, 0)
            if count == 0:
                base_score = 1.0
            seen_counts[action] = count + 1
            scores[i] = base_score
        else:
            scores[i] = 0.2
        if action in _STICKY_ACTIONS:
            scores[i] = -0.5
    scores[n] = 0.0
    return scores


def split_into_steps(actions: List[str], min_size: int = 10, max_size: int = 15) -> List[Tuple[int, int]]:
    n = len(actions)
    scores = _detect_boundary_scores(actions, (min_size, max_size))
    steps: List[Tuple[int, int]] = []
    pos = 0
    while pos < n:
        remaining = n - pos
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
# Action annotation – same as v1
# ---------------------------------------------------------------------------
def annotate_actions(mapper: ActionMapper, actions: List[str]) -> List[Dict[str, Any]]:
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
# NEW: Aggregate actions within a step into a grouped summary block
# ---------------------------------------------------------------------------
def _aggregate_step_actions(step_actions: List[Dict[str, Any]]) -> str:
    """Aggregate a list of annotated actions into a grouped summary for the LLM prompt.

    Groups actions by [category] and lists product: xcount.
    Preserves original ordering within each group.
    """
    from collections import defaultdict

    # Determine category and product label for each action
    categorized = []
    for a in step_actions:
        ab = a["action"]
        product = a["product"]
        ptype = a["type"]

        if not a["has_product"]:
            # Non-macro actions: skip from aggregated block
            # (they're already excluded by order_list)
            continue

        if ab.startswith("UPGRADETO"):
            cat = "Morph"
        elif a["is_structure"]:
            cat = "Build"
        elif a["is_addon"]:
            cat = "Addon"
        elif ptype == "Unit":
            cat = "Train"
        elif ptype == "Upgrade":
            cat = "Research"
        else:
            cat = "Other"

        categorized.append({
            "cat": cat,
            "product": product,
            "action": ab,
        })

    # Count per (category, product)
    counts: Dict[Tuple[str, str], int] = {}
    order: List[Tuple[str, str]] = []
    for item in categorized:
        key = (item["cat"], item["product"])
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1

    # Build output lines, grouped by category
    cat_order = ["Build", "Train", "Addon", "Research", "Morph", "Other"]
    lines = []
    for cat in cat_order:
        entries = [(k, v) for k, v in zip(order, [counts[k] for k in order]) if k[0] == cat]
        if not entries:
            continue
        for (_, product), count in entries:
            lines.append(f"  [{cat}] {product}: x{count}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single trajectory processor (v2: no cross-step context)
# ---------------------------------------------------------------------------
def process_trajectory(
    bot_folder: str,
    seq_path: str,
    meta: Dict[str, Any],
    order_list: List[str],
    mapper: ActionMapper,
    bo_docs_dir: str,
) -> Dict[str, Any]:
    bot_name = meta.get("bot_name", bot_folder)
    map_name = meta.get("map", "Unknown")
    total_action_count = len(order_list)

    print(f"  [{bot_folder}] {total_action_count} actions, annotating...")
    annotated = annotate_actions(mapper, order_list)
    step_ranges = split_into_steps(order_list)
    total_steps = len(step_ranges)
    print(f"  [{bot_folder}] Split into {total_steps} steps: {step_ranges}")

    step_outputs: List[str] = []
    step_index_entries: List[Dict[str, Any]] = []

    # v2: NO BuildContext. Each step is independent.
    for step_idx, (start, end) in enumerate(step_ranges):
        step_num = step_idx + 1
        step_actions = annotated[start:end + 1]
        action_count = end - start + 1

        # Aggregate actions into grouped summary
        actions_block = _aggregate_step_actions(step_actions)

        user_prompt = STEP_USER_PROMPT_TEMPLATE.format(
            actions_block=actions_block,
            step_num=step_num,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        print(f"  [{bot_folder}] Step {step_num}/{total_steps} (actions {start}-{end}, {action_count} actions) -> LLM...")
        llm_output = _call_llm(messages)

        if not llm_output:
            print(f"  [{bot_folder}] Step {step_num} LLM call FAILED (empty response)")
            step_outputs.append(f"[Step {step_num}] *(LLM call failed)*")
            step_index_entries.append({
                "step": step_num, "range": [start, end],
                "action_count": action_count, "llm_call_done": False,
            })
        else:
            llm_output = llm_output.strip()
            step_outputs.append(llm_output)
            step_index_entries.append({
                "step": step_num, "range": [start, end],
                "action_count": action_count, "llm_call_done": True,
            })
            print(f"  [{bot_folder}] Step {step_num} done: {llm_output[:120]}...")

    # Generate Summary
    print(f"  [{bot_folder}] Generating Summary...")
    all_steps_text = "\n\n".join(step_outputs)
    summary_messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": SUMMARY_USER_PROMPT_TEMPLATE.format(
            bot_name=bot_name, map_name=map_name, all_steps=all_steps_text,
        )},
    ]
    summary = _call_llm(summary_messages)
    if not summary:
        summary = f"{bot_name} build order - {total_action_count} actions, {total_steps} steps."

    # Assemble markdown
    md_content = f"# Summary\n\n{summary.strip()}\n\n# Details\n\n"
    for s_out in step_outputs:
        md_content += f"{s_out}\n\n"

    safe_bot = bot_folder.replace("\\", "_").replace("/", "_")
    md_path = os.path.join(bo_docs_dir, f"{safe_bot}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"  [{bot_folder}] Done -> {md_path}")
    return {
        "bot_folder": bot_folder, "bot_name": bot_name, "map": map_name,
        "sequence_file": os.path.basename(seq_path),
        "total_actions": total_action_count, "total_steps": total_steps,
        "steps": step_index_entries, "summary": summary.strip(), "md_path": md_path,
    }


# ---------------------------------------------------------------------------
# Extract random victory sequences – same as v1
# ---------------------------------------------------------------------------
def _map_linux_path(linux_path: str) -> str:
    p = linux_path.replace("/data2/SC2_2606/sharpy-sc2/", "D:/SC2/")
    return p.replace("/", "\\")


def pick_random_victory_sequence(results_json_path: str) -> Optional[Tuple[str, Dict[str, Any], List[str]]]:
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
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="BO Action Sequence -> Natural Language Docs (Precision v2)")
    parser.add_argument("--bot", type=str, default=None)
    parser.add_argument("--max-workers", type=int, default=5)
    parser.add_argument("--data-dir", type=str,
                        default=r"D:\SC2\bo_collection_runs\2026-06-16_terran_bo_commitfix_v5")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir or os.path.join(_TOOLS_DIR, "bo_docs_precise")
    os.makedirs(output_dir, exist_ok=True)

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
                    "bot_name": result["bot_name"], "map": result["map"],
                    "sequence_file": result["sequence_file"],
                    "total_actions": result["total_actions"],
                    "total_steps": result["total_steps"],
                    "steps": result["steps"], "md_path": result["md_path"],
                }
                success_count += 1
                print(f"  [{bot_folder}] COMPLETED ({result['total_steps']} steps)")
            except Exception as exc:
                print(f"  [{bot_folder}] FAILED: {exc}")
                traceback.print_exc()
                fail_count += 1

    summary_data = {
        "total_trajectories": len(trajectories),
        "success": success_count, "failed": fail_count,
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
