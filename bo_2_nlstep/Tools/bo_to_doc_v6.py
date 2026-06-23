"""
BO Action Sequence -> Natural Language Build Order Document (Concise Style Summary v6)

Based on v5, with changes:
  - Step descriptions unchanged from v5/v4/v3 (Slang style, no cross-step context)
  - 3-sentence balanced Summary unchanged from v5 (Early / Mid / Late)
  - Final Step redesigned: brief strategic style characterization instead of detailed follow-up 



Usage:
    python bo_to_doc_v5.py                    # process all 10 BOs (5 concurrent)
    python bo_to_doc_v5.py --bot banshees     # single BO
    python bo_to_doc_v5.py --max-workers 3    # 3 concurrent
"""

import json
import os
import random
import re
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
from prompt_template_v6 import (
    SYSTEM_PROMPT,
    STEP_USER_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
    FINAL_STEP_SYSTEM_PROMPT,
    FINAL_STEP_USER_PROMPT_TEMPLATE,
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


_LLM_MAX_RETRIES = 6
_LLM_RETRY_BASE_SLEEP_SEC = 3
_LLM_FAILED_MARK = "*(LLM call failed)*"


def _call_llm_with_retry(
    messages: List[Dict[str, str]],
    *,
    label: str = "",
    model_key: str = "deepseek-v4-flash",
) -> str:
    """Call LLM with retries; returns non-empty text or raises on exhaustion."""
    last_error = ""
    for attempt in range(1, _LLM_MAX_RETRIES + 1):
        output = (_call_llm(messages, model_key=model_key) or "").strip()
        if output:
            if attempt > 1:
                print(f"    LLM recovered on attempt {attempt}/{_LLM_MAX_RETRIES} ({label})")
            return output
        last_error = "empty response"
        if attempt < _LLM_MAX_RETRIES:
            sleep_sec = _LLM_RETRY_BASE_SLEEP_SEC * attempt
            print(
                f"    LLM attempt {attempt}/{_LLM_MAX_RETRIES} failed ({label}); "
                f"retrying in {sleep_sec}s..."
            )
            time.sleep(sleep_sec)
    raise RuntimeError(f"LLM call failed after {_LLM_MAX_RETRIES} attempts ({label}): {last_error}")


_FINAL_STEP_CLOSING = (
    "Use this gameplan as your strategic baseline -- adapt your decisions based on what you scout and how the game unfolds."
)


def _is_closing_only_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s in {
        _FINAL_STEP_CLOSING,
        _FINAL_STEP_CLOSING.replace("gameplan", "gameplayplan"),
    }:
        return True
    return s.startswith("Use this game") and "strategic baseline" in s


def _normalize_final_step_output(text: str, step_num: int) -> str:
    """Merge a standalone closing sentence back into the final [Step N] line."""
    text = (text or "").strip()
    if not text:
        return f"[Step {step_num}] {_FINAL_STEP_CLOSING}"

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return f"[Step {step_num}] {_FINAL_STEP_CLOSING}"

    if len(lines) == 1:
        line = lines[0]
        if not line.startswith("[Step"):
            line = f"[Step {step_num}] {line}"
        if "strategic baseline" not in line:
            return f"{line} {_FINAL_STEP_CLOSING}"
        return line

    if _is_closing_only_line(lines[-1]):
        closing = lines[-1]
        step_line = ""
        extra: List[str] = []
        for bl in lines[:-1]:
            if bl.startswith("[Step"):
                step_line = bl
            else:
                extra.append(bl)
        if not step_line:
            step_line = f"[Step {step_num}] {' '.join(extra)}" if extra else f"[Step {step_num}]"
        elif extra:
            step_line = f"{step_line} {' '.join(extra)}"
        if "strategic baseline" not in step_line:
            step_line = f"{step_line} {closing}"
        return step_line

    merged = " ".join(lines)
    if not merged.startswith("[Step"):
        merged = f"[Step {step_num}] {merged}"
    if "strategic baseline" not in merged:
        merged = f"{merged} {_FINAL_STEP_CLOSING}"
    return merged


def _fix_final_step_in_markdown(md_text: str) -> tuple[str, bool]:
    """Fix standalone final-step closing paragraph in an existing v6 markdown file."""
    marker = "\n# Details\n\n"
    if marker not in md_text:
        return md_text, False

    head, details = md_text.split(marker, 1)
    parts = [p.strip() for p in details.rstrip().split("\n\n") if p.strip()]
    if not parts:
        return md_text, False

    if not _is_closing_only_line(parts[-1]):
        return md_text, False

    closing = parts[-1]
    if len(parts) == 1:
        fixed_last = f"[Step 1] {closing}"
        return head + marker + fixed_last + "\n\n", True

    prev = parts[-2]
    if not prev.startswith("[Step "):
        return md_text, False

    m = re.match(r"^\[Step\s+(\d+)\]", prev)
    step_num = int(m.group(1)) if m else 1
    fixed_last = _normalize_final_step_output(f"{prev}\n\n{closing}", step_num)
    if fixed_last == prev:
        return md_text, False

    parts = parts[:-2] + [fixed_last]
    return head + marker + "\n\n".join(parts) + "\n\n", True


_FINAL_STEP_CLOSINGS = (
    "Use this gameplan as your strategic baseline -- adapt your decisions based on what you scout and how the game unfolds.",
    "Use this gameplayplan as your strategic baseline -- adapt your decisions based on what you scout and how the game unfolds.",
)


def _normalize_final_step_output(text: str, step_num: int) -> str:
    """Keep the v6 closing sentence on the same [Step N] line."""
    text = (text or "").strip()
    if not text:
        return f"[Step {step_num}] {_FINAL_STEP_CLOSINGS[0]}"

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return f"[Step {step_num}] {_FINAL_STEP_CLOSINGS[0]}"

    closing_line = None
    body_lines: List[str] = []
    for line in lines:
        if line in _FINAL_STEP_CLOSINGS:
            closing_line = line
            continue
        if any(line.endswith(closing) for closing in _FINAL_STEP_CLOSINGS):
            return line
        body_lines.append(line)

    closing = closing_line or _FINAL_STEP_CLOSINGS[0]
    if not body_lines:
        return f"[Step {step_num}] {closing}"

    main = " ".join(body_lines)
    if closing in main:
        return main
    if not main.startswith("[Step"):
        main = f"[Step {step_num}] {main}"
    return f"{main} {closing}"


# ---------------------------------------------------------------------------
# Strategic boundary detection for step splitting (same as v1/v4)
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
# Action annotation (same as v1/v4)
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
# Single trajectory processor (v6: Balanced Summary + Concise Final Step)
# ---------------------------------------------------------------------------
def process_trajectory(
    bot_folder: str,
    seq_path: str,
    meta: Dict[str, Any],
    order_list: List[str],
    mapper: ActionMapper,
    bo_docs_dir: str,
) -> Dict[str, Any]:
    """Process one BO trajectory: annotate, split, call LLM per step, 
    generate 3-sentence balanced summary, generate concise final step, assemble output."""
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

    # v5: NO cross-step context. Each step is independent (same as v4/v3).
    for step_idx, (start, end) in enumerate(step_ranges):
        step_num = step_idx + 1
        step_actions = annotated[start:end + 1]
        action_count = end - start + 1

        # Build actions block (same format as v1/v4)
        actions_block_lines = []
        for a in step_actions:
            line = f"  {a['index']}. {a['action']}"
            if a["has_product"]:
                ptype_label = {
                    "Unit": "\u8bad\u7ec3" if a["is_structure"] else "\u5efa\u9020",
                    "Upgrade": "\u5347\u7ea7",
                }.get(a["type"], a["type"])
                cost_str = ""
                if a["minerals"] or a["gas"]:
                    cost_str = f" ({a['minerals']}\u77ff {a['gas']}\u6c14)"
                line += f" -> [{ptype_label}] {a['product']}{cost_str}"
            actions_block_lines.append(line)
        actions_block = "\n".join(actions_block_lines)

        user_prompt = STEP_USER_PROMPT_TEMPLATE.format(
            actions_block=actions_block,
            step_num=step_num,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        print(f"  [{bot_folder}] Step {step_num}/{total_steps} (actions {start}-{end}, {action_count} actions) -> LLM...")
        llm_output = _call_llm_with_retry(
            messages,
            label=f"{bot_folder} step {step_num}/{total_steps}",
        )
        llm_output = llm_output.strip()
        step_outputs.append(llm_output)
        step_index_entries.append({
            "step": step_num,
            "range": [start, end],
            "action_count": action_count,
            "llm_call_done": True,
        })
        print(f"  [{bot_folder}] Step {step_num} done: {llm_output[:120]}...")

    # -----------------------------------------------------------------------
    # Generate 3-sentence Balanced Summary (early/mid/late)
    # -----------------------------------------------------------------------
    print(f"  [{bot_folder}] Generating Balanced Summary (3-sentence: Early/Mid/Late)...")
    all_steps_text = "\n\n".join(step_outputs)
    summary_messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": SUMMARY_USER_PROMPT_TEMPLATE.format(
            bot_name=bot_name,
            map_name=map_name,
            all_steps=all_steps_text,
        )},
    ]
    summary = _call_llm_with_retry(
        summary_messages,
        label=f"{bot_folder} summary",
    )
    summary = summary.strip()

    # -----------------------------------------------------------------------
    # Generate Concise Strategic Style Final Step (v6: brief characterization only)
    # -----------------------------------------------------------------------
    print(f"  [{bot_folder}] Generating Concise Strategic Style Final Step...")
    final_step_num = total_steps + 1
    final_step_messages = [
        {"role": "system", "content": FINAL_STEP_SYSTEM_PROMPT},
        {"role": "user", "content": FINAL_STEP_USER_PROMPT_TEMPLATE.format(
            bot_name=bot_name,
            map_name=map_name,
            summary=summary,
            all_steps=all_steps_text,
            step_num=final_step_num,
        )},
    ]
    final_step_output = _call_llm_with_retry(
        final_step_messages,
        label=f"{bot_folder} final step",
    )
    final_step_output = _normalize_final_step_output(final_step_output.strip(), final_step_num)

    # Record final step in step index
    step_index_entries.append({
        "step": final_step_num,
        "range": None,
        "action_count": None,
        "llm_call_done": bool(final_step_output),
        "is_final_step": True,
    })

    # -----------------------------------------------------------------------
    # Assemble markdown
    # -----------------------------------------------------------------------
    if any(_LLM_FAILED_MARK in s for s in step_outputs):
        raise RuntimeError(f"[{bot_folder}] refused to write markdown with failed step placeholders")

    md_content = f"# Summary\n\n{summary}\n\n# Details\n\n"
    for s_out in step_outputs:
        md_content += f"{s_out}\n\n"
    # Append the Concise Strategic Style Step as the final entry in Details
    md_content += f"{final_step_output}\n\n"

    # Write output
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
        "summary": summary,
        "final_step": final_step_output,
        "md_path": md_path,
    }


