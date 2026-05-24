"""Safety tests for the three secret-weapon features.

* ``fast_resolve`` — smart routing for already-settled Sports events
* ``market_consensus_estimate`` — Polymarket signal
* ``_research_was_weak`` / ``_rebalance_on_weak_research`` — dynamic
  confidence scaling when research is thin
"""

from __future__ import annotations

import httpx
import pytest
from ai_prophet.forecast import ensemble_agent, llm_utils, market_signal
from ai_prophet.forecast.ensemble_agent import (
    _rebalance_on_weak_research,
    _research_was_weak,
)
from ai_prophet.forecast.llm_utils import (
    _research_has_result_signal,
    fast_resolve,
)
from ai_prophet.forecast.market_signal import (
    _jaccard,
    _kalshi_yes_price_cents,
    _normalize,
    _yes_price_from_market,
    kalshi_price_estimate,
    market_consensus_estimate,
    market_signal_estimate,
    query_polymarket,
)
from ai_prophet.forecast.strategies.base import Estimate

# ---------------------------------------------------------------------------
# fast_resolve — pre-filter
# ---------------------------------------------------------------------------


def test_result_signal_detects_score_pattern() -> None:
    assert _research_has_result_signal("Final 115-94 in Cleveland's favor")
    assert _research_has_result_signal("Detroit 3 - 1 Cleveland")


def test_result_signal_detects_keywords() -> None:
    assert _research_has_result_signal("Cleveland won the game decisively")
    assert _research_has_result_signal("Detroit beat Cleveland 115-94")


def test_result_signal_returns_false_on_empty_or_plain_text() -> None:
    assert _research_has_result_signal("") is False
    assert _research_has_result_signal(None) is False
    assert _research_has_result_signal("Upcoming match, no result yet") is False


# ---------------------------------------------------------------------------
# fast_resolve — main paths
# ---------------------------------------------------------------------------


class _FakeEvent:
    def __init__(self, title, category, outcomes=None):
        self.title = title
        self.category = category
        self.outcomes = outcomes


def test_fast_resolve_skips_non_sports() -> None:
    event = _FakeEvent("Will the Fed cut rates?", "Economics", ["Cut", "Hold"])
    assert fast_resolve(event, "Cut won the day, 5 - 2 vote") is None


def test_fast_resolve_skips_when_no_result_signal() -> None:
    event = _FakeEvent("Will Lakers beat Thunder?", "Sports", ["Lakers", "Thunder"])
    assert fast_resolve(event, "Game scheduled for tomorrow") is None
    assert fast_resolve(event, "") is None


def test_fast_resolve_returns_estimate_when_settled(monkeypatch) -> None:
    event = _FakeEvent(
        "Will Detroit beat Cleveland in Game 6?",
        "Sports",
        ["Detroit", "Cleveland"],
    )
    monkeypatch.setattr(
        llm_utils,
        "call_llm_json",
        lambda *_a, **_kw: {
            "settled": True,
            "p_yes": 0.97,
            "rationale": "Detroit 115-94 over Cleveland in Game 6.",
        },
    )
    est = fast_resolve(event, "Detroit 115-94 Cleveland, Detroit won Game 6.")
    assert est is not None
    assert est.strategy == "fast_resolve"
    assert est.confidence == pytest.approx(0.95)
    assert est.p_yes > 0.9
    assert "Detroit" in est.rationale


def test_fast_resolve_returns_none_when_llm_uncertain(monkeypatch) -> None:
    event = _FakeEvent("Will A beat B?", "Sports", ["A", "B"])
    monkeypatch.setattr(
        llm_utils,
        "call_llm_json",
        lambda *_a, **_kw: {
            "settled": False,
            "p_yes": 0.5,
            "rationale": "Not the same match.",
        },
    )
    assert fast_resolve(event, "Some other result 3-1") is None


def test_fast_resolve_returns_none_when_llm_in_middle(monkeypatch) -> None:
    """Even if settled=true, p_yes near 0.5 is treated as ambiguous."""
    event = _FakeEvent("Will A beat B?", "Sports", ["A", "B"])
    monkeypatch.setattr(
        llm_utils,
        "call_llm_json",
        lambda *_a, **_kw: {"settled": True, "p_yes": 0.5, "rationale": "Tied"},
    )
    assert fast_resolve(event, "A drew B 2-2") is None


def test_fast_resolve_returns_none_on_llm_failure(monkeypatch) -> None:
    event = _FakeEvent("Will A beat B?", "Sports", ["A", "B"])

    def boom(*_a, **_kw):
        raise llm_utils.LLMError("provider exhausted")

    monkeypatch.setattr(llm_utils, "call_llm_json", boom)
    assert fast_resolve(event, "A beat B 3-1") is None


def test_fast_resolve_returns_none_on_bad_payload(monkeypatch) -> None:
    event = _FakeEvent("Will A beat B?", "Sports", ["A", "B"])
    monkeypatch.setattr(
        llm_utils, "call_llm_json", lambda *_a, **_kw: {"settled": True}
    )
    assert fast_resolve(event, "A beat B 3-1") is None


