"""Tool schemas for LLM structured output.

Defines schemas that force the LLM to return valid structured data
via tool/function calling, eliminating JSON parsing errors.
"""

from ai_prophet.trade.llm import ToolSchema

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
                        "rationale": {"type": "string"},
                    },
                    "required": ["market_id", "priority", "rationale"],
                },
                "maxItems": 10,
            },
        },
        "required": ["review"],
    },
)


RESEARCH_QUERIES_TOOL = ToolSchema(
    name="submit_research_queries",
    description="Submit search queries to research this prediction market",
    parameters={
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 1000},
                "minItems": 1,
                "maxItems": 3,
                "description": "1-3 web search queries tailored to this market",
            },
        },
        "required": ["queries"],
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
                "enum": [
                    "BUY_YES",
                    "BUY_NO",
                    "SELL_YES",
                    "SELL_NO",
                    "HOLD",
                ],
                "description": (
                    "Trade recommendation. SELL_YES/SELL_NO only valid when "
                    "you currently hold that side of the market."
                ),
            },
            "size_usd": {
                "type": "number",
                "minimum": 0,
                "description": (
                    "Dollar amount to trade (0 if HOLD). For SELL actions, the "
                    "size is capped at your held position; use a large value "
                    "(e.g. position value) to fully exit."
                ),
            },
            "rationale": {
                "type": "string",
                "maxLength": 4000,
                "description": "Brief reasoning for the trade decision",
            },
        },
        "required": ["recommendation", "size_usd", "rationale"],
    },
)

