#!/usr/bin/env python
# Test script to check if all imports and initializations work

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("Testing imports...")

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✓ dotenv loaded")
except Exception as e:
    print(f"✗ dotenv error: {e}")
    sys.exit(1)

try:
    print("Attempting to import TravelGraph...")
    from workflows.travel_graph import TravelGraph
    print("✓ TravelGraph imported")
except Exception as e:
    print(f"✗ TravelGraph import error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("Attempting to initialize TravelGraph...")
    print("  - Creating TravelGraph instance...")
    graph = TravelGraph()
    print("✓ TravelGraph initialized successfully")
except Exception as e:
    print(f"✗ TravelGraph initialization error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\nAll tests passed! The app should work.")