# ---------------------------------------------------------------------------
# market_signal — text matching helpers
# ---------------------------------------------------------------------------


def test_normalize_drops_stopwords_and_short_tokens() -> None:
    out = _normalize("Will the Lakers beat the Thunder in NBA Game 6?")
    assert "lakers" in out
    assert "thunder" in out
    assert "the" not in out
    assert "in" not in out


def test_jaccard_identical_titles_is_one() -> None:
    a = _normalize("Cleveland beats Detroit Game 6")
    assert _jaccard(a, a) == pytest.approx(1.0)


def test_jaccard_disjoint_is_zero() -> None:
    a = _normalize("Cleveland beats Detroit")
    b = _normalize("Bitcoin reaches new high")
    assert _jaccard(a, b) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# market_signal — yes_price extraction
# ---------------------------------------------------------------------------


def test_yes_price_from_tokens_shape() -> None:
    market = {"tokens": [{"outcome": "Yes", "price": "0.62"}, {"outcome": "No", "price": "0.38"}]}
    assert _yes_price_from_market(market) == pytest.approx(0.62)


def test_yes_price_from_outcome_prices_shape() -> None:
    market = {"outcome_prices": ["0.71", "0.29"]}
    assert _yes_price_from_market(market) == pytest.approx(0.71)


def test_yes_price_from_last_trade_shape() -> None:
    market = {"lastTradePrice": 0.55}
    assert _yes_price_from_market(market) == pytest.approx(0.55)


def test_yes_price_returns_none_when_missing() -> None:
    assert _yes_price_from_market({}) is None
    assert _yes_price_from_market({"tokens": []}) is None


# ---------------------------------------------------------------------------
# market_signal — query path (HTTP mocked)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, *_args, **_kwargs):
        return self._response


def test_query_polymarket_returns_none_on_empty_title() -> None:
    assert query_polymarket("") is None
    assert query_polymarket(None) is None  # type: ignore[arg-type]


def test_query_polymarket_returns_none_on_http_error(monkeypatch) -> None:
    def fake_client(*_a, **_kw):
        return _FakeClient(_FakeResponse({}, status_code=500))

    monkeypatch.setattr(market_signal.httpx, "Client", fake_client)
    assert query_polymarket("Will Lakers beat Thunder?") is None


def test_query_polymarket_returns_none_on_empty_list(monkeypatch) -> None:
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse({"data": []})),
    )
    assert query_polymarket("Will Lakers beat Thunder?") is None


def test_query_polymarket_finds_matching_market(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "question": "Will the Lakers defeat the Thunder in NBA Game 6?",
                "tokens": [
                    {"outcome": "Yes", "price": "0.40"},
                    {"outcome": "No", "price": "0.60"},
                ],
            },
            {
                "question": "Will Bitcoin exceed $200,000?",
                "tokens": [
                    {"outcome": "Yes", "price": "0.05"},
                    {"outcome": "No", "price": "0.95"},
                ],
            },
        ]
    }
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse(payload)),
    )
    result = query_polymarket("Will Lakers beat Thunder in Game 6?")
    assert result is not None
    assert "Lakers" in result["question"]


def test_market_consensus_estimate_returns_well_formed_estimate(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "question": "Will the Lakers defeat the Thunder in NBA Game 6?",
                "tokens": [
                    {"outcome": "Yes", "price": "0.42"},
                    {"outcome": "No", "price": "0.58"},
                ],
            }
        ]
    }
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse(payload)),
    )
    est = market_consensus_estimate("Will Lakers beat Thunder Game 6?")
    assert est is not None
    assert est.strategy == "market_consensus"
    assert est.confidence == pytest.approx(0.65)
    assert est.p_yes == pytest.approx(0.42)
    assert "Polymarket" in est.rationale


def test_market_consensus_estimate_returns_none_on_no_match(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "question": "Will Trump be impeached?",
                "tokens": [{"outcome": "Yes", "price": "0.10"}],
            }
        ]
    }
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse(payload)),
    )
    assert market_consensus_estimate("Will Bitcoin reach $300k?") is None


# ---------------------------------------------------------------------------
# Dynamic confidence scaling
# ---------------------------------------------------------------------------


def _est(strategy: str, conf: float = 0.7, p: float = 0.5) -> Estimate:
    return Estimate(p_yes=p, rationale="r", strategy=strategy, confidence=conf)


def test_research_was_weak_triggers_on_short_brief() -> None:
    short = "x" * 100
    estimates = [_est("evidence_weighted"), _est("base_rate")]
    assert _research_was_weak(short, estimates) is True


