"""Run a full forward pass of the agent pipeline and dump every prompt.

Builds a realistic ``TickContext`` (multiple candidates, a held position,
per-market memory), runs the real 4-stage pipeline against a capturing
fake LLM and fake search client, then writes every system + user prompt
sent at each stage to ``PIPELINE_PROMPTS.md`` at the repo root.

Run:
    python packages/cli/scripts/dump_pipeline_prompts.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from ai_prophet.trade.agent import AgentPipeline
from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.tick_context import CandidateMarket, Position
from ai_prophet.trade.llm import LLMMessage
from ai_prophet_core.client import ServerAPIClient

# ---------------------------------------------------------------------------
# Capturing fakes
# ---------------------------------------------------------------------------

@dataclass
class CapturedCall:
    stage: str            # review | search.queries | search.summary | forecast | action
    tool: str
    system: str
    user: str
    market_id: str | None = None


@dataclass
class CapturingLLM:
    calls: list[CapturedCall] = field(default_factory=list)
    provider: str = "fake"
    model: str = "fake-capture"

    # Track which market the current query-gen / summarize / forecast / action
    # call is about. The pipeline doesn't pass that down explicitly, so we
    # infer it from the user prompt content. Good enough for a dump.
    def generate_json(
        self,
        messages: list[LLMMessage],
        tool=None,
        **_: Any,
    ) -> dict[str, Any]:
        tool_name = getattr(tool, "name", "unknown")
        system = messages[0].content if messages else ""
        user = messages[1].content if len(messages) > 1 else ""
        stage = _STAGE_FOR_TOOL[tool_name]
        # Review is the single global pick; everything else is per-market.
        market_id = None if stage == "review" else _infer_market_id(user)
        self.calls.append(
            CapturedCall(stage=stage, tool=tool_name, system=system, user=user, market_id=market_id),
        )
        return _CANNED_RESPONSES[tool_name]

    def close(self) -> None:
        return None


class FakeSearchClient:
    """Returns two canned results per query so the summarize step runs."""

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        return [
            {
                "title": f"Result 1 for: {query[:40]}",
                "snippet": "First snippet establishing the baseline context.",
                "url": "https://example.com/article-1",
                "text": "Extended article body. " * 20,
            },
            {
                "title": f"Result 2 for: {query[:40]}",
                "snippet": "Second snippet offering a contrasting view.",
                "url": "https://example.com/article-2",
            },
        ][:limit]

    def close(self) -> None:
        return None


_STAGE_FOR_TOOL = {
    "submit_review":           "review",
    "submit_research_queries": "search.queries",
    "submit_search_summary":   "search.summary",
    "submit_forecast":         "forecast",
    "submit_trade_decision":   "action",
}


_CANNED_RESPONSES: dict[str, dict[str, Any]] = {
    "submit_review": {
        "review": [
            {"market_id": "kalshi:DEMOCRATS-TX-26", "priority": 88,
             "rationale": "Volume spike and recent polling shift."},
            {"market_id": "polymarket:AVS-CUP-26", "priority": 72,
             "rationale": "Held position needs reassessment."},
            {"market_id": "kalshi:FED-RATE-MAR", "priority": 65,
             "rationale": "FOMC meeting this week, high info value."},
        ]
    },
    "submit_research_queries": {
        "queries": [
            "latest Texas senate poll 2026 democrats",
            "Beto O'Rourke Texas senate run 2026",
        ]
    },
    "submit_search_summary": {
        "summary": "Polling shows Republican incumbent with a stable ~6 point lead. "
                   "No major scandal or fundraising surprise in last 30 days.",
        "key_points": [
            "538 average: R+6.1 over last 30 days",
            "Democratic challenger raised $4.2M Q4, well below GOP $9.8M",
        ],
        "open_questions": [
            "Will incumbent face a serious primary challenge?",
        ],
    },
    "submit_forecast": {
        "p_yes": 0.27,
        "rationale": "Polling lead is stable and well outside the historical "
                     "upset band. Fundraising gap reinforces this. Modest skew "
                     "vs current 33% mid.",
    },
    "submit_trade_decision": {
        "recommendation": "BUY_NO",
        "size_usd": 250.0,
        "rationale": "Forecast 27% vs NO ask of 65% implies +8pt edge after "
                     "spread. Sizing to ~2.5% of cash.",
    },
}


# market_id -> identifying substrings to search for in a user prompt.
# Question text is included so stages like forecast/action (which only render
# the question, not the raw id) still get correctly attributed.
_MARKET_FINGERPRINTS: dict[str, tuple[str, ...]] = {
    "kalshi:DEMOCRATS-TX-26": (
        "kalshi:DEMOCRATS-TX-26",
        "Will Democrats win the 2026 Texas Senate race?",
    ),
    "polymarket:AVS-CUP-26": (
        "polymarket:AVS-CUP-26",
        "Will the Colorado Avalanche win the 2026 Stanley Cup?",
    ),
    "kalshi:FED-RATE-MAR": (
        "kalshi:FED-RATE-MAR",
        "Will the Fed cut rates at the March 2026 FOMC meeting?",
    ),
}


def _infer_market_id(user_prompt: str) -> str | None:
    """Attribute a call to a market by the earliest fingerprint match.

    Forecast/action prompts mention the portfolio (which may reference a
    different market's question), so we pick the fingerprint that appears
    first — that's always the primary market for the call.
    """
    best_idx: int | None = None
    best_mid: str | None = None
    for mid, fingerprints in _MARKET_FINGERPRINTS.items():
        for fp in fingerprints:
            idx = user_prompt.find(fp)
            if idx == -1:
                continue
            if best_idx is None or idx < best_idx:
                best_idx, best_mid = idx, mid
    return best_mid


# ---------------------------------------------------------------------------
# Tick context fixture
# ---------------------------------------------------------------------------

def build_tick_context() -> TickContext:
    """Three realistic candidates, one held position, populated memory."""
    tick_ts = datetime(2026, 5, 17, 22, 0, tzinfo=UTC)
    asof = tick_ts - timedelta(minutes=10)

    candidates = (
        CandidateMarket(
            market_id="kalshi:DEMOCRATS-TX-26",
            question="Will Democrats win the 2026 Texas Senate race?",
            description="Resolves YES if Democratic nominee wins.",
            resolution_time=datetime(2026, 11, 4, 23, 59, tzinfo=UTC),
            yes_bid=0.33, yes_ask=0.35, yes_mark=0.34,
            no_bid=0.65, no_ask=0.67, no_mark=0.66,
            volume_24h=185_000.0,
            quote_ts=asof,
        ),
        CandidateMarket(
            market_id="polymarket:AVS-CUP-26",
            question="Will the Colorado Avalanche win the 2026 Stanley Cup?",
            description="Resolves YES if Avalanche win the Stanley Cup Finals.",
            resolution_time=datetime(2026, 6, 30, 23, 59, tzinfo=UTC),
            yes_bid=0.18, yes_ask=0.21, yes_mark=0.195,
            no_bid=0.79, no_ask=0.82, no_mark=0.805,
            volume_24h=42_000.0,
            quote_ts=asof,
        ),
        CandidateMarket(
            market_id="kalshi:FED-RATE-MAR",
            question="Will the Fed cut rates at the March 2026 FOMC meeting?",
            description="Resolves YES on a cut of any size.",
            resolution_time=datetime(2026, 3, 19, 18, 0, tzinfo=UTC),
            yes_bid=0.42, yes_ask=0.44, yes_mark=0.43,
            no_bid=0.56, no_ask=0.58, no_mark=0.57,
            volume_24h=920_000.0,
            quote_ts=asof,
        ),
    )

    positions = (
        Position(
            market_id="polymarket:AVS-CUP-26",
            side="NO",
            shares=Decimal("800"),
            avg_entry_price=Decimal("0.400"),
            current_price=Decimal("0.805"),
            unrealized_pnl=Decimal("324.00"),
            realized_pnl=Decimal("0.00"),
            updated_at=asof,
            question="Will the Colorado Avalanche win the 2026 Stanley Cup?",
            entry_forecast_rationale=(
                "Avalanche entered the playoffs as the 4-seed with the league's "
                "weakest goaltending split (.892 SV%). Market priced YES at 40%, "
                "well above the historical base rate (~14%) for similarly-seeded "
                "teams without elite goaltending. Estimated true p_yes ~18%."
            ),
            entry_trade_rationale=(
                "Bought NO at 0.40 for ~22pp of edge vs forecast. Sized at "
                "$320 (~3% of equity) — conviction was high but resolution is "
                "still 2 months out, so kept room to add on dips."
            ),
        ),
    )

    return TickContext(
        run_id="prompt_dump_demo",
        tick_ts=tick_ts,
        data_asof_ts=asof,
        candidate_set_id="snap_dump_demo",
        submission_deadline=tick_ts + timedelta(minutes=55),
        server_now=tick_ts + timedelta(minutes=2),
        candidates=candidates,
        cash=Decimal("6500.00"),
        equity=Decimal("9824.00"),
        total_pnl=Decimal("-176.00"),
        positions=positions,
        total_fills=4,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run() -> Path:
    llm = CapturingLLM()
    api_client = Mock(spec=ServerAPIClient)
    api_client.base_url = "http://fake.local"

    pipeline = AgentPipeline(
        llm_client=llm,  # type: ignore[arg-type]
        event_store=None,
        api_client=api_client,
        config={"search_client": FakeSearchClient()},
    )

    tick_ctx = build_tick_context()
    pipeline.execute(tick_ctx, run_id=tick_ctx.run_id)

    out = _format_markdown(llm.calls, tick_ctx)
    path = Path(__file__).resolve().parents[3] / "PIPELINE_PROMPTS.md"
    path.write_text(out, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------

_STAGE_HEADERS = [
    ("review",         "1. Review stage"),
    ("search.queries", "2a. Search stage — query generation (per market)"),
    ("search.summary", "2b. Search stage — summarization (per market)"),
    ("forecast",       "3. Forecast stage (per market)"),
    ("action",         "4. Action stage (per market)"),
]


def _format_markdown(calls: list[CapturedCall], tick_ctx: TickContext) -> str:
    out: list[str] = []
    out.append("# Pipeline prompt dump\n")
    out.append(
        "Captured by `packages/cli/scripts/dump_pipeline_prompts.py`. "
        "Every system + user prompt the agent actually sends to the LLM on a "
        "single tick, in the order the pipeline emits them.\n",
    )
    out.append(_render_context_block(tick_ctx))

    by_stage: dict[str, list[CapturedCall]] = {key: [] for key, _ in _STAGE_HEADERS}
    for c in calls:
        by_stage.setdefault(c.stage, []).append(c)

    for stage_key, header in _STAGE_HEADERS:
        stage_calls = by_stage.get(stage_key) or []
        out.append(f"\n## {header}\n")
        out.append(f"_{len(stage_calls)} LLM call(s) — tool `{_tool_for_stage(stage_key)}`_\n")
        if not stage_calls:
            out.append("\n_(no calls)_\n")
            continue
        for i, c in enumerate(stage_calls, start=1):
            title = f"Call {i}"
            if c.market_id:
                title += f" — `{c.market_id}`"
            out.append(f"\n### {title}\n")
            out.append("**System prompt:**\n")
            out.append(_fenced(c.system))
            out.append("\n**User prompt:**\n")
            out.append(_fenced(c.user))

    return "".join(out)


def _render_context_block(tick_ctx: TickContext) -> str:
    return (
        "\n## Tick context\n\n"
        f"- `run_id`: `{tick_ctx.run_id}`\n"
        f"- `tick_ts`: `{tick_ctx.tick_ts.isoformat()}`\n"
        f"- Cash: ${float(tick_ctx.cash):,.0f}  ·  "
        f"Equity: ${float(tick_ctx.equity):,.0f}  ·  "
        f"P&L: ${float(tick_ctx.total_pnl):+,.2f}\n"
        f"- Candidates: {len(tick_ctx.candidates)}  ·  "
        f"Open positions: {len(tick_ctx.positions)}\n"
    )


def _fenced(body: str) -> str:
    return f"\n```text\n{body.rstrip()}\n```\n"


def _tool_for_stage(stage_key: str) -> str:
    for tool, stage in _STAGE_FOR_TOOL.items():
        if stage == stage_key:
            return tool
    return "?"


if __name__ == "__main__":
    path = run()
    print(f"Wrote {path}")
