"""
Test script for the attraction_details enrichment service.
Run with: python test_attraction_details.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.attraction_details import get_attraction_details

PASS = 0
FAIL = 0

def check(cond, msg):
    global PASS, FAIL
    if cond:
        print('[PASS] ' + msg)
        PASS += 1
    else:
        print('[FAIL] ' + msg)
        FAIL += 1

# Taj Mahal
r = get_attraction_details('Taj Mahal', 27.1751, 78.0421)
desc = r.get('description', '')
check(bool(desc), 'Taj Mahal: has description')
check('mausoleum' in desc.lower() or 'mughal' in desc.lower() or 'agra' in desc.lower(), 'Taj Mahal: desc is specific')
check(bool(r.get('significance')), 'Taj Mahal: has significance')
check(bool(r.get('wiki_url')), 'Taj Mahal: has wiki_url')
print('  Description: ' + desc[:150])

# Agra Fort
r = get_attraction_details('Agra Fort', 27.1795, 78.0211)
desc = r.get('description', '')
check(bool(desc), 'Agra Fort: has description')
check('agra' in desc.lower() or 'fort' in desc.lower() or 'mughal' in desc.lower(), 'Agra Fort: desc is specific')
print('  Description: ' + desc[:150])

# Eiffel Tower
r = get_attraction_details('Eiffel Tower', 48.8584, 2.2945)
desc = r.get('description', '')
check(bool(desc), 'Eiffel Tower: has description')
check('paris' in desc.lower() or 'eiffel' in desc.lower(), 'Eiffel Tower: desc is specific')
print('  Description: ' + desc[:150])

# Louvre Museum
r = get_attraction_details('Louvre Museum', 48.8606, 2.3376)
desc = r.get('description', '')
check(bool(desc), 'Louvre Museum: has description')
check('louvre' in desc.lower() or 'paris' in desc.lower() or 'museum' in desc.lower(), 'Louvre Museum: desc is specific')
print('  Description: ' + desc[:150])

# Less-famous: Jaswant Thada
r = get_attraction_details('Jaswant Thada', 26.2974, 73.0219)
desc = r.get('description', '')
check(bool(desc), 'Jaswant Thada: has description')
check('jodhpur' in desc.lower() or 'cenotaph' in desc.lower() or 'rajasthan' in desc.lower(), 'Jaswant Thada: desc is specific')
print('  Description: ' + desc[:150])

# Caching consistency
r1 = get_attraction_details('Eiffel Tower', 48.8584, 2.2945)
r2 = get_attraction_details('Eiffel Tower', 48.8584, 2.2945)
check(r1.get('description') == r2.get('description'), 'Cache consistency')

# Graceful unknown place (no hallucination)
r = get_attraction_details('Totally Unknown Place XYZ999AAA')
no_desc = not r.get('description') or len(r.get('description','')) < 30
check(no_desc, 'Unknown place: no hallucinated description')

# Existing data merging
existing = {'id': 'taj_001', 'name': 'Taj Mahal', 'category': 'heritage', 'rating': 4.8, 'estimated_duration': 3}
r = get_attraction_details('Taj Mahal', 27.1751, 78.0421, existing_data=existing)
check(r.get('id') == 'taj_001', 'Existing id preserved after merge')
check(r.get('rating') == 4.8, 'Existing rating preserved after merge')
check(bool(r.get('description')), 'Merged object has Wikipedia description')

# Generic description replaced
existing_generic = {'id': 'x1', 'name': 'Taj Mahal', 'description': 'A renowned attraction in the area, popular among tourists and locals.'}
r = get_attraction_details('Taj Mahal', 27.1751, 78.0421, existing_data=existing_generic)
check('renowned attraction' not in r.get('description',''), 'Generic description replaced by Wikipedia')

print()
print('Results: ' + str(PASS) + ' passed, ' + str(FAIL) + ' failed')
if FAIL == 0:
    print('ALL TESTS PASSED')
else:
    sys.exit(1)
