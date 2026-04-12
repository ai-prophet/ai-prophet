"""Prophet Arena MCP Server.

Exposes the Core API as MCP tools so any MCP-compatible client
(Claude Desktop, Cursor, etc.) can run experiments and trade
on prediction markets through natural language.
"""

from __future__ import annotations

import atexit
import json
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from fastmcp import FastMCP

from .client import ServerAPIClient
from .client_models import TradeIntentRequest

load_dotenv()

DEFAULT_API_URL = "https://ai-prophet-core-api-998105805337.us-central1.run.app"

mcp = FastMCP(
    "Prophet Arena",
    instructions=(
        "You are connected to Prophet Arena, a platform for trading on real "
        "prediction markets. There are two modes:\n\n"
        "BENCHMARK MODE (evaluating models): health_check -> create_experiment "
        "-> add_participant -> claim_tick -> get_markets -> submit_trades -> "
        "finalize_tick -> (repeat). Each tick is a 15-minute decision window.\n\n"
        "AGENT MODE (quick exploration): get_current_markets to browse markets, "
        "forecast_to_trade to bet from a probability, place_trade for direct "
        "execution, submit_forecast for the leaderboard."
    ),
)

_lease_owner = str(uuid.uuid4())
_betting_engine = None


def _get_client() -> ServerAPIClient:
    return ServerAPIClient(
        base_url=os.getenv("PA_SERVER_URL", DEFAULT_API_URL),
        api_key=os.getenv("PA_SERVER_API_KEY"),
    )


