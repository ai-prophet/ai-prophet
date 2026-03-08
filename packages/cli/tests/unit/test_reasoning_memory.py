from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from ai_prophet_core.client_models import ReasoningEntry

from ai_prophet.agent.reasoning_memory import build_memory_context
from ai_prophet.agent.stages import ActionStage, ForecastStage, ReviewStage, StageResult
from ai_prophet.core.tick_context import CandidateMarket, TickContext


def test_build_memory_context_distills_recent_market_history():
    entries = [
        ReasoningEntry(
            participant_idx=0,
            tick_id=datetime(2026, 2, 15, 21, 0, tzinfo=UTC),
            reasoning={
                "candidates": [{"market_id": "m1", "yes_mark": 0.40}],
                "forecasts": {"m1": {"p_yes": 0.46}},
                "decisions": {"m1": {"recommendation": "BUY_YES", "size_usd": 120}},
            },
        ),
        ReasoningEntry(
            participant_idx=0,
            tick_id=datetime(2026, 2, 15, 22, 0, tzinfo=UTC),
            reasoning={
                "candidates": [{"market_id": "m1", "yes_mark": 0.42}],
                "forecasts": {"m1": {"p_yes": 0.44}},
                "decisions": {"m1": {"recommendation": "HOLD", "size_usd": 0}},
            },
        ),
    ]

    ctx = build_memory_context(
        entries=entries,
        current_market_ids=["m1"],
        market_history_limit=3,
    )

    assert "Recent memory (distilled):" in ctx.summary
    assert "m1" in ctx.by_market
    assert "p=0.46" in ctx.by_market["m1"]
    assert "p=0.44" in ctx.by_market["m1"]
    assert "a=BUY_YES" in ctx.by_market["m1"]


def test_build_memory_context_filters_to_active_markets():
    entries = [
        ReasoningEntry(
            participant_idx=0,
            tick_id=datetime(2026, 2, 15, 21, 0, tzinfo=UTC),
            reasoning={
                "candidates": [{"market_id": "old_market", "yes_mark": 0.3}],
                "forecasts": {"old_market": {"p_yes": 0.2}},
                "decisions": {"old_market": {"recommendation": "BUY_NO", "size_usd": 50}},
            },
        ),
    ]
    ctx = build_memory_context(
        entries=entries,
        current_market_ids=["new_market"],
        market_history_limit=3,
    )
    assert ctx.summary == ""
    assert ctx.by_market == {}


def test_build_memory_context_handles_mixed_market_id_types():
    """Memory mapping works even when market IDs mix int and str across payloads."""
    entries = [
        ReasoningEntry(
            participant_idx=0,
            tick_id=datetime(2026, 2, 15, 21, 0, tzinfo=UTC),
            reasoning={
                # Stored as numeric in candidates
                "candidates": [{"market_id": 654414, "yes_mark": 0.08}],
                # Stored as JSON object keys (strings)
                "forecasts": {"654414": {"p_yes": 0.07}},
                "decisions": {"654414": {"recommendation": "BUY_NO", "size_usd": 200}},
            },
        ),
    ]
    ctx = build_memory_context(
        entries=entries,
        # Current tick market IDs may come in as ints
        current_market_ids=[654414],
        market_history_limit=3,
    )

    assert "654414" in ctx.by_market
    assert "p=0.07" in ctx.by_market["654414"]
    assert "a=BUY_NO" in ctx.by_market["654414"]


def test_build_memory_context_keeps_by_market_for_all_active_markets():
    """Only summary is capped; by_market should keep all active market histories."""
    tick = datetime(2026, 2, 15, 21, 0, tzinfo=UTC)
    forecasts = {f"m{i}": {"p_yes": 0.4 + (i * 0.001)} for i in range(12)}
    entries = [
        ReasoningEntry(
            participant_idx=0,
            tick_id=tick,
            reasoning={
                "candidates": [{"market_id": f"m{i}", "yes_mark": 0.5} for i in range(12)],
                "forecasts": forecasts,
                "decisions": {f"m{i}": {"recommendation": "HOLD", "size_usd": 0} for i in range(12)},
            },
        )
    ]
    ctx = build_memory_context(
        entries=entries,
        current_market_ids=[f"m{i}" for i in range(12)],
        market_history_limit=3,
        max_markets=8,
    )

    assert len(ctx.by_market) == 12
    assert "m11" in ctx.by_market
    # Summary remains bounded/capped.
    assert len([ln for ln in ctx.summary.splitlines() if ln.startswith("- ")]) <= 8


