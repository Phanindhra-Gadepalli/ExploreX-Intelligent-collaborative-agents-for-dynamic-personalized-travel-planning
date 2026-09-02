import os
import re
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
import json
from typing import Generator, Optional
from pydantic import BaseModel, Field

class TravelState(BaseModel):
    name: Optional[str] = Field(default=None, description="User's name")
    origin_city: Optional[str] = Field(default=None, description="The city the user is starting their journey from")
    city: Optional[str] = Field(default=None, description="Destination city, state, or country anywhere in the world (e.g., 'Goa', 'Kerala', 'Paris', 'Tokyo', 'Dubai', 'Singapore', 'Rajasthan')")
    days: Optional[str] = Field(default=None, description="Number of days for the trip (e.g. '4', '5')")
    budget: Optional[str] = Field(
        default=None,
        description="Budget category: 'low', 'medium', or 'high'. Set this ONLY when the user says low/medium/high/budget/luxury. "
                    "If the user states an INR amount (e.g. ₹50,000 or Rs 80000), leave this None and fill budget_amount instead."
    )
    budget_amount: Optional[int] = Field(
        default=None,
        description="Explicit numerical budget in Indian Rupees (INR). Extract this when the user states a specific amount "
                    "such as '₹50,000', 'Rs 80000', '1 lakh', '50 thousand', 'budget is 60000', etc. "
                    "Convert lakhs: 1 lakh = 100000. Store the plain integer with no symbols."
    )
    budget_strictness: Optional[str] = Field(
        default=None,
        description="Budget flexibility intent. Set to 'strict' when the user uses words like: "
                    "'must not exceed', 'strictly', 'do not exceed', 'keep below', 'maximum', "
                    "'must be within', 'should not exceed', 'less than', 'no more than'. "
                    "Set to 'flexible' when the user says: 'around', 'approximately', 'about', "
                    "'roughly', 'up to', 'nearly'. Leave None if not mentioned."
    )
    people: Optional[str] = Field(default=None, description="Number of people traveling (e.g. '2', '5')")
    kids: Optional[str] = Field(default=None, description="Are kids traveling? (yes or no)")
    health: Optional[str] = Field(default=None, description="Health status (e.g., 'good', 'limited')")
    hobbies: Optional[str] = Field(default=None, description="Hobbies and interests (e.g., 'history, nature')")
    start_date: Optional[str] = Field(default=None, description="Start date of the trip (YYYY-MM-DD or 'flexible'). If the user says 'not decided', 'flexible', 'no fixed date', 'haven't decided', or 'any date', you MUST output 'flexible'.")

    @staticmethod
    def _json_schema_extra(schema: dict):
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)

    model_config = {
        "json_schema_extra": _json_schema_extra
    }

class ChatAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize the ChatAgent with specified model."""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7, streaming=True)
        # Use a non-streaming model for structured output
        self.extractor_model = ChatGoogleGenerativeAI(model=model_name, temperature=0)
        self.structured_extractor = self.extractor_model.with_structured_output(TravelState)
        
        # Define required fields (these must be filled)
        # budget OR budget_amount must be present — checked by _has_budget()
        self.required_fields = ["name", "origin_city", "city", "days", "people", "kids", "health", "hobbies", "start_date", "budget"]
        # Define all fields, including optional ones
        self.all_fields = self.required_fields + ["budget_amount", "budget_strictness", "specificRequirements"]
        self.conversation_history = []

    def _extract_text(self, content) -> str:
        """Extract plain text from Gemini response content (str or list of dicts)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and 'text' in part:
                    parts.append(part['text'])
                elif isinstance(part, str):
                    parts.append(part)
            return ''.join(parts)
        return str(content) if content else ''

    def _has_budget(self, state: dict) -> bool:
        """Return True if the user has provided either a budget category or a numerical budget amount."""
        return bool(state.get("budget") or state.get("budget_amount"))

    def _init_system_message(self):
        """Initialize system message for the conversation."""
        return SystemMessage(content="""
        You are a helpful global travel assistant named ExploreX. Your job is to collect information about the user's travel plans for ANY destination worldwide.
        Be friendly, conversational, and help the user plan their trip — whether within India or to any international destination.

        INDIA SPECIALIZATION: ExploreX has a deep, rich knowledge base for India and can provide highly detailed, 
        personalized itineraries for any Indian destination. India-specific destinations you know well include:
        Delhi, Mumbai, Jaipur, Goa, Agra, Varanasi, Kerala (Kochi/Alleppey/Munnar), Amritsar, Udaipur, Mysore,
        Hampi, Rishikesh, Darjeeling, Manali, Shimla, Kolkata, Chennai, Hyderabad, Pune, Ahmedabad, Jodhpur,
        Pushkar, Pondicherry, Coorg, Ooty, Munnar, Leh-Ladakh, Kaziranga, Ranthambore, and all Indian states.

        INTERNATIONAL DESTINATIONS: ExploreX also supports international travel planning for destinations like
        Paris, London, New York, Tokyo, Dubai, Singapore, Bali, Rome, Barcelona, Sydney, Bangkok, and any other
        world destination. For international destinations, ExploreX uses live web information to provide accurate
        recommendations.

        Collect all necessary information about their trip, including:
        - Origin city (where they are traveling from)
        - Destination (any city, state, or country in the world)
        - Number of days
        - Number of people
        - Budget (low/medium/high OR a specific amount in their local currency)
        - Kids traveling (yes/no)
        - Health status
        - Hobbies and interests
        - Travel start date

        For budget, accept EITHER a category (low/medium/high) OR a specific amount (e.g. ₹50,000, $2000, €1500).
        Also pay attention to any specific requirements the traveler mentions, such as accessibility needs,
        food restrictions (vegetarian, halal, kosher, etc.), special interests, or any constraints.

        CRITICAL: Never assume, invent, or infer missing information (such as origin city, budget, start date, number of travelers).
        You MUST explicitly ask the user for any missing mandatory parameters and wait for their response before proceeding.
        """)


    def collect_info(self, user_input: str, state: dict = None) -> dict:
        """Check for missing information and ask user questions to complete the required information."""
        if state is None:
            state = {}

        # Initialize conversation if it's empty
        if not self.conversation_history:
            self.conversation_history.append(self._init_system_message())

        # Extract structured data using LLM
        if user_input and user_input.strip():
            # Budget is special: either budget (category) OR budget_amount (numeric) satisfies the requirement
            missing_budget = not self._has_budget(state)
            missing_current = [f for f in self.required_fields if not state.get(f)]
            # Always try to extract budget fields if budget is not yet fully resolved
            extra_extract = []
            if missing_budget:
                for f in ["budget", "budget_amount", "budget_strictness"]:
                    if f not in missing_current:
                        extra_extract.append(f)
            fields_to_extract = missing_current + extra_extract
            
            if fields_to_extract:
                # Formulate extraction prompt with context of missing fields and their descriptions
                field_descriptions = []
                for field_name, field_info in TravelState.model_json_schema().get("properties", {}).items():
                    if field_name in fields_to_extract:
                        field_descriptions.append(f"- {field_name}: {field_info.get('description', '')}")
                
                desc_str = "\n".join(field_descriptions)
                
                extraction_prompt = (
                    f"Carefully extract the following travel details from the user's input: {', '.join(fields_to_extract)}.\n\n"
                    f"Definitions:\n{desc_str}\n\n"
                    f"IMPORTANT BUDGET RULES:\n"
                    f"- If user says 'low/medium/high/budget/luxury' → fill 'budget' field only.\n"
                    f"- If user states a specific INR number (e.g. ₹50000, 50 thousand, 1 lakh) → fill 'budget_amount' with the integer, leave 'budget' empty.\n"
                    f"- 1 lakh = 100000, 50 thousand = 50000.\n"
                    f"- Detect strictness from phrases: 'strictly/must not exceed/keep below/no more than' → 'strict'; 'around/approximately/up to/roughly' → 'flexible'.\n\n"
                    f"CRITICAL: Extract ONLY information explicitly stated by the user. Do not infer, guess, or use default values for ANY missing fields.\n\n"
                    f"Do NOT confuse health status with destination, or budget amount with number of people.\n\n"
                    f"User Input: '{user_input}'"
                )
                
                try:
                    import concurrent.futures
                    print(f"[DEBUG] Invoking structured_extractor...")
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    future = executor.submit(self.structured_extractor.invoke, extraction_prompt)
                    try:
                        extracted_data = future.result(timeout=15)  # 15-second hard timeout
                    except concurrent.futures.TimeoutError:
                        print(f"[WARN] Structured extraction timed out after 15s — skipping extraction")
                        extracted_data = None
                    finally:
                        # CRITICAL: shutdown(wait=False) so we do NOT block waiting for the
                        # still-running background thread. Without this, the 'with' block's
                        # __exit__ calls shutdown(wait=True) which hangs until Gemini responds.
                        executor.shutdown(wait=False)
                    if extracted_data is not None:
                        print(f"[DEBUG] structured_extractor returned: {type(extracted_data)}")
                        extracted_dict = extracted_data.model_dump(exclude_none=True)
                        print(f"[DEBUG] extracted_dict: {extracted_dict}")
                        for field, value in extracted_dict.items():
                            # Only update fields that are genuinely missing or budget extras
                            if value and (field in missing_current or field in extra_extract):
                                state[field] = value
                                print(f"Updated state: {field} = {value}")
                                
                        # Normalize budget if budget_amount is present but budget is not
                        if state.get("budget_amount") and not state.get("budget"):
                            try:
                                amt = int(state["budget_amount"])
                                if amt <= 30000:
                                    state["budget"] = "low"
                                elif amt <= 100000:
                                    state["budget"] = "medium"
                                else:
                                    state["budget"] = "high"
                                print(f"Normalized budget category to '{state['budget']}' based on amount {amt}")
                            except (ValueError, TypeError):
                                pass
                except Exception as e:
                    import traceback
                    print(f"[ERROR] Structured extraction failed: {e}")
                    traceback.print_exc()


        # Add user input to conversation if not empty
        if user_input and user_input.strip():
            self.conversation_history.append(HumanMessage(content=user_input))

        # Get AI response based on current state and conversation history
        messages = self.conversation_history.copy()
        missing = [f for f in self.required_fields if not state.get(f)]
        # Budget is satisfied if either category OR numeric amount is provided
        if self._has_budget(state) and "budget" in missing:
            missing = [f for f in missing if f != "budget"]
        complete = len(missing) == 0
        messages.append(HumanMessage(content=f'''
        Current state: {json.dumps(state, ensure_ascii=False)}
        Required fields: {json.dumps(self.required_fields, ensure_ascii=False)}
        Missing fields: {json.dumps(missing, ensure_ascii=False)}
        
        IMPORTANT RULES:
        1. This assistant ONLY plans trips within India. If the city provided is NOT in India, 
           ask the user to choose an Indian city instead.
        2. Strictly ask ONLY about the fields explicitly listed in `Missing fields`. Do not ask about food preferences, accommodation, or transportation unless they are missing.
        3. Remember to acknowledge information that has already been provided.
        4. Tell the user they can write "not decided" for the start date if flexible.
        5. For budget, accept EITHER a category (low/medium/high) OR a specific INR amount (e.g. ₹50,000). Both are valid.
        6. Keep your response concise and friendly (1-2 sentences max).
        '''))

        print(f"[DEBUG] Calling LLM (model.stream)...")
        try:
            response = self.model.stream(messages)
            print(f"[DEBUG] LLM stream object created (lazy, not yet consumed): {type(response)}")
            return {
                "stream": response,
                "missing_fields": missing,
                "complete": complete,
                "state": state.copy()
            }
        except Exception as e:
            import traceback
            print(f"[ERROR] model.stream() raised an exception: {e}")
            traceback.print_exc()
            return {
                "stream": None,
                "missing_fields": missing,
                "complete": False,
                "state": state.copy(),
                "error": str(e)
            }
        print(f"[DEBUG] LLM returned (stream)")  # NOTE: won't be reached in normal flow

    def interact_with_user(self, message: str, state: dict = None) -> Generator:
        """Process user message and generate a streaming response."""
        if state is None:
            state = {}

        # Add user message to conversation
        self.conversation_history.append(HumanMessage(content=message))

        # Generate streaming response based on the conversation history
        try:
            return self.model.stream(self.conversation_history)
        except Exception as e:
            print(f"Error in interact_with_user: {e}")
            return None