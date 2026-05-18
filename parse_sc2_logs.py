import os
import json
import re
from pathlib import Path

def process_single_batch(base_dir):
    """
    处理单个文件夹的逻辑
    """
    base_path = Path(base_dir)
    if not base_path.exists() or not base_path.is_dir():
        print(f"跳过: 找不到目录 {base_dir}")
        return

    print(f"正在处理: {base_path.name} ...")

    # 1. 解析文件夹名称提取信息
    # 规则: batch_日期_时间_地图_对战_难度_模型
    dir_name = base_path.name
    parts = dir_name.split('_')
    
    if len(parts) >= 7 and parts[0] == "batch":
        map_name = parts[3]
        matchup = parts[4]
        difficulty = parts[5]
        model_name = "_".join(parts[6:])
    else:
        map_name = "Unknown"
        matchup = "Unknown"
        difficulty = "Unknown"
        model_name = "Unknown"

    # 2. 统计胜负
    total_logs = 0
    victories = 0
    defeats = 0
    ties = 0
    no_result = 0
    
    # 匹配日志中胜负结果的正则
    result_pattern = re.compile(r"Result for player 1 - Bot UniversalLLMBot.*?:\s*(Victory|Defeat|Tie)", re.IGNORECASE)

    for log_file in base_path.rglob('*.log'):
        total_logs += 1
        found_in_file = False
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    match = result_pattern.search(line)
                    if match:
                        res = match.group(1).capitalize()
                        if res == "Victory": victories += 1
                        elif res == "Defeat": defeats += 1
                        elif res == "Tie": ties += 1
                        found_in_file = True
                        break
            if not found_in_file:
                no_result += 1
        except Exception as e:
            print(f"  读取 {log_file.name} 出错: {e}")

    # 3. 构造数据结构
    result_data = {
        "folder_name": dir_name,
        "game_info": {
            "map": map_name,
            "matchup": matchup,
            "difficulty": difficulty,
            "model": model_name
        },
        "statistics": {
            "total_logs": total_logs,
            "victories": victories,
            "defeats": defeats,
            "ties": ties,
            "no_result_found": no_result,
            "win_rate": f"{(victories/total_logs*100):.2f}%" if total_logs > 0 else "0%"
        }
    }

    # 4. 保存 JSON
    output_path = base_path / "match_statistics.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, indent=4, ensure_ascii=False)
    
    print(f"  完成! 胜: {victories}, 负: {defeats}, 总数: {total_logs}")
    print(f"  JSON 已保存至: {output_path}\n")

def batch_process_logs(path_list):
    """
    接收路径列表并循环处理
    """
    print(f"开始批量处理，共 {len(path_list)} 个任务...\n")
    for path in path_list:
        process_single_batch(path)
    print("所有任务处理完毕。")

if __name__ == "__main__":
    # 在这里定义您的路径列表
    my_paths = [
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1402_KairosJunctionLE_terranvterran_hard_deepseek-v4-pro-reasoning",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1406_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1406_KairosJunctionLE_terranvterran_hard_deepseek-v4-pro",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1410_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash-reasoning",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1411_KairosJunctionLE_terranvterran_hard_qwen3-32b-nothinking",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1412_KairosJunctionLE_terranvterran_hard_qwen3-32b-thinking",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1413_KairosJunctionLE_terranvterran_hard_qwen2_5_14b",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1751_KairosJunctionLE_terranvterran_medium_qwen2_5_14b",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1752_KairosJunctionLE_terranvterran_medium_qwen3-32b-nothinking",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1752_KairosJunctionLE_terranvterran_medium_qwen3-32b-thinking",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1753_KairosJunctionLE_terranvterran_medium_deepseek-v4-flash",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1930_KairosJunctionLE_terranvterran_medium_deepseek-v4-pro-reasoning",
        "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_1958_KairosJunctionLE_terranvterran_hard_deepseek-v4-pro-reasoning"
    ]
    # my_paths = [
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2215_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2244_KairosJunctionLE_terranvzerg_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2245_KairosJunctionLE_terranvprotoss_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260514_1406_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2246_KairosJunctionLE_terranvzerg_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2246_KairosJunctionLE_terranvprotoss_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2306_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2331_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2330_KairosJunctionLE_terranvzerg_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2330_KairosJunctionLE_terranvprotoss_hard_deepseek-v4-flash",
    #     "/data2/SC2_shy/sharpy-sc2/game_records/batch_20260515_2328_KairosJunctionLE_terranvterran_hard_deepseek-v4-flash",
    # ]
    
    batch_process_logs(my_paths)