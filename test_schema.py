import os
from dotenv import load_dotenv
load_dotenv()
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from typing import Optional

class TravelState(BaseModel):
    city: Optional[str] = Field(description="Destination city or cities in India (comma separated if multiple).")

extractor_model = ChatGoogleGenerativeAI(model="gemini-flash-lite-latest", temperature=0)
structured_extractor = extractor_model.with_structured_output(TravelState)

print(hasattr(structured_extractor, 'schema'))
try:
    print(structured_extractor.schema)
except Exception as e:
    print(e)
    
print("Using TravelState.model_json_schema():")
print(TravelState.model_json_schema())
