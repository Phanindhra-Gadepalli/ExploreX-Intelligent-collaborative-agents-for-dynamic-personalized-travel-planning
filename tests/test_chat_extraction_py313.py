"""Regression tests for chat extraction after the Gemini tool-name failure.

Background
----------
``TravelState`` used to strip ``title`` from its JSON schema. langchain-core
derives the Gemini function-declaration name from that same ``title``, so the
tool was sent as ``{"name": ""}`` and every structured-extraction call failed
with::

    400 GenerateContentRequest.tools[0].function_declarations[0].name:
        Invalid function name.

Because the exception was swallowed, no field was ever persisted and the chat
agent kept re-asking for information the user had already supplied.

These tests are offline: no provider credentials and no network. The model
stream is replaced with an empty iterator, and the structured extractor is
either disabled or replaced with a stub.
"""
import pytest

from agents import chat_agent as chat_agent_module
from agents.chat_agent import ChatAgent, TravelState
from services.chat_extract import (
    extract_trip_fields,
    has_value,
    is_valid_tool_name,
    merge_missing,
)

TOKYO_MESSAGES = [
    "5-day Tokyo luxury trip",
    "Bengaluru, 10 people and no kids, start date is flexible",
    "High budget, everyone is in good health and we like photography and viewpoints",
]


class _EmptyStream:
    def __iter__(self):
        return iter([])


class _OfflineModel:
    """Stands in for the streaming chat model; never touches the network."""

    def stream(self, messages):  # noqa: ARG002 - signature mirrors the real model
        return _EmptyStream()


class _RaisingExtractor:
    """Simulates the original Gemini 400 (or a timeout/quota error)."""

    def invoke(self, *args, **kwargs):  # noqa: ARG002
        raise RuntimeError(
            "400 GenerateContentRequest.tools[0].function_declarations[0].name: "
            "Invalid function name."
        )


@pytest.fixture
def offline_agent(monkeypatch):
    # Construction must never require a real credential.
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    agent = ChatAgent()
    agent.model = _OfflineModel()
    return agent


def _run(agent, message, state):
    result = agent.collect_info(message, state)
    # Consume the (empty) stream so any deferred work completes.
    if result.get("stream") is not None:
        list(result["stream"])
    return result


def test_travel_state_tool_name_is_valid_for_gemini():
    """The exact field Gemini rejected must now be populated and legal."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    tool = convert_to_openai_tool(TravelState)
    name = tool["function"]["name"]

    assert name == "TravelState"
    assert is_valid_tool_name(name)
    # A non-empty description is also required by the provider.
    assert tool["function"].get("description", "").strip()


def test_empty_tool_name_is_the_reproduced_failure_mode():
    """Document the old bug: a popped root title yields ``name=""``."""
    from pydantic import BaseModel, ConfigDict
    from langchain_core.utils.function_calling import convert_to_openai_tool

    class BrokenState(BaseModel):
        model_config = ConfigDict(
            json_schema_extra=lambda schema: schema.pop("title", None)
        )
        city: str | None = None

    name = convert_to_openai_tool(BrokenState)["function"]["name"]
    assert name == ""
    assert not is_valid_tool_name(name)


def test_validate_extractor_tool_name_flags_an_invalid_schema(monkeypatch, offline_agent):
    from pydantic import BaseModel, ConfigDict

    class BrokenState(BaseModel):
        model_config = ConfigDict(
            json_schema_extra=lambda schema: schema.pop("title", None)
        )
        city: str | None = None

    monkeypatch.setattr(chat_agent_module, "TravelState", BrokenState)
    reason = offline_agent._validate_extractor_tool_name()

    assert reason is not None
    assert "invalid function name" in reason
    assert offline_agent.tool_function_name == ""


def test_deterministic_parse_of_the_tokyo_conversation():
    state = {}
    for message in TOKYO_MESSAGES[:2]:
        parsed = extract_trip_fields(message, current=state)
        merge_missing(state, parsed)

    assert state["city"] == "Tokyo"
    assert state["days"] == 5
    assert state["origin_city"] == "Bengaluru"
    assert state["people"] == 10
    assert state["kids"] == 0
    assert state["start_date"] == "flexible"

    parsed = extract_trip_fields(TOKYO_MESSAGES[2], current=state)
    merge_missing(state, parsed)

    assert state["budget"] == "high"
    assert state["health"] == "good"
    assert "photography" in state["hobbies"]
    assert "viewpoint" in state["hobbies"]


def test_zero_kids_and_flexible_dates_are_answered_not_missing(offline_agent):
    parsed = extract_trip_fields(
        "Bengaluru, 10 people and no kids, start date is flexible"
    )
    assert parsed["kids"] == 0
    assert parsed["start_date"] == "flexible"
    assert has_value(parsed["kids"])
    # A real zero is valid; zero days or zero people are not.
    assert offline_agent.is_valid_field("kids", 0)
    assert not offline_agent.is_valid_field("days", 0)
    assert not offline_agent.is_valid_field("people", 0)


def test_flexible_date_variants():
    for text in (
        "start date is flexible",
        "not decided",
        "any date is fine",
        "no fixed date",
        "undecided",
    ):
        assert extract_trip_fields(text).get("start_date") == "flexible", text


def test_merge_missing_only_fills_empty_fields():
    state = {"city": "Tokyo", "days": 5, "kids": 0}
    applied = merge_missing(
        state,
        {"city": "Paris", "days": 7, "kids": 2, "people": 10},
    )
    assert applied == {"people": 10}
    # Existing values (including the valid zero) are preserved.
    assert state == {"city": "Tokyo", "days": 5, "kids": 0, "people": 10}


def test_collect_info_completes_the_tokyo_scenario_without_any_extractor(offline_agent):
    """The full flow must work even if Gemini is completely unavailable."""
    offline_agent.structured_extractor = None
    state = {}

    result = _run(offline_agent, TOKYO_MESSAGES[0], state)
    state = result["state"]
    assert result["complete"] is False
    assert "city" not in result["missing_fields"]
    assert "days" not in result["missing_fields"]

    result = _run(offline_agent, TOKYO_MESSAGES[1], state)
    state = result["state"]
    assert result["complete"] is False
    assert not {"origin_city", "people", "kids", "start_date"} & set(
        result["missing_fields"]
    )

    result = _run(offline_agent, TOKYO_MESSAGES[2], state)
    state = result["state"]

    assert result["complete"] is True
    assert result["missing_fields"] == []
    assert state["city"] == "Tokyo"
    assert state["days"] == 5
    assert state["origin_city"] == "Bengaluru"
    assert state["people"] == 10
    assert state["kids"] == 0
    assert state["start_date"] == "flexible"
    assert state["budget"] == "high"
    assert state["health"] == "good"


def test_collect_info_falls_back_when_extractor_raises(offline_agent):
    """A provider error must not lose the user's message or loop the chat."""
    offline_agent.structured_extractor = _RaisingExtractor()

    result = _run(offline_agent, "7-day Paris trip from Mumbai for 2 people", {})
    state = result["state"]

    assert state["city"] == "Paris"
    assert state["days"] == 7
    assert state["origin_city"] == "Mumbai"
    assert state["people"] == 2
    assert "city" not in result["missing_fields"]
    assert "days" not in result["missing_fields"]
    assert "origin_city" not in result["missing_fields"]
    assert "people" not in result["missing_fields"]


