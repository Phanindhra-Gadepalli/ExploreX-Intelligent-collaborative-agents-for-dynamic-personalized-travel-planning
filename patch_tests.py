import re

with open('test_budget_agent.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('result["total"]', 'result["estimated_total"]')
code = code.replace('result["intercity_transport"]', 'result["transport"]')
code = code.replace('result["remaining"]', 'result["remaining_budget"]')
code = code.replace('result["fuel_cost"]', 'result["fuel"]')
code = code.replace('result["attractions"]', 'result["activities"]')
code = code.replace('result["budget_level"]', 'result["budget_category"]')
code = code.replace('result["budget_amount"]', 'result["budget_limit"]')
code = code.replace('result["budget_strictness"]', 'result["budget_type"]')
code = code.replace('r["intercity_transport"]', 'r["transport"]')
code = code.replace('r["attractions"]', 'r["activities"]')
code = code.replace('r["fuel_cost"]', 'r["fuel"]')

# Also fix the expected_keys
expected_keys_old = '''        expected_keys = {
            "total", "accommodation", "food", "transport", "intercity_transport",
            "miscellaneous", "attractions", "car_rental", "fuel_cost",
            "budget_level", "budget_amount", "budget_strictness",
            "budget_warning", "budget_infeasible", "remaining",
            "rooms", "days", "people", "breakdown",
        }'''
expected_keys_new = '''        expected_keys = {
            "estimated_total", "accommodation", "food", "transport", "local_transport",
            "miscellaneous", "activities", "car_rental", "fuel",
            "budget_category", "budget_limit", "budget_type",
            "budget_warning", "budget_infeasible", "remaining_budget", "within_budget", "utilization_percentage",
            "rooms", "days", "people", "breakdown",
        }'''
code = code.replace(expected_keys_old, expected_keys_new)

with open('test_budget_agent.py', 'w', encoding='utf-8') as f:
    f.write(code)
