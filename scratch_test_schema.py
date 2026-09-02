from pydantic import BaseModel, Field
import json

class TravelState(BaseModel):
    name: str | None = Field(default=None, description="User's name")
    
    model_config = {
        "json_schema_extra": lambda s: [s.pop("title", None), [v.pop("title", None) for v in s.get("properties", {}).values()]]
    }

print(json.dumps(TravelState.model_json_schema(), indent=2))
