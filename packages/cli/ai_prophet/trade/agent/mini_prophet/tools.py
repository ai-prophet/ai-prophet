"""Trading-specific tools for the mini-prophet forecasting agent."""

from __future__ import annotations

from typing import Any

from miniprophet.environment.source_board import SourceBoard
from miniprophet.exceptions import Submitted


class MarketDataTool:
    """Provides prediction market price data to the forecasting agent.

    Implements the mini-prophet ``Tool`` protocol.  Takes no arguments —
    always returns data for the market passed at construction time.
    """

    name = "get_market_data"

    def __init__(self, market: Any) -> None:
        self._market = market

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Get current prediction market prices and trading data.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def execute(self, args: dict) -> dict:  # noqa: ARG002
        m = self._market
        output = (
            f"Market: {m.question}\n"
            f"YES: bid={m.yes_bid:.3f} ask={m.yes_ask:.3f} mid={m.yes_mark:.3f}\n"
            f"NO:  bid={m.no_bid:.3f} ask={m.no_ask:.3f} mid={m.no_mark:.3f}\n"
            f"24h volume: {m.volume_24h:.0f}\n"
            f"Quote time: {m.quote_ts.isoformat()}"
        )
        return {"output": output}

    def display(self, output: dict) -> None:  # noqa: ARG002
        pass


class TradingSubmitTool:
    """Extended submit tool that also captures rationale.

    On a valid submission it raises :class:`Submitted` (same as the
    upstream ``SubmitTool``), carrying the probabilities **and** rationale
    in the exit message's ``extra`` dict.
    """

    name = "submit"

    def __init__(self, outcomes: list[str], board: SourceBoard) -> None:
        self._outcomes = outcomes
        self._board = board

    def get_schema(self) -> dict:
        outcomes_desc = ", ".join(f'"{o}"' for o in self._outcomes)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Submit your final probability forecast. "
                    "Probabilities must sum to 1.0."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "probabilities": {
                            "type": "object",
                            "description": (
                                f"Map of outcome to probability. "
                                f"Outcomes: [{outcomes_desc}]."
                            ),
                            "properties": {
                                o: {"type": "number"} for o in self._outcomes
                            },
                            "required": list(self._outcomes),
                        },
                        "rationale": {
                            "type": "string",
                            "description": (
                                "2-3 sentence explanation of your forecast."
                            ),
                        },
                    },
                    "required": ["probabilities", "rationale"],
                },
            },
        }

    def execute(self, args: dict) -> dict:
        probabilities = args.get("probabilities", {})

        # Validate: all outcomes present
        missing = [o for o in self._outcomes if o not in probabilities]
        if missing:
            return {
                "output": f"Missing outcomes: {missing}. Please include all outcomes.",
                "error": True,
            }

        # Validate: values in [0, 1]
        for outcome, prob in probabilities.items():
            if not isinstance(prob, (int, float)) or prob < 0 or prob > 1:
                return {
                    "output": (
                        f"Probability for '{outcome}' must be a number "
                        f"between 0 and 1, got {prob}."
                    ),
                    "error": True,
                }

        rationale = args.get("rationale", "")

        raise Submitted({
            "role": "exit",
            "content": "Forecast submitted.",
            "extra": {
                "exit_status": "submitted",
                "submission": probabilities,
                "rationale": rationale,
                "board": self._board.serialize(),
            },
        })

    def display(self, output: dict) -> None:  # noqa: ARG002
        pass