# --- Prompt injection tests ---

def _make_tick_ctx(memory_summary="", memory_by_market=None):
    """Build a minimal TickContext with memory fields set."""
    market = CandidateMarket(
        market_id="m1",
        question="Will X happen?",
        description="desc",
        resolution_time=datetime(2026, 3, 1, tzinfo=UTC),
        yes_bid=0.45, yes_ask=0.55, yes_mark=0.50,
        no_bid=0.45, no_ask=0.55, no_mark=0.50,
        volume_24h=1000.0,
        quote_ts=datetime(2026, 2, 20, 5, 30, tzinfo=UTC),
    )
    return TickContext(
        run_id="test:0",
        tick_ts=datetime(2026, 2, 20, 6, 0, tzinfo=UTC),
        data_asof_ts=datetime(2026, 2, 20, 5, 30, tzinfo=UTC),
        candidate_set_id="snap_test",
        submission_deadline=datetime(2026, 2, 20, 6, 55, tzinfo=UTC),
        server_now=datetime(2026, 2, 20, 5, 45, tzinfo=UTC),
        candidates=(market,),
        cash=Decimal("10000"), equity=Decimal("10000"),
        total_pnl=Decimal("0"), positions=(), total_fills=0,
        memory_summary=memory_summary,
        memory_by_market=memory_by_market or {},
    )


def test_review_stage_includes_memory_in_prompt():
    """When memory_summary is set, ReviewStage includes it in the user prompt."""
    llm = MagicMock()
    llm.generate_json.return_value = {"review": []}
    stage = ReviewStage(llm_client=llm, max_markets=5)

    ctx = _make_tick_ctx(memory_summary="Recent memory (distilled):\n- m1: p=0.46 a=BUY_YES")
    stage.execute(ctx, {})

    # The user prompt is the second message passed to generate_json
    call_args = llm.generate_json.call_args
    messages = call_args[0][0]
    user_msg = messages[1].content
    assert "RECENT MEMORY:" in user_msg
    assert "p=0.46" in user_msg


def test_review_stage_omits_memory_when_empty():
    """When memory_summary is empty, no RECENT MEMORY block in prompt."""
    llm = MagicMock()
    llm.generate_json.return_value = {"review": []}
    stage = ReviewStage(llm_client=llm, max_markets=5)

    ctx = _make_tick_ctx(memory_summary="")
    stage.execute(ctx, {})

    messages = llm.generate_json.call_args[0][0]
    user_msg = messages[1].content
    assert "RECENT MEMORY" not in user_msg


def test_forecast_stage_includes_per_market_memory():
    """ForecastStage injects per-market memory into the user prompt."""
    llm = MagicMock()
    llm.generate_json.return_value = {"p_yes": 0.50, "rationale": "test"}
    stage = ForecastStage(llm_client=llm)

    memory = {"m1": "Recent history for m1: 02-15 21:00 p=0.46 m=0.40 a=BUY_YES s=$120"}
    ctx = _make_tick_ctx(memory_by_market=memory)

    search_results = {
        "search": StageResult(
            stage_name="search", success=True,
            data={"summaries": {"m1": {"summary": "test", "key_points": [], "open_questions": []}}},
        )
    }
    stage.execute(ctx, search_results)

    messages = llm.generate_json.call_args[0][0]
    user_msg = messages[1].content
    assert "RECENT MEMORY:" in user_msg
    assert "p=0.46" in user_msg
    assert "a=BUY_YES" in user_msg


def test_action_stage_includes_per_market_memory():
    """ActionStage injects per-market memory into the user prompt."""
    llm = MagicMock()
    llm.generate_json.return_value = {"recommendation": "HOLD", "size_usd": 0, "rationale": "no edge"}
    stage = ActionStage(llm_client=llm)

    memory = {"m1": "Recent history for m1: 02-15 21:00 p=0.46 m=0.40 a=BUY_YES s=$120"}
    ctx = _make_tick_ctx(memory_by_market=memory)

    forecast_results = {
        "forecast": StageResult(
            stage_name="forecast", success=True,
            data={"forecasts": {"m1": {"p_yes": 0.50, "rationale": "test"}}},
        )
    }
    stage.execute(ctx, forecast_results)

    messages = llm.generate_json.call_args[0][0]
    user_msg = messages[1].content
    assert "RECENT MEMORY:" in user_msg
    assert "p=0.46" in user_msg
