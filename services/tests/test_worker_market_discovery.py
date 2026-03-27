from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.worker.main import fetch_kalshi_markets


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
