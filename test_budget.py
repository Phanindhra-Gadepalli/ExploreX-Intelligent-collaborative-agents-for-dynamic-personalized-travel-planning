import sys
import os

# Set up path to import agents
sys.path.append(os.path.abspath('d:/project/adv_ExploreX/ExploreX-Intelligent-collaborative-agents-for-dynamic-personalized-travel-planning'))

from agents.budget_agent import BudgetAgent

agent = BudgetAgent()
spots = [
    {"name": "Burj Khalifa", "price_level": "high"}
]
user_prefs = {
    "budget_amount": 50000,
    "budget_strictness": "strict",
    "people": 10,
    "days": 8,
    "city": "Dubai"
}

transit_options = {
    "intercity_transport": [
        {"mode": "flight", "cost_inr": 25000}
    ]
}

budget = agent.estimate_budget(
    spots=spots,
    user_prefs=user_prefs,
    should_rent_car=False,
    transit_options=transit_options
)
print("Budget:", budget)