def test_research_was_weak_triggers_on_evidence_phrase() -> None:
    long_brief = "x" * 2000
    estimates = [
        Estimate(
            p_yes=0.5,
            rationale="No web evidence found; defaulting near 0.5.",
            strategy="evidence_weighted",
            confidence=0.4,
        ),
        _est("base_rate"),
    ]
    assert _research_was_weak(long_brief, estimates) is True


def test_research_was_weak_returns_false_for_normal_run() -> None:
    long_brief = "x" * 2000
    estimates = [
        Estimate(
            p_yes=0.7,
            rationale="Multiple sources align on Cleveland advancing.",
            strategy="evidence_weighted",
            confidence=0.75,
        ),
        _est("base_rate"),
    ]
    assert _research_was_weak(long_brief, estimates) is False


def test_rebalance_caps_evidence_and_boosts_base_rate() -> None:
    estimates = [
        _est("evidence_weighted", conf=0.8),
        _est("base_rate", conf=0.5),
        _est("contrarian", conf=0.6),
        _est("market_consensus", conf=0.65),
    ]
    rebalanced = _rebalance_on_weak_research(estimates)
    by_name = {e.strategy: e for e in rebalanced}
    assert by_name["evidence_weighted"].confidence == pytest.approx(0.3)
    assert by_name["base_rate"].confidence == pytest.approx(0.7)
    # Other strategies are left alone.
    assert by_name["contrarian"].confidence == pytest.approx(0.6)
    assert by_name["market_consensus"].confidence == pytest.approx(0.65)
    # Originals were not mutated (dataclasses.replace, not in-place).
    assert estimates[0].confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# forecast_event integration: fast-resolve short-circuit
# ---------------------------------------------------------------------------


def test_forecast_event_uses_fast_resolve_for_sports(monkeypatch) -> None:
    """A successful fast_resolve must skip strategies + deliberation."""
    monkeypatch.setenv("ENABLE_DELIBERATION", "false")  # belt and suspenders
    monkeypatch.setattr(ensemble_agent, "research_event", lambda **_kw: "x" * 4000)
    monkeypatch.setattr(
        ensemble_agent,
        "fast_resolve",
        lambda _event, _research: Estimate(
            p_yes=0.97,
            rationale="Detroit 115-94 Cleveland.",
            strategy="fast_resolve",
            confidence=0.95,
        ),
    )

    def explode_strategy(*_a, **_kw):
        raise AssertionError("strategies must not run on fast-resolve")

    monkeypatch.setattr(ensemble_agent, "_run_strategy", explode_strategy)

    event = ensemble_agent.EventRequest(
        title="Will Detroit beat Cleveland?",
        category="Sports",
        outcomes=["Detroit", "Cleveland"],
    )
    final = ensemble_agent.forecast_event(event)
    assert final.p_yes == pytest.approx(0.97)
    assert len(final.estimates) == 1
    assert final.estimates[0].strategy == "fast_resolve"


def test_forecast_event_includes_market_estimate_when_available(monkeypatch) -> None:
    """A non-None market_signal lands in final.estimates alongside strategies."""
    monkeypatch.setenv("ENABLE_DELIBERATION", "false")
    monkeypatch.setattr(ensemble_agent, "research_event", lambda **_kw: "x" * 4000)
    monkeypatch.setattr(ensemble_agent, "fast_resolve", lambda _e, _r: None)

    table = {
        "evidence_weighted": _est("evidence_weighted", conf=0.75, p=0.7),
        "base_rate": _est("base_rate", conf=0.55, p=0.6),
        "contrarian": _est("contrarian", conf=0.5, p=0.65),
    }
    monkeypatch.setattr(
        ensemble_agent,
        "_run_strategy",
        lambda strategy, event, research, temporal_ctx=None: table[strategy.name],
    )
    monkeypatch.setattr(
        ensemble_agent,
        "_market_signal_task",
        lambda _ticker, _title: _est("market_consensus", conf=0.65, p=0.72),
    )

    event = ensemble_agent.EventRequest(title="t", category="Sports", outcomes=["A", "B"])
    final = ensemble_agent.forecast_event(event)
    strategies_in_result = {e.strategy for e in final.estimates}
    assert "market_consensus" in strategies_in_result