def test_partial_llm_values_win_and_fallback_fills_the_rest(offline_agent):
    """The LLM is authoritative; deterministic parsing only fills blanks."""

    class _PartialExtractor:
        def invoke(self, *args, **kwargs):  # noqa: ARG002
            return TravelState(days=6, city="Kyoto")

    offline_agent.structured_extractor = _PartialExtractor()

    result = _run(offline_agent, "Kyoto for 5 days, 3 people", {"budget": "medium"})
    state = result["state"]

    assert state["days"] == 6  # LLM value wins over the regex's 5
    assert state["city"] == "Kyoto"  # preserved from the LLM
    assert state["people"] == 3  # recovered by the fallback
    assert state["budget"] == "medium"  # untouched


def test_numeric_budget_derives_a_category(offline_agent):
    offline_agent.structured_extractor = None

    result = _run(offline_agent, "Our budget is Rs 2 lakh", {})
    state = result["state"]

    assert state["budget_amount"] == 200_000
    assert state["budget"] == "high"


def test_explicit_budget_category_beats_amount_derivation(offline_agent):
    offline_agent.structured_extractor = None

    result = _run(offline_agent, "Medium budget of around Rs 150000", {})
    state = result["state"]

    assert state["budget_amount"] == 150_000
    assert state["budget"] == "medium"  # the user's wording wins


def test_sanitize_extracted_rejects_bad_values(offline_agent):
    clean = offline_agent._sanitize_extracted(
        {
            "days": "a few",
            "people": -2,
            "kids": 0,
            "budget": "unlimited",
            "budget_amount": "50000",
            "budget_strictness": "whenever",
            "city": "  Tokyo  ",
        },
        offline_agent.required_fields + ["budget_amount", "budget_strictness"],
    )

    assert "days" not in clean
    assert "people" not in clean
    assert "budget" not in clean
    assert "budget_strictness" not in clean
    assert clean["kids"] == 0
    assert clean["budget_amount"] == 50000
    assert clean["city"] == "Tokyo"


def test_amount_strictness_and_synonyms():
    parsed = extract_trip_fields(
        "10 people, no kids, budget must not exceed Rs 80,000, everyone is healthy"
    )
    assert parsed["people"] == 10
    assert parsed["kids"] == 0
    assert parsed["budget_amount"] == 80_000
    assert parsed["budget_strictness"] == "strict"
    assert parsed["health"] == "good"


def test_group_of_ten_and_photography_interests():
    parsed = extract_trip_fields("we are a group of ten who love photography and viewpoints")
    assert parsed["people"] == 10
    assert parsed["hobbies"] == "photography and viewpoints"
