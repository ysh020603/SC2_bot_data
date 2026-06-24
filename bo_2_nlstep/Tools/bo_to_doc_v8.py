"""
BO Action Sequence -> Natural Language Build Order Document (Situation-Aware Fuzzy v8)

Based on v6, with changes:
  - Step descriptions keep v6 Slang style and no cross-step context
  - Ordinal wording is removed from step descriptions
  - 3-sentence balanced Summary unchanged from v5 (Early / Mid / Late)
  - Final Step redesigned: brief strategic style characterization instead of detailed follow-up 
Based on v7, with additions:
  - Each normal step gets action-derived soft macro-control cues
  - The LLM blends those cues into natural fuzzy coaching language
  - Summary and final step rules are preserved from v7



Usage:
    python bo_to_doc_v8.py                    # process all 10 BOs (5 concurrent)
    python bo_to_doc_v8.py --bot banshees     # single BO
    python bo_to_doc_v8.py --max-workers 3    # 3 concurrent
    python bo_to_doc_v8.py --bot banshees --model-key kimi-k2.5
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
from prompt_template_v8 import (
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


def _single_line(text: str) -> str:
    """Collapse model output into the required single-line step format."""
    return " ".join((text or "").strip().split())


def _remove_disallowed_ordinals(text: str) -> str:
    """Remove counted ordinal wording while preserving SC2 labels like depot-first."""
    replacements = {
        "first": "a",
        "1st": "a",
        "second": "another",
        "2nd": "another",
        "third": "another",
        "3rd": "another",
        "fourth": "another",
        "4th": "another",
        "fifth": "another",
        "5th": "another",
        "sixth": "another",
        "seventh": "another",
        "eighth": "another",
        "ninth": "another",
        "tenth": "another",
    }

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        replacement = replacements[token.lower()]
        return replacement.capitalize() if token[0].isupper() else replacement

    text = re.sub(r"(?<!-)\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|4th|5th)\b(?!-)", repl, text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe\s+a\s+(?!one\b)", "a ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(your|my|our|their)\s+a\s+(?!one\b)", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba\s+another\b", "another", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe\s+another\b", "another", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe\s+a\s+one\b", "an existing one", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe\s+another\s+one\b", "another one", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!-)\bnext one\b(?!-)", "another one", text, flags=re.IGNORECASE)
    return text


def _normalize_model_text(text: str) -> str:
    return _remove_disallowed_ordinals(_single_line(text))


def _sanitize_v8_normal_step_text(text: str) -> str:
    """Remove enemy-analysis wording from normal V8 steps only."""
    replacements = (
        (r"\bscout(?:ing)?\s+enemy\s+threats?\b", "read the map state"),
        (r"\benemy\s+threats?\b", "map-state options"),
        (r"\bto\s+pressure\s+the\s+game\b", "to add mobile map presence"),
        (r"\bapply\s+pressure\b", "add production tempo"),
        (r"\bcloaked\s+harassers\b", "cloaked tech units"),
        (r"\bharassers\b", "mobile units"),
        (r"\bharasser\b", "mobile unit"),
        (r"\bcloaked\s+harass(?:ment)?\s+units\b", "cloaked tech units"),
        (r"\bharass(?:ment)?\s+units\b", "mobile units"),
        (r"\bharrassment\s+units\b", "mobile units"),
        (r"\bharassment\b", "mobile map presence"),
        (r"\bharrassment\b", "mobile map presence"),
        (r"\bharass\b", "apply mobile map presence"),
        (r"\bharassing\b", "using mobile map presence"),
        (r"\bpressure\b", "production tempo"),
        (r"\bthreats\b", "options"),
        (r"\bthreat\b", "option"),
        (r"\brushes\b", "fast timings"),
        (r"\brush\b", "fast timing"),
        (r"\bscouting\b", "vision"),
        (r"\bscout\b", "vision"),
        (r"\benemy\b", "map"),
        (r"\bopponents\b", "games"),
        (r"\bopponent\b", "game"),
        (r"\bmove-out\b", "advance"),
        (r"\bmove out\b", "advance"),
        (r"\bpoke\b", "small advance"),
        (r"\bhold\b", "stabilize"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\bapply mobile map presence production tempo\b", "add mobile map presence", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmobile map presence production tempo\b", "mobile map presence", text, flags=re.IGNORECASE)
    return _single_line(text)


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
# v8 soft macro-control cues
# ---------------------------------------------------------------------------
_TRAIN_PREFIXES = (
    "COMMANDCENTERTRAIN_",
    "BARRACKSTRAIN_",
    "FACTORYTRAIN_",
    "STARPORTTRAIN_",
    "TRAIN_",
)

_PRODUCTION_STRUCTURE_ACTIONS = {
    "TERRANBUILD_BARRACKS",
    "TERRANBUILD_FACTORY",
    "TERRANBUILD_STARPORT",
}

_GAS_HEAVY_ACTIONS = {
    "TERRANBUILD_FACTORY",
    "TERRANBUILD_STARPORT",
    "TERRANBUILD_FUSIONCORE",
    "TERRANBUILD_ARMORY",
    "BUILD_TECHLAB_FACTORY",
    "BUILD_TECHLAB_STARPORT",
    "BUILD_TECHLAB_BARRACKS",
    "FACTORYTRAIN_SIEGETANK",
    "TRAIN_CYCLONE",
    "STARPORTTRAIN_BANSHEE",
    "STARPORTTRAIN_BATTLECRUISER",
    "STARPORTTRAIN_RAVEN",
    "STARPORTTRAIN_LIBERATOR",
    "STARPORTTRAIN_VIKINGFIGHTER",
    "BARRACKSTECHLABRESEARCH_STIMPACK",
    "RESEARCH_COMBATSHIELD",
    "RESEARCH_CONCUSSIVESHELLS",
    "RESEARCH_CYCLONELOCKONDAMAGE",
    "RESEARCH_INFERNALPREIGNITER",
    "RESEARCH_TERRANINFANTRYARMOR",
    "RESEARCH_TERRANINFANTRYWEAPONS",
    "RESEARCH_TERRANVEHICLEWEAPONS",
}


def infer_soft_situation_cues(step_actions: List[Dict[str, Any]]) -> List[str]:
    """Infer v8 macro-control cues from the current step actions only.

    These cues are intentionally fuzzy: they do not contain observation values,
    but they tell the label model which obs fields a downstream reasoning model
    should use when executing the step.
    """
    actions = [str(a["action"]) for a in step_actions]
    action_set = set(actions)
    train_count = sum(1 for action in actions if action.startswith(_TRAIN_PREFIXES))
    cues: "OrderedDict[str, str]" = OrderedDict()

    def add(key: str, text: str) -> None:
        if key not in cues:
            cues[key] = text

    if (
        "COMMANDCENTERTRAIN_SCV" in action_set
        or "TERRANBUILD_COMMANDCENTER" in action_set
        or "TERRANBUILD_REFINERY" in action_set
        or "UPGRADETOORBITAL_ORBITALCOMMAND" in action_set
    ):
        add(
            "worker_saturation",
            "Worker saturation: phrase SCV production as something to pace from worker current/ideal together with this step's economy task, such as a new base, orbital, or refinery.",
        )

    if (
        "TERRANBUILD_SUPPLYDEPOT" in action_set
        or train_count >= 4
        or bool(action_set & _PRODUCTION_STRUCTURE_ACTIONS)
    ):
        add(
            "supply_buffer",
            "Supply buffer: phrase depot planning as depending on current supply headroom plus this step's SCV, Marine, or production-structure demand, with modest buffer but not excessive unused cap.",
        )

    if "TERRANBUILD_REFINERY" in action_set:
        add(
            "gas_capacity",
            "Gas capacity: describe the Refinery as strengthening gas income or gas flexibility for upcoming tech, without binding it to one exact unit, upgrade, or building target.",
        )
    elif action_set & _GAS_HEAVY_ACTIONS:
        add(
            "gas_capacity",
            "Gas capacity: mention minerals and gas as macro capacity for gas-heavy tech, units, or upgrades, without forcing a one-to-one Refinery target.",
        )

    if not cues:
        add(
            "macro_pacing",
            "Macro pacing: keep this step grounded in worker saturation, supply headroom, and minerals/gas while staying faithful to the listed actions.",
        )

    return list(cues.values())


def format_soft_situation_cues(cues: List[str]) -> str:
    return "\n".join(f"- {cue}" for cue in cues)


# ---------------------------------------------------------------------------
# Single trajectory processor (v8: macro-control normal steps + v7 summary/final)
# ---------------------------------------------------------------------------
def process_trajectory(
    bot_folder: str,
    seq_path: str,
    meta: Dict[str, Any],
    order_list: List[str],
    mapper: ActionMapper,
    bo_docs_dir: str,
    model_key: str,
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

    # v8: NO cross-step context and NO concrete obs values. Each step receives
    # action-derived soft cues that teach the downstream model what to inspect.
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
        soft_cues = infer_soft_situation_cues(step_actions)
        soft_cues_block = format_soft_situation_cues(soft_cues)

        user_prompt = STEP_USER_PROMPT_TEMPLATE.format(
            actions_block=actions_block,
            soft_cues_block=soft_cues_block,
            step_num=step_num,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        print(f"  [{bot_folder}] Step {step_num}/{total_steps} (actions {start}-{end}, {action_count} actions) -> LLM...")
        llm_output = _call_llm(messages, model_key=model_key)

        if not llm_output:
            print(f"  [{bot_folder}] Step {step_num} LLM call FAILED (empty response)")
            step_outputs.append(f"[Step {step_num}] *(LLM call failed)*")
            step_index_entries.append({
                "step": step_num,
                "range": [start, end],
                "action_count": action_count,
                "llm_call_done": False,
                "soft_situation_cues_v8": soft_cues,
            })
        else:
            llm_output = _normalize_model_text(llm_output)
            llm_output = _sanitize_v8_normal_step_text(llm_output)
            step_outputs.append(llm_output)
            step_index_entries.append({
                "step": step_num,
                "range": [start, end],
                "action_count": action_count,
                "llm_call_done": True,
                "soft_situation_cues_v8": soft_cues,
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
    summary = _call_llm(summary_messages, model_key=model_key)
    if not summary:
        summary = f"{bot_name} build order - {total_action_count} actions, {total_steps} steps."
    summary = _normalize_model_text(summary)

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
    final_step_output = _call_llm(final_step_messages, model_key=model_key)
    if not final_step_output:
        final_step_output = f"[Step {final_step_num}] Use this gameplan as your strategic baseline -- adapt your decisions based on what you scout and how the game unfolds."
    final_step_output = _normalize_model_text(final_step_output)

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
    if os.path.exists(p):
        return p

    marker = "bo_collection_runs/"
    if marker in linux_path:
        rel = linux_path.split(marker, 1)[1].replace("/", "\\")
        local_p = os.path.join(_REPO_ROOT, rel)
        if os.path.exists(local_p):
            return local_p

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

    parser = argparse.ArgumentParser(description="BO Action Sequence -> Natural Language Docs (v8 Situation-Aware Fuzzy)")
    parser.add_argument("--bot", type=str, default=None,
                        help="Process only this bot folder (e.g. banshees)")
    parser.add_argument("--max-workers", type=int, default=5,
                        help="Max concurrent trajectories (default 5)")
    parser.add_argument("--data-dir", type=str,
                        default=r"D:\SC2\bo_collection_runs\2026-06-16_terran_bo_commitfix_v5",
                        help="Path to the data directory with BO folders")
    parser.add_argument("--output-dir", type=str,
                        default=None,
                        help="Output directory for bo_docs (default: Tools/bo_docs_situation_aware)")
    parser.add_argument("--model-key", type=str,
                        default="deepseek-v4-flash",
                        help="Model key from API_config/config.json (default: deepseek-v4-flash)")
    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = args.output_dir or os.path.join(_TOOLS_DIR, "bo_docs_situation_aware")
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

    print(f"\nProcessing {len(trajectories)} trajectories with {args.max_workers} workers using model_key={args.model_key}...\n")

    mapper = ActionMapper()

    step_index_data: Dict[str, Any] = {}
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_bot = {}
        for bot_folder, seq_path, meta, order_list in trajectories:
            future = executor.submit(
                process_trajectory,
                bot_folder, seq_path, meta, order_list, mapper, output_dir, args.model_key,
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
