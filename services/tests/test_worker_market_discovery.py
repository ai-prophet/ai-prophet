from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder
from db_models import TradingMarket, TradingPosition
from services.worker.main import (
    _is_excluded_market,
    _mark_market_resolved,
    fetch_kalshi_markets,
    get_peer_tickers,
    purge_excluded_tracked_markets,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, payloads: list[dict]):
        self._payloads = payloads
        self.calls = 0

    def get(self, url: str, headers: dict | None = None, params: dict | None = None, timeout: int | None = None):
        payload = self._payloads[self.calls]
        self.calls += 1
        return _FakeResponse(payload)


class _FakeAdapter:
    def __init__(self, payloads: list[dict]):
        self._base_url = "https://api.elections.kalshi.com"
        self._timeout = 30
        self._session = _FakeSession(payloads)

    def _sign_request(self, method: str, path: str) -> dict:
        return {}


def _market_payload(ticker: str, *, close_days: int = 5, yes_ask: str = "0.42", no_ask: str = "0.58") -> dict:
    close_time = (datetime.now(UTC) + timedelta(days=close_days)).isoformat().replace("+00:00", "Z")
    return {
        "ticker": ticker,
        "status": "open",
        "close_time": close_time,
        "yes_ask_dollars": yes_ask,
        "no_ask_dollars": no_ask,
        "yes_bid_dollars": "0.40",
        "no_bid_dollars": "0.56",
        "last_price_dollars": yes_ask,
        "event_ticker": f"EV-{ticker}",
        "yes_sub_title": "",
        "rules_primary": "",
        "open_time": close_time,
        "volume_24h_fp": "123.00",
    }


def _page_with_market(ticker: str, *, cursor: str = "") -> dict:
    return {
        "events": [
            {
                "title": f"Event {ticker}",
                "category": "Politics",
                "markets": [_market_payload(ticker)],
            }
        ],
        "cursor": cursor,
    }


def test_fetch_kalshi_markets_keeps_paging_until_late_candidates_are_found():
    late_page_payloads = []
    for page_num in range(10):
        late_page_payloads.append(
            {
                "events": [
                    {
                        "title": f"Future Event {page_num}",
                        "category": "World",
                        "markets": [_market_payload(f"TOO-LATE-{page_num}", close_days=30)],
                    }
                ],
                "cursor": f"cursor-{page_num + 1}",
            }
        )

    late_page_payloads.append(_page_with_market("LATE-ONE", cursor="cursor-11"))
    late_page_payloads.append(_page_with_market("LATE-TWO"))

    adapter = _FakeAdapter(late_page_payloads)

    markets = fetch_kalshi_markets(adapter, max_markets=5)

    assert [m["ticker"] for m in markets] == ["LATE-ONE", "LATE-TWO"]
    assert adapter._session.calls == 12


def test_fetch_kalshi_markets_dedupes_duplicate_tickers_across_pages():
    adapter = _FakeAdapter(
        [
            _page_with_market("DUP-ONE", cursor="cursor-1"),
            _page_with_market("DUP-ONE", cursor="cursor-2"),
            _page_with_market("DUP-TWO"),
        ]
    )

    markets = fetch_kalshi_markets(adapter, max_markets=10)

    assert [m["ticker"] for m in markets] == ["DUP-ONE", "DUP-TWO"]


def test_fetch_kalshi_markets_skips_mentions_events():
    adapter = _FakeAdapter(
        [
            {
                "events": [
                    {
                        "title": "Mention Event",
                        "category": "MENTIONS",
                        "markets": [_market_payload("MENTION-ONE")],
                    },
                    {
                        "title": "Normal Event",
                        "category": "Politics",
                        "markets": [_market_payload("REAL-ONE")],
                    },
                ],
                "cursor": "",
            }
        ]
    )

    markets = fetch_kalshi_markets(adapter, max_markets=10)

    assert [m["ticker"] for m in markets] == ["REAL-ONE"]