def test_forecast_event_omits_market_estimate_when_none(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_DELIBERATION", "false")
    monkeypatch.setattr(ensemble_agent, "research_event", lambda **_kw: "x" * 4000)
    monkeypatch.setattr(ensemble_agent, "fast_resolve", lambda _e, _r: None)

    table = {
        "evidence_weighted": _est("evidence_weighted", conf=0.75, p=0.7),
        "base_rate": _est("base_rate", conf=0.55, p=0.6),
        "contrarian": _est("contrarian", conf=0.5, p=0.65),
    }
    monkeypatch.setattr(
        ensemble_agent,
        "_run_strategy",
        lambda strategy, event, research, temporal_ctx=None: table[strategy.name],
    )
    monkeypatch.setattr(ensemble_agent, "_market_signal_task", lambda _ticker, _title: None)

    event = ensemble_agent.EventRequest(title="t", category="Sports", outcomes=["A", "B"])
    final = ensemble_agent.forecast_event(event)
    strategies_in_result = {e.strategy for e in final.estimates}
    assert "market_consensus" not in strategies_in_result


# ---------------------------------------------------------------------------
# Kalshi: yes-price extraction
# ---------------------------------------------------------------------------


def test_kalshi_yes_price_prefers_last_price() -> None:
    market = {"last_price": 42, "yes_bid": 39, "yes_ask": 41}
    assert _kalshi_yes_price_cents(market) == pytest.approx(42.0)


def test_kalshi_yes_price_skips_zero_or_extreme_last() -> None:
    """A last_price of 0 or >=100 falls back to bid/ask."""
    market = {"last_price": 0, "yes_bid": 30, "yes_ask": 36}
    assert _kalshi_yes_price_cents(market) == pytest.approx(33.0)
    market = {"last_price": 100, "yes_bid": 60, "yes_ask": 70}
    assert _kalshi_yes_price_cents(market) == pytest.approx(65.0)


def test_kalshi_yes_price_uses_midpoint_when_no_last() -> None:
    market = {"yes_bid": 25, "yes_ask": 27}
    assert _kalshi_yes_price_cents(market) == pytest.approx(26.0)


def test_kalshi_yes_price_uses_ask_alone() -> None:
    market = {"yes_ask": 55}
    assert _kalshi_yes_price_cents(market) == pytest.approx(55.0)


def test_kalshi_yes_price_uses_bid_alone() -> None:
    market = {"yes_bid": 45}
    assert _kalshi_yes_price_cents(market) == pytest.approx(45.0)


def test_kalshi_yes_price_none_when_no_signal() -> None:
    assert _kalshi_yes_price_cents({}) is None
    assert _kalshi_yes_price_cents({"yes_bid": 0, "yes_ask": 0}) is None


# ---------------------------------------------------------------------------
# kalshi_price_estimate — HTTP-mocked
# ---------------------------------------------------------------------------


def test_kalshi_price_estimate_returns_none_for_empty_ticker() -> None:
    assert kalshi_price_estimate(None) is None
    assert kalshi_price_estimate("") is None
    assert kalshi_price_estimate("   ") is None


def test_kalshi_price_estimate_returns_none_on_404(monkeypatch) -> None:
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse({}, status_code=404)),
    )
    assert kalshi_price_estimate("KX-NONEXISTENT") is None


def test_kalshi_price_estimate_extracts_price_from_wrapped_payload(monkeypatch) -> None:
    """Kalshi wraps the record as {"market": {...}}."""
    payload = {
        "market": {
            "ticker": "KX-LAKERS-WIN",
            "last_price": 62,
            "yes_bid": 60,
            "yes_ask": 64,
            "status": "open",
        }
    }
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse(payload)),
    )
    est = kalshi_price_estimate("KX-LAKERS-WIN", "Will the Lakers win?")
    assert est is not None
    assert est.strategy == "market_price"
    assert est.confidence == pytest.approx(0.65)
    assert est.p_yes == pytest.approx(0.62)
    assert "62c" in est.rationale
    assert "Lakers" in est.rationale  # title preserved for traceability


def test_kalshi_price_estimate_accepts_bare_dict(monkeypatch) -> None:
    """Some endpoints return the market record directly, not wrapped."""
    payload = {"ticker": "KX-X", "last_price": 35, "yes_bid": 30, "yes_ask": 40}
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse(payload)),
    )
    est = kalshi_price_estimate("KX-X")
    assert est is not None
    assert est.p_yes == pytest.approx(0.35)


def test_kalshi_price_estimate_returns_none_on_no_usable_price(monkeypatch) -> None:
    payload = {"market": {"ticker": "KX-DEAD", "status": "closed"}}
    monkeypatch.setattr(
        market_signal.httpx,
        "Client",
        lambda *_a, **_kw: _FakeClient(_FakeResponse(payload)),
    )
    assert kalshi_price_estimate("KX-DEAD") is None


def test_kalshi_price_estimate_strips_whitespace_in_ticker() -> None:
    """A whitespace-only ticker should be treated as empty."""
    assert kalshi_price_estimate("   ") is None


# ---------------------------------------------------------------------------
# kalshi_price_estimate — KALSHI_API_KEY auth handling
# ---------------------------------------------------------------------------


class _CapturingClient:
    """httpx.Client stand-in that records constructor kwargs and request URL."""

    last_kwargs: dict = {}
    last_url: str = ""

    def __init__(self, *_args, **kwargs):
        type(self).last_kwargs = dict(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, url, *_args, **_kwargs):
        type(self).last_url = url
        # Always return a valid Kalshi-shaped response so the rest of the
        # function executes; the test only inspects what was sent.
        return _FakeResponse(
            {"market": {"ticker": "KX-FOO", "last_price": 50}}
        )


