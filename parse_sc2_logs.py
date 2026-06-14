import json
import re
from pathlib import Path
from collections import defaultdict

# 匹配日志中胜负结果的正则（Victory / Defeat / Tie）
RESULT_PATTERN = re.compile(
    r"Result for player 1 - Bot .+?:\s*(Victory|Defeat|Tie)\s*$",
    re.IGNORECASE,
)
OPPONENT_ID_PATTERN = re.compile(
    r"OpponentId:\s+(\S+)",
    re.IGNORECASE,
)
# [Start] Opponent race: Protoss
OPPONENT_RACE_PATTERN = re.compile(
    r"\[Start\]\s*Opponent race:\s*(\w+)",
    re.IGNORECASE,
)
# 仅匹配子目录名: ..._harder_air_KairosJunctionLE_...
PATH_STYLE_PATTERN = re.compile(
    r"_(?:hard|harder|medium|easy|veryhard)_([a-z]+)_",
)
# 子目录名: ..._terran_vs_protoss_harder_air_...，捕获对手种族
PATH_RACE_PATTERN = re.compile(
    r"_vs_([a-z]+)_(?:hard|harder|medium|easy|veryhard)_",
    re.IGNORECASE,
)
# Computer Harder(Terran, Air) -> group(1)=种族, group(2)=风格
COMPUTER_STYLE_PATTERN = re.compile(
    r"Computer\s+(?:Hard|Harder|Medium|Easy|VeryHard)\(\s*(\w+)\s*,\s*(\w+)\)",
    re.IGNORECASE,
)
# >>> TOP AGENT: t=0 BYPASSED by --force_strategy='marine_rush'
FORCE_STRATEGY_PATTERN = re.compile(
    r"--force_strategy=['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "game_records" / "strategy_statistics.json"


def empty_stats():
    return {
        "total_logs": 0,
        "victories": 0,
        "defeats": 0,
        "ties": 0,
        "no_result_found": 0,
    }


def compute_rates(stats):
    total = stats["total_logs"]
    victories = stats["victories"]
    defeats = stats["defeats"]
    ties = stats["ties"]
    decided = victories + defeats
    return {
        **stats,
        "win_rate": f"{(victories / total * 100):.2f}%" if total > 0 else "0%",
        "tie_rate": f"{(ties / total * 100):.2f}%" if total > 0 else "0%",
        "win_rate_excluding_ties": (
            f"{(victories / decided * 100):.2f}%" if decided > 0 else "0%"
        ),
    }


def build_nested_report(strategy_race_style_stats):
    """strategy -> opponent_race -> opponent_style -> raw stats"""
    report = {}
    for strategy in sorted(strategy_race_style_stats):
        race_map = strategy_race_style_stats[strategy]
        strategy_total = empty_stats()
        by_opponent_race = {}

        for race in sorted(race_map):
            style_map = race_map[race]
            race_total = empty_stats()
            by_opponent_style = {}

            for style in sorted(style_map):
                stats = style_map[style]
                by_opponent_style[style] = compute_rates(stats)
                for key in race_total:
                    race_total[key] += stats[key]
                    strategy_total[key] += stats[key]

            by_opponent_race[race] = {
                "statistics": compute_rates(race_total),
                "by_opponent_style": by_opponent_style,
            }

        report[strategy] = {
            "statistics": compute_rates(strategy_total),
            "by_opponent_race": by_opponent_race,
        }
    return report


def is_game_log(log_path: Path) -> bool:
    """排除 worker / batch 调度日志，只统计对局目录下的 log。"""
    if "_batch_logs" in log_path.parts:
        return False
    if log_path.name.startswith("worker_"):
        return False
    return True


def collect_batch_dirs(paths):
    """路径可以是 batch 目录，也可以是 game_records 根目录。"""
    batch_dirs = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            print(f"跳过: 找不到路径 {raw}")
            continue
        if path.name.startswith("batch_") and path.is_dir():
            batch_dirs.append(path)
        elif path.is_dir():
            batch_dirs.extend(sorted(p for p in path.glob("batch_*") if p.is_dir()))
        else:
            print(f"跳过: 非目录 {raw}")
    return batch_dirs


def collect_game_logs(batch_dirs):
    seen = set()
    for batch_dir in batch_dirs:
        for log_file in batch_dir.rglob("*.log"):
            if not is_game_log(log_file):
                continue
            resolved = log_file.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield log_file


def parse_log_file(log_path):
    """
    单次读取日志，返回 (strategy, opponent_race, opponent_style, result)。
    strategy 来自 --force_strategy；result 取最后一次对局结果。
    """
    path = Path(log_path)
    parent_match = PATH_STYLE_PATTERN.search(path.parent.name)
    path_style = parent_match.group(1).lower() if parent_match else None
    parent_race_match = PATH_RACE_PATTERN.search(path.parent.name)
    path_race = parent_race_match.group(1).lower() if parent_race_match else None

    strategy = None
    log_style = None
    log_race = None
    computer_style = None
    computer_race = None
    start_race = None
    last_result = None

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if strategy is None:
                    strat_match = FORCE_STRATEGY_PATTERN.search(line)
                    if strat_match:
                        strategy = strat_match.group(1).lower()

                if log_style is None:
                    id_match = OPPONENT_ID_PATTERN.search(line)
                    if id_match:
                        parts = id_match.group(1).split(".")
                        log_style = parts[-1].lower()
                        # 形如 universal_llm.terran-ai.protoss.harder.air
                        if len(parts) >= 3:
                            log_race = parts[-3].lower()

                if start_race is None:
                    race_match = OPPONENT_RACE_PATTERN.search(line)
                    if race_match:
                        start_race = race_match.group(1).lower()

                if computer_style is None:
                    comp_match = COMPUTER_STYLE_PATTERN.search(line)
                    if comp_match:
                        computer_race = comp_match.group(1).lower()
                        computer_style = comp_match.group(2).lower()

                result_match = RESULT_PATTERN.search(line)
                if result_match:
                    last_result = result_match.group(1).capitalize()
    except OSError as e:
        print(f"  读取 {log_path.name} 出错: {e}")
        return "unknown", "unknown", "unknown", None

    opponent_style = log_style or computer_style or path_style or "unknown"
    opponent_race = start_race or log_race or computer_race or path_race or "unknown"
    return strategy or "unknown", opponent_race, opponent_style, last_result


def record_result(stats, result):
    stats["total_logs"] += 1
    if result == "Victory":
        stats["victories"] += 1
    elif result == "Defeat":
        stats["defeats"] += 1
    elif result == "Tie":
        stats["ties"] += 1
    else:
        stats["no_result_found"] += 1


def aggregate_by_strategy(path_list, output_path=None):
    """
    跨多个 batch 目录汇总：按己方策略分组，组内再按对手风格统计。
    """
    batch_dirs = collect_batch_dirs(path_list)
    if not batch_dirs:
        print("未找到任何 batch 目录。")
        return None

    print(f"扫描 {len(batch_dirs)} 个 batch 目录...\n")

    overall_stats = empty_stats()
    # strategy -> opponent_race -> opponent_style -> stats
    strategy_race_style_stats = defaultdict(
        lambda: defaultdict(lambda: defaultdict(empty_stats))
    )
    source_batches = sorted({d.name for d in batch_dirs})

    log_count = 0
    for log_file in collect_game_logs(batch_dirs):
        log_count += 1
        try:
            strategy, opponent_race, opponent_style, result = parse_log_file(log_file)
            record_result(overall_stats, result)
            record_result(
                strategy_race_style_stats[strategy][opponent_race][opponent_style],
                result,
            )
        except Exception as e:
            print(f"  处理 {log_file} 出错: {e}")

    by_strategy = build_nested_report(strategy_race_style_stats)

    result_data = {
        "source_batches": source_batches,
        "batch_count": len(batch_dirs),
        "log_count": log_count,
        "statistics": compute_rates(overall_stats),
        "by_strategy": by_strategy,
    }

    out = Path(output_path) if output_path else DEFAULT_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=4, ensure_ascii=False)

    s = overall_stats
    print(
        f"汇总完成: {log_count} 场对局, 胜 {s['victories']}, 负 {s['defeats']}, "
        f"平 {s['ties']}, 无结果 {s['no_result_found']}"
    )
    for strategy, block in by_strategy.items():
        st = block["statistics"]
        print(
            f"\n  策略 [{strategy}] 总计 {st['total_logs']} 场, "
            f"胜 {st['victories']}, 负 {st['defeats']}, 平 {st['ties']}, "
            f"胜率(不含平) {st['win_rate_excluding_ties']}"
        )
        for race, race_block in block["by_opponent_race"].items():
            rst = race_block["statistics"]
            print(
                f"    [对手种族 {race:8s}] {rst['victories']}胜/"
                f"{rst['total_logs']}场  "
                f"负{rst['defeats']} 平{rst['ties']}  "
                f"胜率(不含平) {rst['win_rate_excluding_ties']}"
            )
            for style, style_st in race_block["by_opponent_style"].items():
                print(
                    f"        vs {style:8s}  {style_st['victories']}胜/"
                    f"{style_st['total_logs']}场  "
                    f"负{style_st['defeats']} 平{style_st['ties']}  "
                    f"胜率(不含平) {style_st['win_rate_excluding_ties']}"
                )

    print(f"\nJSON 已保存至: {out}\n")
    return result_data


if __name__ == "__main__":
    # 可填 batch 目录，或 game_records 根目录（自动扫描其下所有 batch_*）
    my_paths = [
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_1740_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_1823_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_1854_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_2142_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_2143_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_2144_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_2207_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260526_2234_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_0956_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_0957_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_0958_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_1059_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_1100_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_1655_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_1633_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260527_2251_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260528_1158_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260528_1246_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260528_1247_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        # "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260528_1410_KairosJunctionLE_terranVterran_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2112_KairosJunctionLE_terranVprotoss_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2113_KairosJunctionLE_terranVprotoss_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2113_KairosJunctionLE_terranVzerg_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2114_KairosJunctionLE_terranVzerg_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2115_KairosJunctionLE_terranVzerg_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2116_KairosJunctionLE_terranVprotoss_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2144_KairosJunctionLE_terranVprotoss_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2145_KairosJunctionLE_terranVprotoss_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2146_KairosJunctionLE_terranVzerg_harder_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260603_2149_KairosJunctionLE_terranVzerg_harder_deepseek-v4-flash",
    ]

    aggregate_by_strategy(my_paths)