# ---------------------------------------------------------------------------
# Extract highest-difficulty victory sequences (same as v3/v4)
# ---------------------------------------------------------------------------
def _map_linux_path(linux_path: str) -> str:
    """Convert Linux paths from results.json to local Windows paths."""
    p = linux_path.replace("/data2/SC2_2606/sharpy-sc2/", "D:/SC2/")
    p = p.replace("/", "\\")
    return p


_DIFFICULTY_RANK = {
    "veryhard": 5,
    "harder": 4,
    "hard": 3,
    "mediumhard": 2,
    "medium": 1,
}


def pick_hardest_victory_sequence(results_json_path: str) -> Optional[Tuple[str, Dict[str, Any], List[str]]]:
    """Given a results.json, pick the victory match with the highest difficulty
    that has a valid sequence file with a non-empty order_list."""
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

    # Sort by difficulty (highest first), ties broken randomly
    victories.sort(
        key=lambda m: (_DIFFICULTY_RANK.get(m.get("difficulty", ""), 0), random.random()),
        reverse=True,
    )

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

    print(f"  No valid sequence (with order_list) found among hardest difficulty victories")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="BO Action Sequence -> Natural Language Docs (v6 Concise Style Summary)")
    parser.add_argument("--bot", type=str, default=None,
                        help="Process only this bot folder (e.g. banshees)")
    parser.add_argument("--max-workers", type=int, default=5,
                        help="Max concurrent trajectories (default 5)")
    parser.add_argument("--data-dir", type=str,
                        default=r"D:\SC2\bo_collection_runs\2026-06-16_terran_bo_commitfix_v5",
                        help="Path to the data directory with BO folders")
    parser.add_argument("--output-dir", type=str,
                        default=None,
                        help="Output directory for bo_docs (default: Tools/bo_docs_concise)")
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir or os.path.join(_TOOLS_DIR, "bo_docs_concise")
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

    print(f"Found {len(bo_folders)} BO folders. Picking highest-difficulty victory sequences...")

    trajectories = []
    for bot_folder, results_path in sorted(bo_folders):
        result = pick_hardest_victory_sequence(results_path)
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
                print(f"  [{bot_folder}] COMPLETED ({result['total_steps']} steps + concise)")
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
