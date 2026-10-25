import os
from dotenv import load_dotenv
load_dotenv()

from agents.information_agent import InformationAgent

def test_validation():
    agent = InformationAgent()
    
    valid_destinations = [
        "Varkala", "Ayodhya", "Hampi", "Gokarna", "Rameswaram", 
        "Mahabalipuram", "Munnar", "Wayanad", "Alleppey", "Kodaikanal", 
        "Coorg", "Chikmagalur", "Auli", "Spiti", "Ziro", "Orchha", 
        "Khajuraho", "Diu", "Daman", "Mount Abu", "Pushkar", 
        "Srisailam", "Tirupati", "Pondicherry", "Shillong", "Gangtok", 
        "Leh", "Srinagar"
    ]
    
    invalid_destinations = [
        "Paris", "London", "Tokyo", "Dubai", "New York"
    ]
    
    print("Testing VALID destinations (all should be True):")
    for dest in valid_destinations:
        result = agent.validate_indian_location(dest)
        print(f"[{'PASS' if result else 'FAIL'}] {dest}: {result}")
        
    print("\nTesting INVALID destinations (all should be False):")
    for dest in invalid_destinations:
        result = agent.validate_indian_location(dest)
        print(f"[{'PASS' if not result else 'FAIL'}] {dest}: {result}")

if __name__ == "__main__":
    test_validation()
