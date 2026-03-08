"""Tool schemas for LLM structured output.

Defines schemas that force the LLM to return valid structured data
via tool/function calling, eliminating JSON parsing errors.
"""

from ai_prophet.llm import ToolSchema

REVIEW_TOOL = ToolSchema(
    name="submit_review",
    description="Submit market selection decisions for detailed analysis",
    parameters={
        "type": "object",
        "properties": {
            "review": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "market_id": {"type": "string"},
                        "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 3,
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["market_id", "priority", "queries", "rationale"],
                },
                "maxItems": 10,
            },
        },
        "required": ["review"],
    },
)


SEARCH_SUMMARY_TOOL = ToolSchema(
    name="submit_search_summary",
    description="Submit a summary of search results for a market",
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-6 sentence synthesis of findings",
            },
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key factual points from the search",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Unresolved questions or ambiguities",
            },
        },
        "required": ["summary", "key_points", "open_questions"],
    },
)


FORECAST_TOOL = ToolSchema(
    name="submit_forecast",
    description="Submit a probability forecast for an event",
    parameters={
        "type": "object",
        "properties": {
            "p_yes": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Probability the event resolves YES",
            },
            "rationale": {
                "type": "string",
                "description": "2-3 sentence explanation of probability estimate",
            },
        },
        "required": ["p_yes", "rationale"],
    },
)


TRADE_DECISION_TOOL = ToolSchema(
    name="submit_trade_decision",
    description="Submit a trade decision with sizing",
    parameters={
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": ["BUY_YES", "BUY_NO", "HOLD"],
                "description": "Trade recommendation",
            },
            "size_usd": {
                "type": "number",
                "minimum": 0,
                "description": "Dollar amount to trade (0 if HOLD)",
            },
            "rationale": {
                "type": "string",
                "maxLength": 1000,
                "description": "Brief reasoning for the trade decision",
            },
        },
        "required": ["recommendation", "size_usd", "rationale"],
    },
)

