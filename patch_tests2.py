import re

with open('test_budget_agent.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Fix total
code = code.replace("r['total']", "r['estimated_total']")
code = code.replace('r["total"]', 'r["estimated_total"]')

# Fix transit mock in TestIntercityTransportCost
def replacer(m):
    try:
        flights = eval(m.group(1))
        trains = eval(m.group(2))
        buses = eval(m.group(3))
        
        arr = []
        if flights:
            arr.extend([{"mode": "flight", **f} for f in flights])
        if trains:
            arr.extend([{"mode": "train", **t} for t in trains])
        if buses:
            arr.extend([{"mode": "bus", **b} for b in buses])
            
        return '"intercity_transport": ' + str(arr)
    except Exception as e:
        return m.group(0)

code = re.sub(r'"flights": (\[.*?\]),\s*"trains": (\[.*?\]),\s*"buses": (\[.*?\])', replacer, code)

with open('test_budget_agent.py', 'w', encoding='utf-8') as f:
    f.write(code)