def test_kalshi_omits_auth_when_keys_unset(monkeypatch) -> None:
    """With no ``KALSHI_API_KEY_ID``/``KALSHI_PRIVATE_KEY``, the request
    is sent without auth headers and goes to the trading-api host. If
    trading-api returns 401, our ``_kalshi_get`` helper auto-falls back
    to the public elections endpoint (covered by other tests).
    """
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)  # legacy var
    monkeypatch.delenv("KALSHI_API_URL", raising=False)
    monkeypatch.setattr(market_signal.httpx, "Client", _CapturingClient)

    est = kalshi_price_estimate("KX-FOO")
    assert est is not None
    assert _CapturingClient.last_kwargs.get("headers") is None
    assert "trading-api.kalshi.com" in _CapturingClient.last_url


def test_kalshi_treats_whitespace_key_id_as_unset(monkeypatch) -> None:
    """A whitespace-only Key ID means we send no auth headers (RSA
    signing requires a real Key ID)."""
    monkeypatch.setenv("KALSHI_API_KEY_ID", "   ")
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("KALSHI_API_URL", raising=False)
    monkeypatch.setattr(market_signal.httpx, "Client", _CapturingClient)

    est = kalshi_price_estimate("KX-FOO")
    assert est is not None
    assert _CapturingClient.last_kwargs.get("headers") is None


def test_kalshi_signs_request_when_both_keys_set(monkeypatch) -> None:
    """With both ``KALSHI_API_KEY_ID`` and ``KALSHI_PRIVATE_KEY`` (valid
    PEM) configured, the request includes the three RSA-PSS auth
    headers: ``KALSHI-ACCESS-KEY``, ``KALSHI-ACCESS-TIMESTAMP``, and
    ``KALSHI-ACCESS-SIGNATURE``.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate an ephemeral RSA key just for this test.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    # Clear the module-level PEM cache so this test's key is loaded fresh.
    market_signal._PRIVATE_KEY_CACHE.clear()

    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id-abc")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    monkeypatch.delenv("KALSHI_API_URL", raising=False)
    monkeypatch.setattr(market_signal.httpx, "Client", _CapturingClient)

    est = kalshi_price_estimate("KX-FOO")
    assert est is not None
    headers = _CapturingClient.last_kwargs.get("headers") or {}
    assert headers.get("KALSHI-ACCESS-KEY") == "test-key-id-abc"
    assert headers.get("KALSHI-ACCESS-TIMESTAMP") is not None
    assert headers.get("KALSHI-ACCESS-SIGNATURE") is not None
    # Signature is base64-encoded.
    import base64
    sig_raw = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    assert len(sig_raw) == 256  # 2048-bit RSA signature is 256 bytes


def test_kalshi_explicit_url_override_wins(monkeypatch) -> None:
    """``KALSHI_API_URL`` beats the auto-selected URL even when other
    Kalshi-related env vars are set. Auth headers are still attempted
    if Key ID + private key are configured, but the URL is the explicit
    one.
    """
    monkeypatch.setenv("KALSHI_API_URL", "https://my-custom-host.example/v2/markets")
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(market_signal.httpx, "Client", _CapturingClient)

    est = kalshi_price_estimate("KX-FOO")
    assert est is not None
    assert _CapturingClient.last_url == "https://my-custom-host.example/v2/markets/KX-FOO"


# ---------------------------------------------------------------------------
# kalshi_multi_outcome_probabilities — per-outcome market price lookup
# ---------------------------------------------------------------------------


def test_kalshi_multi_outcome_matches_each_outcome_to_a_child_market(
    monkeypatch,
) -> None:
    """For a multi-outcome event, fetch all child markets and align each
    one to the input ``outcomes`` list by token overlap. Each match's
    price becomes that outcome's probability; unmatched outcomes get
    the uninformative 0.5 prior.

    Crucially, the output is INDEPENDENT per the eval admin's
    confirmation — values are not sum-normalized.
    """
    from ai_prophet.forecast.market_signal import (
        kalshi_multi_outcome_probabilities,
    )

    # Stub the child-markets fetch to return 3 fake Kalshi markets, each
    # with a label that aligns to one of the input outcomes.
    fake_markets = [
        {"ticker": "EVT-T80", "yes_sub_title": "BTC above $80k", "last_price": 95},
        {"ticker": "EVT-T90", "yes_sub_title": "BTC above $90k", "last_price": 70},
        {"ticker": "EVT-T100", "yes_sub_title": "BTC above $100k", "last_price": 30},
    ]
    monkeypatch.setattr(
        market_signal, "kalshi_event_markets", lambda _evt: fake_markets
    )

    outcomes = ["BTC above $80k", "BTC above $90k", "BTC above $100k"]
    out = kalshi_multi_outcome_probabilities("EVT-123", outcomes)

    assert out is not None
    assert len(out) == 3
    # Each output entry is an object with the right keys.
    for item in out:
        assert set(item.keys()) == {"market", "probability"}
    # Positional alignment to input outcomes.
    assert [item["market"] for item in out] == outcomes
    # Prices in cents map to probabilities, no sum-normalization.
    assert out[0]["probability"] == pytest.approx(0.95)
    assert out[1]["probability"] == pytest.approx(0.70)
    assert out[2]["probability"] == pytest.approx(0.30)
    # The sum is 1.95 — definitively NOT normalized to 1.
    total = sum(item["probability"] for item in out)
    assert total == pytest.approx(1.95, abs=1e-3)


def test_kalshi_multi_outcome_returns_none_when_no_child_markets(monkeypatch) -> None:
    """If the event has no child markets on Kalshi, return None so the
    caller can fall through to the LLM path."""
    from ai_prophet.forecast.market_signal import (
        kalshi_multi_outcome_probabilities,
    )

    monkeypatch.setattr(market_signal, "kalshi_event_markets", lambda _evt: None)

    out = kalshi_multi_outcome_probabilities("EVT-MISSING", ["A", "B", "C"])
    assert out is None


def test_kalshi_multi_outcome_uses_0_5_prior_for_unmatched_outcomes(
    monkeypatch,
) -> None:
    """If only some outcomes match a child market, the others get the
    0.5 uninformative prior. Match count must be > 0 for the helper to
    return a non-None result (otherwise the LLM fallback should run).
    """
    from ai_prophet.forecast.market_signal import (
        kalshi_multi_outcome_probabilities,
    )

    # Only one child market exists; the input has three outcomes.
    fake_markets = [
        {"ticker": "EVT-A", "subtitle": "Outcome alpha resolves yes", "last_price": 65},
    ]
    monkeypatch.setattr(
        market_signal, "kalshi_event_markets", lambda _evt: fake_markets
    )

    outcomes = ["Outcome alpha resolves yes", "totally unrelated text", "another miss"]
    out = kalshi_multi_outcome_probabilities("EVT-PARTIAL", outcomes)

    assert out is not None
    assert out[0]["probability"] == pytest.approx(0.65)
    assert out[1]["probability"] == pytest.approx(0.5)
    assert out[2]["probability"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Dispatcher: market_signal_estimate
# ---------------------------------------------------------------------------


def test_dispatcher_returns_kalshi_when_ticker_hits(monkeypatch) -> None:
    """If Kalshi returns an Estimate, Polymarket isn't even called."""
    monkeypatch.setattr(
        market_signal,
        "kalshi_price_estimate",
        lambda ticker, title=None: _est("market_price", conf=0.65, p=0.42),
    )

    polymarket_called = []

    def fake_polymarket(_title):
        polymarket_called.append(True)
        return _est("market_consensus", conf=0.65, p=0.99)

    monkeypatch.setattr(market_signal, "market_consensus_estimate", fake_polymarket)

    est = market_signal_estimate("KX-X", "anything")
    assert est is not None
    assert est.strategy == "market_price"
    assert polymarket_called == []  # Kalshi succeeded; Polymarket skipped


