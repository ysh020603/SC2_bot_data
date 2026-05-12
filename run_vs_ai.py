import os
import sys

# 确保能正确导入 python-sc2 和 sharpy 相关模块
sys.path.insert(1, "python-sc2")

from bot_loader import GameStarter, BotDefinitions
from version import update_version_txt

def main():
    update_version_txt()
    
    # ==========================================
    # 1. 你的 Bot 配置
    # ==========================================
    # 填写 dummies 目录下 bot 的注册名称 (例如: "lings", "adept_allin", "marine_rush" 等)
    my_bot_name = "llm_bot"
    
    # ==========================================
    # 2. 游戏与地图配置
    # ==========================================
    # 地图必须存在于你的 StarCraft II/Maps 目录下
    map_name = "KairosJunctionLE" 
    
    # 是否开启实时模式 (True = 正常人眼观看速度，False = 解除帧数限制，最快速度进行演算)
    real_time = False 

    # ==========================================
    # 3. 内置 AI (对手) 配置
    # ==========================================
    # 种族可选: terran, zerg, protoss, random
    enemy_race = "terran" 
    
    # 难度可选: veryeasy, easy, medium, mediumhard, hard, harder, veryhard, cheatvision, cheatmoney, cheatinsane
    enemy_difficulty = "hard" 
    
    # 风格可选: randombuild, rush, timing, power, macro, air
    enemy_build = "macro"

    # ==========================================
    # 启动逻辑 (借用 sharpy 原生架构)
    # ==========================================
    # sharpy 框架解析 ai 的格式为 "ai.种族.难度.风格"
    p2_string = f"ai.{enemy_race}.{enemy_difficulty}.{enemy_build}"

    # 使用 sys.argv 模拟命令行参数，从而复用 GameStarter 极其完善的日志和初始化流程
    args = ["run_custom.py", "-m", map_name, "-p1", my_bot_name, "-p2", p2_string]
    if real_time:
        args.append("-rt")
    sys.argv = args

    print(f"==================================================")
    print(f" 正在启动 SC2 对战...")
    print(f" 你的 Bot: {my_bot_name}")
    print(f" 对手 AI : {enemy_race.upper()} | 难度: {enemy_difficulty} | 风格: {enemy_build}")
    print(f" 比赛地图: {map_name}")
    print(f"==================================================")

    root_dir = os.path.dirname(os.path.abspath(__file__))
    ladder_bots_path = os.path.join(root_dir, "Bots")
    definitions: BotDefinitions = BotDefinitions(ladder_bots_path)
    
    starter = GameStarter(definitions)
    starter.play()

if __name__ == "__main__":
    main()