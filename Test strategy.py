from agents.strategy_agent import StrategyAgent
from datetime import datetime, timedelta
import json

def test_strategy():
    # 初始化InformationAgent
    strategy_agent = StrategyAgent()
    
    with open("input of strategy.txt", "r") as f:
        data = json.load(f)
        selected_spots = data["selected_spots"]
        total_days = data["total_days"]
        all_attractions = data["all_attractions"]

    
    results = strategy_agent.plan_remaining_time(
            selected_spots=selected_spots,
            total_days=total_days,
            all_attractions=all_attractions,    

        )
    return results    
        

if __name__ == "__main__":
    test_strategy() 