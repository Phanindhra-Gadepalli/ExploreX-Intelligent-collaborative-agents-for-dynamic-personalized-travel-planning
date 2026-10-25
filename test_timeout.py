import os
from dotenv import load_dotenv
load_dotenv()
from agents.chat_agent import ChatAgent

def main():
    agent = ChatAgent()
    state = {}
    
    # First invocation
    print("--- First Invocation ---")
    res1 = agent.collect_info("plan a 9 days trip to varanasi and i like temples and ghats", state)
    for chunk in res1["stream"]: pass
    print("State after 1:", state)
    
    print("\n--- Second Invocation ---")
    res2 = agent.collect_info("origin city is hyderabad with 4 people and no kids and i am in good health and start date is 11 october 2026 and i have strictly 50000 budget", state)
    if res2["stream"]:
        for chunk in res2["stream"]: pass
    print("State after 2:", state)

    print("\n--- Third Invocation ---")
    res3 = agent.collect_info("go ahead", state)
    if res3["stream"]:
        for chunk in res3["stream"]: pass
    print("State after 3:", state)
    print("Missing fields:", res3["missing_fields"])

if __name__ == "__main__":
    main()