def test_dispatcher_falls_back_to_polymarket_when_kalshi_misses(monkeypatch) -> None:
    monkeypatch.setattr(
        market_signal, "kalshi_price_estimate", lambda ticker, title=None: None
    )
    monkeypatch.setattr(
        market_signal,
        "market_consensus_estimate",
        lambda _title: _est("market_consensus", conf=0.65, p=0.55),
    )

    est = market_signal_estimate("KX-MISS", "Will X happen?")
    assert est is not None
    assert est.strategy == "market_consensus"  # came from Polymarket


def test_dispatcher_returns_none_when_both_miss(monkeypatch) -> None:
    monkeypatch.setattr(
        market_signal, "kalshi_price_estimate", lambda ticker, title=None: None
    )
    monkeypatch.setattr(
        market_signal, "market_consensus_estimate", lambda _title: None
    )
    assert market_signal_estimate("KX-NONE", "no match") is None


def test_dispatcher_skips_kalshi_when_no_ticker(monkeypatch) -> None:
    """No ticker → straight to Polymarket."""
    kalshi_called = []

    def fake_kalshi(ticker, title=None):
        kalshi_called.append(True)
        return _est("market_price", conf=0.65, p=0.5)

    monkeypatch.setattr(market_signal, "kalshi_price_estimate", fake_kalshi)
    monkeypatch.setattr(
        market_signal,
        "market_consensus_estimate",
        lambda _title: _est("market_consensus", conf=0.65, p=0.5),
    )

    est = market_signal_estimate(None, "Will X happen?")
    assert est is not None
    assert est.strategy == "market_consensus"
    assert kalshi_called == []  # never called without a ticker


def test_dispatcher_returns_none_when_no_ticker_and_no_title() -> None:
    assert market_signal_estimate(None, None) is None
    assert market_signal_estimate("", "") is None


# ---------------------------------------------------------------------------
# Market anchoring
# ---------------------------------------------------------------------------


