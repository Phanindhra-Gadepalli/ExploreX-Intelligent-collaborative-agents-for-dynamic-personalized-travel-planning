import asyncio
from workflows.travel_graph import TravelGraph

async def main():
    graph = TravelGraph()
    state = {
        "user_info": {
            "name": "Test User",
            "city": "Kerala",
            "origin_city": "Tirupati",
            "days": 5,
            "people": 10,
            "kids": 0,
            "start_date": "2026-11-22",
            "budget_amount": 80000,
            "budget_strictness": "strict",
            "hobbies": "viewpoints, photography",
        },
        "session_id": "test_budget_strict",
    }
    
    print("--- Running Test ---")
    async for step in graph.run(state):
        print(f"Step: {step['step']}")
        if "budget" in step.get("state", {}):
            b = step["state"]["budget"]
            if b:
                print(f"BUDGET INFO: Limit={b.get('budget_limit')}, Total={b.get('estimated_total')}, Feasible={not b.get('budget_infeasible')}")
        if step["step"] == "complete":
            print(f"Final Response: {step['response']}")
            print(f"Itinerary Length: {len(step.get('itinerary', []))}")

if __name__ == "__main__":
    asyncio.run(main())
