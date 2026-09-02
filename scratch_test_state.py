import asyncio
from agents.chat_agent import ChatAgent

async def test_extraction():
    agent = ChatAgent()
    state = {
      "name": "rajesh",
      "origin_city": "hyderabad",
      "city": "Mumbai",
      "days": "5",
      "people": "4",
      "kids": "no"
    }

    user_input = "i am in good health and i like exploring famous attractions in mumbai and start date is 2026-09-10 and i have strictly 40000 budget"

    print("Initial state:", state)
    result = agent.collect_info(user_input, state)
    print("Result state:", result)

if __name__ == "__main__":
    asyncio.run(test_extraction())
