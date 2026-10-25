import os, sys
sys.path.append(r'd:\project\vaiage_main_india')
from agents.information_agent import InformationAgent
agent = InformationAgent()
print(f'Varkala: {agent.validate_indian_location("Varkala")}')
print(f'Ayodhya: {agent.validate_indian_location("Ayodhya")}')
