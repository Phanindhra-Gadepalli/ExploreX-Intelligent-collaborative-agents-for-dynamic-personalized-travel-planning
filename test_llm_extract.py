import os
import json
from dotenv import load_dotenv
load_dotenv()
from langchain_google_genai import ChatGoogleGenerativeAI

llm = ChatGoogleGenerativeAI(model='gemini-1.5-flash', temperature=0)

message = 'Hi, my name is Rajesh, I want to visit Darjeelingfor 5 days, medium budget, 4 people, 2adults and 2 kids, 2026-10-15, I enjoy adventures and sight seeing, I am in good health,origin city is hyderabad'
missing = ['name', 'origin_city', 'city', 'days', 'budget', 'people', 'kids', 'health', 'hobbies', 'start_date']

prompt = f"""
Extract the following missing fields from the user's message.
Missing fields to extract: {', '.join(missing)}

User message: "{message}"

Respond ONLY with a valid JSON object containing the extracted fields. If a field is not mentioned, omit it.
For budget, use: low, medium, high.
For kids, use: yes, no.
"""

try:
    res = llm.invoke(prompt)
    content = res.content
    if '```json' in content:
        content = content.split('```json')[1].split('```')[0]
    elif '```' in content:
        content = content.split('```')[1].split('```')[0]
    extracted = json.loads(content.strip())
    print(extracted)
except Exception as e:
    print('Failed', e)
