import os
import re
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
import json
from typing import Generator, Optional
from pydantic import BaseModel, ConfigDict, Field

from services.chat_extract import (
    extract_trip_fields,
    has_value,
    merge_missing,
    is_valid_tool_name,
)

class TravelState(BaseModel):
    """The trip details ExploreX collects before it can plan anything."""

    name: Optional[str] = Field(default=None, description="User's name")
    origin_city: Optional[str] = Field(default=None, description="The city the user is starting their journey from")
    city: Optional[str] = Field(default=None, description="Destination city, state, or country anywhere in the world (e.g., 'Goa', 'Kerala', 'Paris', 'Tokyo', 'Dubai', 'Singapore', 'Rajasthan')")
    days: Optional[int] = Field(default=None, description="Number of days for the trip (e.g. 4, 5)")
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
    people: Optional[int] = Field(default=None, description="Number of people traveling (e.g. 2, 5)")
    kids: Optional[int] = Field(default=None, description="Number of kids traveling. 0 means no kids.")
    health: Optional[str] = Field(default=None, description="Health status (e.g., 'good', 'limited')")
    hobbies: Optional[str] = Field(default=None, description="Hobbies and interests (e.g., 'history, nature')")
    start_date: Optional[str] = Field(default=None, description="Start date of the trip (YYYY-MM-DD or 'flexible'). If the user says 'not decided', 'flexible', 'no fixed date', 'haven't decided', or 'any date', you MUST output 'flexible'.")

    @staticmethod
    def _json_schema_extra(schema: dict):
        """Strip redundant per-property titles, but KEEP the root title.

        langchain-core derives the OpenAI/Gemini tool function name from
        ``schema["title"]``. The previous version popped it, so
        ``with_structured_output(TravelState)`` produced a function declaration
        with ``name=""`` and every extraction failed with:

            400 GenerateContentRequest.tools[0].function_declarations[0].name:
                Invalid function name. Must start with a letter or an underscore.

        The empty name is what made ExploreX re-ask for information the user
        had already given (nothing was ever extracted or persisted).
        """
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)

    model_config = ConfigDict(
        title="TravelState",
        json_schema_extra=_json_schema_extra,
    )

class ChatAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize the ChatAgent with specified model."""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7, streaming=True)
        # Use a non-streaming model for structured output
        self.extractor_model = ChatGoogleGenerativeAI(model=model_name, temperature=0)
        self.structured_extractor = self.extractor_model.with_structured_output(TravelState)
        self.tool_function_name = None
        # Fail loudly (and degrade gracefully) instead of silently looping if a
        # future langchain/schema change produces an invalid tool name again.
        self.extractor_error = self._validate_extractor_tool_name()
        if self.extractor_error:
            # Keep the object, but never call it: an invalid declaration would
            # only produce another HTTP 400 on every single turn.
            self.structured_extractor = None
        
        # Define required fields (these must be filled)
        # budget OR budget_amount must be present — checked by _has_budget()
        self.required_fields = ["origin_city", "city", "days", "people", "kids", "health", "hobbies", "start_date", "budget"]
        # Define all fields, including optional ones
        self.all_fields = self.required_fields + ["budget_amount", "budget_strictness", "specificRequirements"]
        self.conversation_history = []

    _BUDGET_CATEGORIES = ("low", "medium", "high")

    def _validate_extractor_tool_name(self):
        """Verify the Gemini function-declaration name for ``TravelState``.

        langchain-core derives the tool name from the JSON schema title. When
        that title is missing the name becomes ``""`` and Gemini rejects the
        whole request with HTTP 400, which used to be swallowed as "extraction
        failed" and caused ExploreX to re-ask the same questions forever.

        Returns ``None`` when the declaration is usable, otherwise a short
        reason string. The reason is deliberately provider-safe (no keys, no
        request bodies) so it can be logged and surfaced in diagnostics.
        """
        try:
            from langchain_core.utils.function_calling import convert_to_openai_tool

            tool = convert_to_openai_tool(TravelState)
            name = ((tool or {}).get("function") or {}).get("name")
            self.tool_function_name = name
            if not is_valid_tool_name(name):
                print(
                    f"[ERROR] Structured extractor function name {name!r} is invalid "
                    f"for Gemini (must match ^[A-Za-z_][A-Za-z0-9_.:-]{{0,127}}$). "
                    f"Deterministic extraction will be used until this is fixed."
                )
                return f"invalid function name: {name!r}"
            print(f"[DEBUG] Structured extractor function name: {name!r}")
            return None
        except Exception as e:
            # A schema-to-tool conversion failure is just as fatal for the
            # primary path, so record it and let the fallback take over.
            self.tool_function_name = None
            print(f"[ERROR] Could not derive structured extractor function name: {e}")
            return f"function name derivation failed: {e}"

    def _sanitize_extracted(self, payload: dict, allowed) -> dict:
        """Validate extractor output before it is merged into the trip state.

        A malformed or hallucinated value must never corrupt persisted state
        (for example ``days = "a few"`` or ``budget = "unlimited"``). Invalid
        values are logged and dropped; the deterministic parser can still fill
        the field from the user's own wording.
        """
        clean = {}
        for field, value in (payload or {}).items():
            if field not in allowed or not has_value(value):
                continue

            if field in ("days", "people", "kids", "budget_amount"):
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    print(f"[WARN] Ignoring non-numeric extraction {field}={value!r}")
                    continue
                if field in ("days", "people") and number <= 0:
                    print(f"[WARN] Ignoring non-positive extraction {field}={value!r}")
                    continue
                if field in ("kids", "budget_amount") and number < 0:
                    print(f"[WARN] Ignoring negative extraction {field}={value!r}")
                    continue
                if field == "budget_amount" and number == 0:
                    continue
                clean[field] = number
                continue

            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
                if field == "budget":
                    value = value.lower()
                    if value not in self._BUDGET_CATEGORIES:
                        print(f"[WARN] Ignoring unknown budget category {value!r}")
                        continue
                elif field == "budget_strictness":
                    value = value.lower()
                    if value not in ("strict", "flexible"):
                        print(f"[WARN] Ignoring unknown budget strictness {value!r}")
                        continue
            clean[field] = value
        return clean

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

    def is_valid_field(self, field: str, value) -> bool:
        """
        Semantic validation for required fields.
        Zero is valid for kids, but invalid for days/people.
        Empty strings or None are invalid.
        """
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
        if field in ["days", "people"] and value == 0:
            return False
        return True

    def _has_budget(self, state: dict) -> bool:
        """Return True if the user has provided either a budget category or a numerical budget amount."""
        return self.is_valid_field("budget", state.get("budget")) or self.is_valid_field("budget_amount", state.get("budget_amount"))

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
            # Always attempt to extract the full mutable field set (not just the
            # missing ones). This lets the user MODIFY an already-captured value
            # later in the conversation — e.g. "change my destination to Paris",
            # "make it 7 days", "increase my budget" — instead of the first value
            # being frozen forever. The extractor is instructed to return a value
            # ONLY when the user explicitly states/changes it, so unchanged fields
            # come back as None and are left untouched.
            fields_to_extract = list(dict.fromkeys(
                self.required_fields + ["budget_amount", "budget_strictness"]
            ))

            if fields_to_extract:
                # Formulate extraction prompt with context of the fields and their descriptions
                field_descriptions = []
                for field_name, field_info in TravelState.model_json_schema().get("properties", {}).items():
                    if field_name in fields_to_extract:
                        field_descriptions.append(f"- {field_name}: {field_info.get('description', '')}")
                
                desc_str = "\n".join(field_descriptions)
                
                history_texts = []
                for msg in self.conversation_history[-3:]:
                    if isinstance(msg, HumanMessage):
                        history_texts.append(f"User: {msg.content}")
                    elif isinstance(msg, AIMessage):
                        history_texts.append(f"Assistant: {msg.content}")
                history_texts.append(f"User: {user_input}")
                context_str = "\n".join(history_texts)
                
                extraction_prompt = (
                    f"Carefully extract the following travel details from the user's LATEST input: {', '.join(fields_to_extract)}.\n\n"
                    f"Definitions:\n{desc_str}\n\n"
                    f"IMPORTANT BUDGET RULES:\n"
                    f"- If user says 'low/medium/high/budget/luxury' → fill 'budget' field only.\n"
                    f"- If user states a specific INR number (e.g. ₹50000, 50 thousand, 1 lakh) → fill 'budget_amount' with the integer, leave 'budget' empty.\n"
                    f"- 1 lakh = 100000, 50 thousand = 50000.\n"
                    f"- Detect strictness from phrases: 'strictly/must not exceed/keep below/no more than' → 'strict'; 'around/approximately/up to/roughly' → 'flexible'.\n\n"
                    f"UPDATE / CHANGE RULE: If the user explicitly changes or updates a value "
                    f"(e.g. 'change my destination to Paris', 'make it 7 days', 'increase my budget to 150000', "
                    f"'actually 3 people'), return the NEW value for that field so it overrides the previous one. "
                    f"The user's LATEST message always wins over earlier context.\n\n"
                    f"CRITICAL: Extract ONLY information explicitly stated by the user. Do not infer, guess, or use default values for ANY field. "
                    f"If a field is not mentioned or changed in the latest input, leave it as None.\n\n"
                    f"Do NOT confuse health status with destination, or budget amount with number of people.\n\n"
                    f"Conversation Context:\n{context_str}"
                )
                
                extracted_dict = {}
                applied_deterministic = {}

                if self.structured_extractor is None:
                    print(
                        f"[WARN] Structured extractor unavailable "
                        f"({self.extractor_error}); using deterministic extraction"
                    )
                else:
                    try:
                        print(f"[DEBUG] Invoking structured_extractor...")
                        # Pass timeout in config instead of using ThreadPoolExecutor
                        extracted_data = self.structured_extractor.invoke(
                            extraction_prompt,
                            config={"timeout": 15},
                        )
                    except Exception as e:
                        # Never suppress this: log the provider error, then
                        # recover the details deterministically below.
                        import traceback
                        print(f"[ERROR] Structured extraction failed: {e}")
                        traceback.print_exc()
                        extracted_data = None

                    if extracted_data is None:
                        print(
                            "[WARN] Structured extraction returned no object; "
                            "using deterministic extraction"
                        )
                    else:
                        try:
                            if isinstance(extracted_data, TravelState):
                                payload = extracted_data.model_dump(exclude_none=True)
                            elif isinstance(extracted_data, dict):
                                payload = TravelState.model_validate(
                                    extracted_data
                                ).model_dump(exclude_none=True)
                            else:
                                raise TypeError(
                                    f"unexpected extractor return type "
                                    f"{type(extracted_data).__name__}"
                                )

                            extracted_dict = self._sanitize_extracted(
                                payload, fields_to_extract
                            )
                            print(f"[DEBUG] extracted_dict: {extracted_dict}")
                            # The LLM is authoritative for explicit updates, so it
                            # may overwrite an earlier value when the user changes it.
                            for field, value in extracted_dict.items():
                                state[field] = value
                                print(f"Updated state: {field} = {value}")
                        except Exception as e:
                            import traceback
                            print(f"[ERROR] Extracted object failed validation: {e}")
                            traceback.print_exc()
                            extracted_dict = {}

                # Deterministic safety net: recover explicit details from the
                # user's own message without a second model call. This only
                # fills fields that are still empty, so a successful LLM
                # extraction (including an intentional update) always wins.
                deterministic = extract_trip_fields(user_input, current=state)
                applied_deterministic = merge_missing(
                    state, deterministic, keys=fields_to_extract
                )
                if applied_deterministic:
                    print(
                        f"[DEBUG] Deterministic fallback filled: "
                        f"{sorted(applied_deterministic)}"
                    )

                # Derive a budget category from a numeric amount, unless the
                # user explicitly gave a category (low/medium/high/luxury) in
                # this same turn — in which case their wording wins.
                category_given_now = (
                    "budget" in extracted_dict or "budget" in applied_deterministic
                )
                if state.get("budget_amount") and not category_given_now:
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


        # Add user input to conversation if not empty
        if user_input and user_input.strip():
            self.conversation_history.append(HumanMessage(content=user_input))

        # Get AI response based on current state and conversation history
        messages = self.conversation_history.copy()
        missing = [f for f in self.required_fields if not self.is_valid_field(f, state.get(f))]
        # Budget is satisfied if either category OR numeric amount is provided
        if self._has_budget(state) and "budget" in missing:
            missing = [f for f in missing if f != "budget"]
        complete = len(missing) == 0
        messages.append(HumanMessage(content=f'''
        Current state: {json.dumps(state, ensure_ascii=False)}
        Required fields: {json.dumps(self.required_fields, ensure_ascii=False)}
        Missing fields: {json.dumps(missing, ensure_ascii=False)}
        
        IMPORTANT RULES:
        1. Strictly ask ONLY about the fields explicitly listed in `Missing fields`. Do not ask about food preferences, accommodation, or transportation unless they are missing.
        2. Remember to acknowledge information that has already been provided.
        3. Tell the user they can write "not decided" for the start date if flexible.
        4. For budget, accept EITHER a category (low/medium/high) OR a specific INR amount (e.g. ₹50,000). Both are valid.
        5. Keep your response concise and friendly (1-2 sentences max).
        '''))

        print(f"[DEBUG] Calling LLM (model.stream)...")
        try:
            response = self.model.stream(messages)
            print(f"[DEBUG] LLM stream object created (lazy, not yet consumed): {type(response)}")
            
            def response_generator():
                full_content = ""
                for chunk in response:
                    text_chunk = self._extract_text(chunk.content)
                    if text_chunk:
                        full_content += text_chunk
                    yield chunk
                self.conversation_history.append(AIMessage(content=full_content))
                
            return {
                "stream": response_generator(),
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