def test_fetch_kalshi_markets_skips_mentions_ticker_even_when_category_is_sports():
    adapter = _FakeAdapter(
        [
            {
                "events": [
                    {
                        "title": "College Basketball",
                        "ticker": "EV-MSUCONN",
                        "category": "Sports",
                        "markets": [
                            _market_payload("KXNCAABMENTION-26MAR28MSUCONN-DOUB"),
                            _market_payload("REAL-SPORTS-ONE"),
                        ],
                    },
                ],
                "cursor": "",
            }
        ]
    )

    markets = fetch_kalshi_markets(adapter, max_markets=10)

    assert [m["ticker"] for m in markets] == ["REAL-SPORTS-ONE"]


def test_excluded_market_helper_catches_mentions_anywhere():
    assert _is_excluded_market(category="MENTIONS") is True
    assert _is_excluded_market(category="Sports", ticker="KXNCAABMENTION-26MAR28MSUCONN-DOUB") is True
    assert _is_excluded_market(category="Sports", event_ticker="KXNCAABMENTION-26MAR28MSUCONN") is True
    assert _is_excluded_market(category="Sports", title="Normal title") is False


def test_purge_excluded_tracked_markets_removes_mentions_rows():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add_all(
            [
                TradingMarket(
                    instance_name="Haifeng",
                    market_id="kalshi:MENTION-ONE",
                    ticker="KXNCAABMENTION-26MAR28MSUCONN-DOUB",
                    event_ticker="KXNCAABMENTION-26MAR28MSUCONN",
                    title="Mention row",
                    category="Sports",
                    last_price=0.5,
                    yes_bid=0.49,
                    yes_ask=0.5,
                    no_bid=0.5,
                    no_ask=0.51,
                    volume_24h=100,
                    updated_at=datetime.now(UTC),
                ),
                TradingMarket(
                    instance_name="Haifeng",
                    market_id="kalshi:REAL-ONE",
                    ticker="REAL-ONE",
                    event_ticker="EV-REAL",
                    title="Real row",
                    category="Politics",
                    last_price=0.5,
                    yes_bid=0.49,
                    yes_ask=0.5,
                    no_bid=0.5,
                    no_ask=0.51,
                    volume_24h=100,
                    updated_at=datetime.now(UTC),
                ),
            ]
        )

    removed = purge_excluded_tracked_markets(engine, "Haifeng")

    assert removed == 1
    with get_session(engine) as session:
        tickers = [row[0] for row in session.query(TradingMarket.ticker).all()]
    assert tickers == ["REAL-ONE"]


def test_get_peer_tickers_excludes_mentions_and_preserves_recent_order():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    earlier = datetime(2026, 3, 27, 0, 0, tzinfo=UTC)
    later = datetime(2026, 3, 27, 1, 0, tzinfo=UTC)
    with get_session(engine) as session:
        session.add_all(
            [
                TradingMarket(
                    instance_name="Haifeng",
                    market_id="kalshi:OLD-REAL",
                    ticker="OLD-REAL",
                    event_ticker="EV-OLD-REAL",
                    title="Old real",
                    category="Politics",
                    last_price=0.5,
                    yes_bid=0.49,
                    yes_ask=0.5,
                    no_bid=0.5,
                    no_ask=0.51,
                    volume_24h=10,
                    updated_at=earlier,
                ),
                TradingMarket(
                    instance_name="Haifeng",
                    market_id="kalshi:MENTION-ONE",
                    ticker="KXNCAABMENTION-26MAR28MSUCONN-DOUB",
                    event_ticker="KXNCAABMENTION-26MAR28MSUCONN",
                    title="Mention",
                    category="Sports",
                    last_price=0.5,
                    yes_bid=0.49,
                    yes_ask=0.5,
                    no_bid=0.5,
                    no_ask=0.51,
                    volume_24h=10,
                    updated_at=later,
                ),
                TradingMarket(
                    instance_name="Haifeng",
                    market_id="kalshi:NEW-REAL",
                    ticker="NEW-REAL",
                    event_ticker="EV-NEW-REAL",
                    title="New real",
                    category="World",
                    last_price=0.5,
                    yes_bid=0.49,
                    yes_ask=0.5,
                    no_bid=0.5,
                    no_ask=0.51,
                    volume_24h=10,
                    updated_at=later,
                ),
            ]
        )

    assert get_peer_tickers(engine, "Haifeng") == ["NEW-REAL", "OLD-REAL"]