def _final(p: float, agreement: float = 0.8) -> object:
    """Build a FinalPrediction with a single dummy estimate."""
    from ai_prophet.forecast.ensemble import FinalPrediction

    return FinalPrediction(
        p_yes=p,
        rationale="ensemble rationale",
        raw_p_yes=p,
        agreement=agreement,
        shrinkage=0.05,
        estimates=[_est("s", conf=0.7, p=p)],
    )


def test_market_anchor_returns_ensemble_unchanged_when_no_market_signal() -> None:
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    final = _final(0.72, agreement=0.9)
    # No market estimate in the list.
    result = _market_anchored_prediction(
        final, [_est("evidence_weighted", 0.7, 0.72)], "TICKER"
    )
    assert result.p_yes == pytest.approx(0.72)
    assert result is final or result.p_yes == final.p_yes


def test_market_anchor_matches_market_when_delta_small() -> None:
    """|0.55 - 0.58| = 0.03 < 0.05 → snap to the market price 0.58.

    The match-market threshold was tightened from 0.10 to 0.05 to mirror
    the play the top leaderboard teams use: don't diverge unless you
    have meaningful edge.
    """
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    final = _final(0.55, agreement=0.9)
    estimates = [
        _est("evidence_weighted", 0.7, 0.55),
        _est("market_price", 0.65, 0.58),
    ]
    result = _market_anchored_prediction(final, estimates, "TICKER")
    assert result.p_yes == pytest.approx(0.58)
    assert "match_market" in result.rationale


def test_market_anchor_uses_ensemble_when_delta_large_and_agreement_high() -> None:
    """|0.80 - 0.30| = 0.50 ≥ 0.05 AND agreement > 0.85 → trust ensemble.

    HIGH_AGREEMENT threshold raised from 0.75 to 0.85: we require
    near-unanimous internal consensus before overriding the market.
    """
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    final = _final(0.80, agreement=0.90)
    estimates = [
        _est("evidence_weighted", 0.8, 0.80),
        _est("market_consensus", 0.65, 0.30),
    ]
    result = _market_anchored_prediction(final, estimates, "TICKER")
    assert result.p_yes == pytest.approx(0.80)
    assert "use_ensemble" in result.rationale


def test_market_anchor_blends_when_delta_large_and_agreement_low() -> None:
    """|0.85 - 0.30| = 0.55 ≥ 0.05 AND agreement <= 0.85 → 0.8*market + 0.2*ensemble.

    Blend weights tightened from (0.6, 0.4) to (0.8, 0.2): on weak-
    consensus events the market is much more often right than our
    ensemble, so the blend now pulls 4x harder toward the market.
    """
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    final = _final(0.85, agreement=0.55)
    estimates = [
        _est("evidence_weighted", 0.6, 0.85),
        _est("market_price", 0.65, 0.30),
    ]
    result = _market_anchored_prediction(final, estimates, "TICKER")
    # 0.8 * 0.30 + 0.2 * 0.85 = 0.24 + 0.17 = 0.41
    assert result.p_yes == pytest.approx(0.41, abs=1e-3)
    assert "blend" in result.rationale


def test_market_anchor_uses_ensemble_at_agreement_above_threshold() -> None:
    """agreement > 0.85 (not >=) gates the use_ensemble branch."""
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    # agreement exactly 0.85 → NOT use_ensemble (strict >); falls to blend.
    final = _final(0.85, agreement=0.85)
    estimates = [
        _est("evidence_weighted", 0.7, 0.85),
        _est("market_price", 0.65, 0.30),
    ]
    result = _market_anchored_prediction(final, estimates, "TICKER")
    assert "blend" in result.rationale

    # agreement just above 0.85 → use_ensemble.
    final2 = _final(0.85, agreement=0.851)
    result2 = _market_anchored_prediction(final2, estimates, "TICKER")
    assert "use_ensemble" in result2.rationale


def test_market_anchor_log_line_includes_all_required_fields(monkeypatch, caplog) -> None:
    """Verify the phase=market_anchor log has every field the spec requires."""
    import logging

    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    caplog.set_level(logging.INFO, logger="ai_prophet.forecast.ensemble_agent")
    final = _final(0.42, agreement=0.5)
    estimates = [
        _est("evidence_weighted", 0.5, 0.42),
        _est("market_price", 0.65, 0.60),
    ]
    _market_anchored_prediction(final, estimates, "MY-TICKER")

    anchor_logs = [r.message for r in caplog.records if "phase=market_anchor" in r.message]
    assert anchor_logs, "no market_anchor log line emitted"
    line = anchor_logs[-1]
    assert "ticker=MY-TICKER" in line
    assert "market_p=0.600" in line
    assert "ensemble_p=0.420" in line
    assert "delta=" in line
    assert "action=" in line