def _model_to_dict(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return dict(obj)


def _close_betting_engine() -> None:
    global _betting_engine

    engine = _betting_engine
    if engine is None:
        return

    close = getattr(engine, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
    _betting_engine = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@mcp.tool
def health_check() -> dict:
    """Check if the Prophet Arena API is reachable. Call this first."""
    with _get_client() as api:
        return _model_to_dict(api.health_check())


# ---------------------------------------------------------------------------
# Experiment setup
# ---------------------------------------------------------------------------

@mcp.tool
def create_experiment(
    slug: str,
    n_ticks: int = 24,
    config_description: str = "",
) -> dict:
    """Create a new experiment (or resume an existing one by slug).

    Args:
        slug: Unique name for the experiment. Reuse to resume a stopped run.
        n_ticks: How many ticks to run. Each tick = 15 min. 24 ticks = 6 hours.
        config_description: Optional free-text description of your strategy.
    """
    with _get_client() as api:
        resp = api.create_or_get_experiment(
            slug=slug,
            config_hash=f"mcp-{slug}",
            config_json={"source": "mcp", "description": config_description},
            n_ticks=n_ticks,
        )
        return _model_to_dict(resp)


@mcp.tool
def add_participant(
    experiment_id: str,
    model_name: str = "mcp:interactive",
    starting_cash: float = 10000.0,
) -> dict:
    """Register a trading agent in an experiment.

    Args:
        experiment_id: From create_experiment.
        model_name: Label for this agent (e.g. "mcp:my-strategy").
        starting_cash: Starting balance in USD.
    """
    with _get_client() as api:
        resp = api.upsert_participant(
            experiment_id, model=model_name, rep=0, starting_cash=starting_cash,
        )
        return _model_to_dict(resp)


@mcp.tool
def get_progress(experiment_id: str) -> dict:
    """Check how many ticks are completed, in-progress, or remaining.

    Args:
        experiment_id: From create_experiment.
    """
    with _get_client() as api:
        return _model_to_dict(api.get_progress(experiment_id))


# ---------------------------------------------------------------------------
# Tick lifecycle
# ---------------------------------------------------------------------------

@mcp.tool
def claim_tick(experiment_id: str) -> dict:
    """Claim the next available tick. Returns tick_id and candidate_set_id.

    If no tick is available, returns no_tick_available=true with a reason.
    If reason is "experiment_completed", the experiment is done.

    Args:
        experiment_id: From create_experiment.
    """
    with _get_client() as api:
        resp = api.claim_tick(experiment_id, _lease_owner)
        result = {
            "tick_id": resp.tick_id,
            "candidate_set_id": resp.candidate_set_id,
            "lease_expires_at": resp.lease_expires_at,
            "reclaim_count": resp.reclaim_count,
            "no_tick_available": resp.no_tick_available,
            "retry_after_sec": resp.retry_after_sec,
            "reason": resp.reason,
        }
        result = {key: value for key, value in result.items() if value is not None}
        return result


@mcp.tool
def get_markets(tick_ts: str, candidate_set_id: str | None = None) -> dict:
    """Get candidate prediction markets for a tick.

    Returns up to 256 live markets with current bid/ask prices.
    Use the tick_ts and candidate_set_id from claim_tick.

    Args:
        tick_ts: ISO timestamp from claim_tick (e.g. "2026-03-16T09:30:00+00:00").
        candidate_set_id: Candidate set ID from claim_tick.
    """
    with _get_client() as api:
        ts = datetime.fromisoformat(tick_ts)
        resp = api.get_candidates(ts, candidate_set_id)
        markets = []
        for m in resp.markets:
            markets.append({
                "market_id": m.market_id,
                "question": m.question,
                "description": m.description,
                "resolution_time": m.resolution_time.isoformat(),
                "topic": m.topic,
                "best_bid": m.quote.best_bid,
                "best_ask": m.quote.best_ask,
                "volume_24h": m.quote.volume_24h,
            })
        return {
            "candidate_set_id": resp.candidate_set_id,
            "market_count": resp.market_count,
            "markets": markets,
        }


@mcp.tool
def submit_trades(
    experiment_id: str,
    participant_idx: int,
    tick_id: str,
    candidate_set_id: str,
    trades: list[dict],
) -> dict:
    """Submit trade intents for a tick.

    Each trade is a dict with: market_id, action (BUY/SELL), side (YES/NO),
    amount (dollar amount as string, e.g. "100").

    Trades fill at the snapshot's best bid/ask. Rejected trades are returned
    with a reason (e.g. constraint violation).

    Args:
        experiment_id: From create_experiment.
        participant_idx: From add_participant.
        tick_id: From claim_tick.
        candidate_set_id: From get_markets.
        trades: List of trade dicts. Each needs: market_id, action, side, amount.
    """
    intents = []
    for i, t in enumerate(trades):
        intents.append(TradeIntentRequest(
            market_id=t["market_id"],
            action=t["action"],
            side=t["side"],
            shares=str(t.get("amount", t.get("shares", "100"))),
            idempotency_key=f"{experiment_id}:{participant_idx}:{tick_id}:{i}",
        ))

    with _get_client() as api:
        resp = api.submit_trade_intents(
            experiment_id, participant_idx, tick_id, candidate_set_id, intents,
        )
        return _model_to_dict(resp)


@mcp.tool
def finalize_tick(
    experiment_id: str,
    participant_idx: int,
    tick_id: str,
) -> dict:
    """Finalize a participant and complete the tick. Call after submitting trades (or deciding to skip).

    Args:
        experiment_id: From create_experiment.
        participant_idx: From add_participant.
        tick_id: From claim_tick.
    """
    with _get_client() as api:
        api.finalize_participant(
            experiment_id, participant_idx, tick_id, status="COMPLETED",
        )
        resp = api.complete_tick(experiment_id, tick_id)
        return _model_to_dict(resp)


# ---------------------------------------------------------------------------
# Portfolio and reasoning
# ---------------------------------------------------------------------------

@mcp.tool
def get_portfolio(experiment_id: str, participant_idx: int) -> dict:
    """Get the current portfolio: cash, equity, and open positions.

    Args:
        experiment_id: From create_experiment.
        participant_idx: From add_participant.
    """
    with _get_client() as api:
        resp = api.get_portfolio(experiment_id, participant_idx)
        if resp is None:
            return {"status": "no_portfolio", "detail": "No trades yet."}
        return _model_to_dict(resp)


@mcp.tool
def get_reasoning(
    experiment_id: str,
    participant_idx: int | None = None,
    limit: int = 20,
) -> dict:
    """Get previously submitted reasoning/plans.

    Args:
        experiment_id: From create_experiment.
        participant_idx: Filter to a specific participant (optional).
        limit: Max entries to return.
    """
    with _get_client() as api:
        resp = api.get_reasoning(experiment_id, participant_idx, limit)
        return _model_to_dict(resp)


# ---------------------------------------------------------------------------
# Agent-builder tools (no benchmark tick required)
# ---------------------------------------------------------------------------

@mcp.tool
def get_current_markets() -> dict:
    """Fetch current prediction markets without creating an experiment.

    Returns Prophet Arena's curated market universe: liquid, tradeable
    markets filtered by volume, quote freshness, and time to resolution.
    This is NOT every market on every exchange.
    """
    with _get_client() as api:
        resp = api.get_market_snapshot()
        requested_as_of_ts = resp.requested_asof_ts.isoformat()
        data_as_of_ts = resp.data_asof_ts.isoformat()
        markets = []
        for m in resp.markets:
            markets.append({
                "market_id": m.market_id,
                "question": m.question,
                "description": m.description,
                "resolution_time": m.resolution_time.isoformat(),
                "topic": m.topic,
                "best_bid": m.quote.best_bid,
                "best_ask": m.quote.best_ask,
                "volume_24h": m.quote.volume_24h,
            })
        return {
            "candidate_set_id": resp.candidate_set_id,
            "requested_as_of_ts": requested_as_of_ts,
            "data_as_of_ts": data_as_of_ts,
            "market_count": resp.market_count,
            "markets": markets,
        }


@mcp.tool
def submit_forecast(predictions: list[dict]) -> dict:
    """Submit probability predictions to the forecast leaderboard.

    Each prediction needs: market_ticker, p_yes (0-1), rationale (optional).
    Team is resolved from the API key.
    """
    with _get_client() as api:
        return _model_to_dict(api.submit_forecast(predictions))


# ---------------------------------------------------------------------------
# Live betting tools (uses BettingEngine)
# ---------------------------------------------------------------------------

def _get_betting_engine():
    """Lazy-create a BettingEngine from env vars."""
    global _betting_engine

    if _betting_engine is not None:
        return _betting_engine

    from .betting import BettingEngine, LiveBettingSettings
    from .betting.db import create_db_engine

    settings = LiveBettingSettings.from_env()
    db_engine = create_db_engine() if settings.enabled else None
    _betting_engine = BettingEngine(
        db_engine=db_engine,
        paper=settings.paper,
        kalshi_config=settings.kalshi,
        enabled=settings.enabled,
    )
    return _betting_engine


def _bet_result_to_dict(result) -> dict:
    d: dict = {
        "market_id": result.market_id,
        "order_placed": result.order_placed,
    }
    if result.signal:
        d["side"] = result.signal.side
        d["shares"] = result.signal.shares
        d["price"] = result.signal.price
    if result.status:
        d["status"] = result.status
    if result.error:
        d["error"] = result.error
    return d


def _trade_status_response(market_id: str, *, status: str, reason: str) -> dict:
    return {
        "market_id": market_id,
        "order_placed": False,
        "status": status,
        "reason": reason,
    }


@mcp.tool
def forecast_to_trade(
    market_id: str,
    p_yes: float,
    yes_ask: float,
    no_ask: float,
) -> dict:
    """Place a bet based on a probability forecast.

    The betting strategy decides side and size. Routes to paper trade
    or live Kalshi based on engine config.

    Args:
        market_id: Market identifier (e.g. "kalshi:NASDAQ-100-GT5K").
        p_yes: Your probability estimate that YES resolves (0-1).
        yes_ask: Current ask price for YES contracts (0-1).
        no_ask: Current ask price for NO contracts (0-1).
    """
    engine = _get_betting_engine()
    if not engine.enabled:
        return _trade_status_response(
            market_id,
            status="DISABLED",
            reason="betting engine disabled",
        )
    result = engine.trade_from_forecast(
        market_id=market_id, p_yes=p_yes, yes_ask=yes_ask, no_ask=no_ask,
    )
    if result is None:
        return _trade_status_response(
            market_id,
            status="SKIP",
            reason="strategy passed",
        )
    return _bet_result_to_dict(result)


@mcp.tool
def place_trade(
    market_id: str,
    side: str,
    shares: int,
    price: float,
) -> dict:
    """Place a trade directly, bypassing strategy evaluation.

    Routes to paper trade or live Kalshi based on engine config.

    Args:
        market_id: Market identifier (e.g. "kalshi:NASDAQ-100-GT5K").
        side: "yes" or "no".
        shares: Number of contracts.
        price: Limit price (0-1).
    """
    engine = _get_betting_engine()
    if not engine.enabled:
        return _trade_status_response(
            market_id,
            status="DISABLED",
            reason="betting engine disabled",
        )
    result = engine.make_trade(
        market_id=market_id, side=side, shares=shares, price=price,
    )
    return _bet_result_to_dict(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


atexit.register(_close_betting_engine)


if __name__ == "__main__":
    main()
