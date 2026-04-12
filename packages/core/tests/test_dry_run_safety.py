"""Paper trade safety gate: verify BettingEngine(paper=True) never makes real HTTP calls."""

from unittest.mock import patch

from ai_prophet_core.betting import BettingEngine


def test_make_trade_paper_no_http():
    engine = BettingEngine(paper=True, enabled=True)

    with patch("requests.Session.post") as mock_post, \
         patch("requests.Session.get") as mock_get:
        result = engine.make_trade("kalshi:TEST", side="yes", shares=5, price=0.55)

        mock_post.assert_not_called()
        mock_get.assert_not_called()

    assert result.order_placed is True
    assert result.status == "DRY_RUN"


def test_trade_from_forecast_paper_no_http():
    engine = BettingEngine(paper=True, enabled=True)

    with patch("requests.Session.post") as mock_post, \
         patch("requests.Session.get") as mock_get:
        result = engine.trade_from_forecast(
            market_id="kalshi:TEST", p_yes=0.72, yes_ask=0.55, no_ask=0.45,
        )

        mock_post.assert_not_called()
        mock_get.assert_not_called()

    assert result is not None
    assert result.order_placed is True
    assert result.status == "DRY_RUN"


def test_on_forecast_method_removed():
    engine = BettingEngine(enabled=False)
    assert not hasattr(engine, "on_forecast")
