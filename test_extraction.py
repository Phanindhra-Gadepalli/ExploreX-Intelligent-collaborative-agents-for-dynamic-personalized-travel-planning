import os
from dotenv import load_dotenv
load_dotenv()
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import Optional

class TravelState(BaseModel):
    name: Optional[str] = Field(description="User's name")
    origin_city: Optional[str] = Field(description="The city the user is starting their journey from")
    city: Optional[str] = Field(description="Destination city or cities in India (comma separated if multiple). DO NOT use placeholder words like 'City'.")
    days: Optional[str] = Field(description="Number of days for the trip (e.g. '4', '5')")
    budget: Optional[str] = Field(description="Budget level (low, medium, or high)")
    people: Optional[str] = Field(description="Number of people traveling (e.g. '2', '5')")
    kids: Optional[str] = Field(description="Are kids traveling? (yes or no)")
    health: Optional[str] = Field(description="Health status (e.g., 'good', 'limited')")
    hobbies: Optional[str] = Field(description="Hobbies and interests (e.g., 'history, nature')")
    start_date: Optional[str] = Field(description="Start date of the trip (YYYY-MM-DD or 'flexible')")

extractor_model = ChatGoogleGenerativeAI(model="gemini-flash-lite-latest", temperature=0)
structured_extractor = extractor_model.with_structured_output(TravelState)

user_inputs = [
    "Delhi",
    "Mumbai",
    "Thanjavur",
    "Thanjavur and Madurai for 4 days with medium budget and 5 people, no kids, I am in good health, I enjoy history, old temples and palaces, start date is 2026-10-26."
]

missing_current = ["city", "days", "budget", "people", "kids", "health", "hobbies", "start_date"]

for user_input in user_inputs:
    print(f"\n--- Testing input: {user_input} ---")
    extraction_prompt = f"Carefully extract the following missing travel details from the user's input: {', '.join(missing_current)}. Pay close attention to field definitions. For example, 'city' is the destination city, and 'health' is the user's health status. Do not confuse them.\n\nUser Input: '{user_input}'"
    try:
        extracted_data = structured_extractor.invoke(extraction_prompt)
        extracted_dict = extracted_data.model_dump(exclude_none=True)
        for field, value in extracted_dict.items():
            if value and field in missing_current:
                print(f"Extracted: {field} = {value}")
    except Exception as e:
        print(f"Error: {e}")