def test_market_anchor_clamps_blend_result_to_legal_range() -> None:
    """Blend math always stays in [0.01, 0.99]."""
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    # Pathological inputs (both at extreme ends) — blend stays within range.
    final = _final(0.999, agreement=0.5)
    estimates = [
        _est("evidence_weighted", 0.5, 0.999),
        _est("market_price", 0.65, 0.999),
    ]
    result = _market_anchored_prediction(final, estimates, "X")
    assert 0.01 <= result.p_yes <= 0.99


def test_market_anchor_preserves_diagnostic_fields() -> None:
    """The agreement/shrinkage/estimates fields survive anchoring."""
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    final = _final(0.55, agreement=0.9)
    estimates = [
        _est("evidence_weighted", 0.7, 0.55),
        _est("market_price", 0.65, 0.58),
    ]
    result = _market_anchored_prediction(final, estimates, "X")
    assert result.agreement == pytest.approx(0.9)
    assert result.shrinkage == pytest.approx(final.shrinkage)
    assert result.raw_p_yes == pytest.approx(final.raw_p_yes)
    assert len(result.estimates) == len(final.estimates)


def test_market_anchor_recognizes_market_consensus_strategy() -> None:
    """Polymarket signals (strategy='market_consensus') also trigger anchoring."""
    from ai_prophet.forecast.ensemble_agent import _market_anchored_prediction

    final = _final(0.55, agreement=0.9)
    estimates = [
        _est("evidence_weighted", 0.7, 0.55),
        _est("market_consensus", 0.65, 0.58),  # not market_price
    ]
    result = _market_anchored_prediction(final, estimates, "X")
    assert result.p_yes == pytest.approx(0.58)  # matched the polymarket price
    assert "match_market" in result.rationale


def test_forecast_event_short_circuits_when_market_signal_available(monkeypatch) -> None:
    """Phase 0 short-circuit: when a market signal exists, return it
    directly and skip the entire ensemble. Under the scoring formula
    ``(our_brier - market_brier) * completion_rate``, matching the
    market on covered events guarantees a near-zero Brier delta — the
    play the top leaderboard teams (Dr Strange, partini at +0.01 to
    +0.02) use.
    """
    from ai_prophet.forecast import ensemble_agent

    monkeypatch.setattr(
        ensemble_agent,
        "_market_signal_task",
        lambda _tk, _t: _est("market_price", 0.65, 0.62),
    )

    # Sentinels: these MUST NOT be called when a market signal exists.
    research_called = {"n": 0}

    def _no_research(**_kw):
        research_called["n"] += 1
        return "x" * 4000

    strategy_called = {"n": 0}

    def _no_strategy(strategy, event, research, temporal_ctx=None):
        strategy_called["n"] += 1
        return _est(strategy.name, 0.7, 0.60)

    monkeypatch.setattr(ensemble_agent, "research_event", _no_research)
    monkeypatch.setattr(ensemble_agent, "_run_strategy", _no_strategy)

    event = ensemble_agent.EventRequest(
        title="Will Lakers beat Thunder?",
        category="Sports",
        market_ticker="KX-LAKERS",
        outcomes=["Lakers", "Thunder"],
    )
    final = ensemble_agent.forecast_event(event)

    # Market price returned directly — no ensemble work.
    assert final.p_yes == pytest.approx(0.62, abs=1e-6)
    assert "market_match" in final.rationale
    # The full pipeline must NOT have run.
    assert research_called["n"] == 0, "research ran despite market signal"
    assert strategy_called["n"] == 0, "strategies ran despite market signal"
    # FinalPrediction diagnostic fields are populated.
    assert final.agreement == 1.0
    assert final.shrinkage == 0.0
    assert len(final.estimates) == 1
    assert final.estimates[0].strategy == "market_price"


def test_forecast_event_rebalances_when_research_is_thin(monkeypatch) -> None:
    """A short research brief triggers evidence->0.3, base_rate->0.7."""
    monkeypatch.setenv("ENABLE_DELIBERATION", "false")
    monkeypatch.setattr(ensemble_agent, "research_event", lambda **_kw: "")  # empty
    monkeypatch.setattr(ensemble_agent, "fast_resolve", lambda _e, _r: None)
    monkeypatch.setattr(ensemble_agent, "_market_signal_task", lambda _ticker, _title: None)

    table = {
        "evidence_weighted": _est("evidence_weighted", conf=0.6, p=0.7),
        "base_rate": _est("base_rate", conf=0.5, p=0.6),
        "contrarian": _est("contrarian", conf=0.5, p=0.65),
    }
    monkeypatch.setattr(
        ensemble_agent,
        "_run_strategy",
        lambda strategy, event, research, temporal_ctx=None: table[strategy.name],
    )

    event = ensemble_agent.EventRequest(title="t", category="Sports", outcomes=["A", "B"])
    final = ensemble_agent.forecast_event(event)
    by_name = {e.strategy: e for e in final.estimates}
    assert by_name["evidence_weighted"].confidence == pytest.approx(0.3)
    assert by_name["base_rate"].confidence == pytest.approx(0.7)