def test_mark_market_resolved_updates_all_instances_and_logs_settlement_orders():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)

    with get_session(engine) as session:
        session.add_all(
            [
                TradingMarket(
                    instance_name="Haifeng",
                    market_id="kalshi:RES-SETTLE",
                    ticker="RES-SETTLE",
                    event_ticker="EV-RES-SETTLE",
                    title="Resolved settle market",
                    category="Politics",
                    expiration=now - timedelta(hours=1),
                    last_price=0.42,
                    yes_bid=0.41,
                    yes_ask=0.42,
                    no_bid=0.58,
                    no_ask=0.59,
                    volume_24h=100.0,
                    updated_at=now,
                ),
                TradingMarket(
                    instance_name="Jibang",
                    market_id="kalshi:RES-SETTLE",
                    ticker="RES-SETTLE",
                    event_ticker="EV-RES-SETTLE",
                    title="Resolved settle market",
                    category="Politics",
                    expiration=now - timedelta(hours=1),
                    last_price=0.42,
                    yes_bid=0.41,
                    yes_ask=0.42,
                    no_bid=0.58,
                    no_ask=0.59,
                    volume_24h=100.0,
                    updated_at=now,
                ),
                TradingPosition(
                    instance_name="Haifeng",
                    market_id="kalshi:RES-SETTLE",
                    contract="yes",
                    quantity=4.0,
                    avg_price=0.25,
                    realized_pnl=0.0,
                    unrealized_pnl=0.0,
                    max_position=4.0,
                    realized_trades=0,
                    updated_at=now,
                ),
                TradingPosition(
                    instance_name="Jibang",
                    market_id="kalshi:RES-SETTLE",
                    contract="no",
                    quantity=3.0,
                    avg_price=0.60,
                    realized_pnl=0.0,
                    unrealized_pnl=0.0,
                    max_position=3.0,
                    realized_trades=0,
                    updated_at=now,
                ),
            ]
        )

    adapter = _FakeAdapter([{"market": {"result": "yes"}}])
    _mark_market_resolved(engine, adapter, "RES-SETTLE")

    with get_session(engine) as session:
        markets = (
            session.query(TradingMarket)
            .filter(TradingMarket.market_id == "kalshi:RES-SETTLE")
            .order_by(TradingMarket.instance_name.asc())
            .all()
        )
        positions = (
            session.query(TradingPosition)
            .filter(TradingPosition.market_id == "kalshi:RES-SETTLE")
            .order_by(TradingPosition.instance_name.asc())
            .all()
        )
        orders = (
            session.query(BettingOrder)
            .filter(BettingOrder.ticker == "RES-SETTLE")
            .order_by(BettingOrder.instance_name.asc())
            .all()
        )

    assert [row.last_price for row in markets] == [1.0, 1.0]
    assert [row.quantity for row in positions] == [0.0, 0.0]
    assert [round(row.realized_pnl, 4) for row in positions] == [3.0, -1.8]
    assert [row.status for row in orders] == ["SETTLED", "SETTLED"]
    assert [row.action for row in orders] == ["SELL", "SELL"]
    assert [row.side for row in orders] == ["YES", "NO"]
    assert [row.count for row in orders] == [4, 3]
    assert [row.filled_shares for row in orders] == [4.0, 3.0]
    assert [row.fill_price for row in orders] == [1.0, 0.0]
