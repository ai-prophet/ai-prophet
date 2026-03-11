"""MiniProphetForecastStage — replaces Search + Forecast with an agentic loop.

Markets are processed in parallel via mini-prophet's ``batch_forecast`` API.
The pipeline's ``LLMClient`` is wrapped in :class:`LLMClientBridge` and passed
as the ``model`` parameter, so all stages share the same underlying LLM —
no separate ``MINIPROPHET_MODEL_NAME`` configuration is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from miniprophet import ForecastProblem, batch_forecast

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.config import MiniProphetConfig
from ai_prophet.trade.llm import LLMClient

from ..stages.base import PipelineStage, StageResult
from .bridge import LLMClientBridge
from .prompts import INSTANCE_TEMPLATE, SYSTEM_TEMPLATE
from .tools import MarketDataTool, TradingSubmitTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom agent class for batch_forecast
# ---------------------------------------------------------------------------


class TradingForecastAgent:
    """Per-market forecasting agent wrapping ``DefaultForecastAgent``.

    All heavy setup — tools, environment, context manager, and the inner
    ``DefaultForecastAgent`` — happens in ``__init__``.  :meth:`run` only
    delegates to the inner agent and extracts the rationale from its exit
    message.

    ``batch_forecast`` creates the ``model`` (our :class:`LLMClientBridge`)
    once and passes it to every agent instance via the factory.  The ``env``
    created by ``batch_forecast`` contains default tools; we swap
    ``SubmitTool`` for :class:`TradingSubmitTool` and add
    :class:`MarketDataTool`.
    """

    def __init__(
        self,
        model: Any,
        env: Any,
        *,
        market_info: dict[str, dict] | None = None,
        mp_config: MiniProphetConfig | None = None,
        rationale_store: dict[str, str] | None = None,
        context_manager: Any | None = None,
        **kwargs: Any,
    ) -> None:
        from miniprophet.agent.default import DefaultForecastAgent
        from miniprophet.environment.forecast_env import ForecastEnvironment

        self._market_info = market_info or {}
        self._mp_config = mp_config
        self._rationale_store = rationale_store if rationale_store is not None else {}

        # Attributes inspected by EvalBatchAgentWrapper for cost tracking
        self.messages: list[dict] = []
        self.model_cost: float = 0.0
        self.search_cost: float = 0.0
        self.total_cost: float = 0.0
        self.n_calls: int = 0
        self.n_searches: int = 0

        # Swap default submit tool → TradingSubmitTool; add MarketDataTool.
        # These are market-independent at init time; MarketDataTool is added
        # per-market in run() since we don't know the title yet.
        board = env.board
        tools = [t for name, t in env._tools.items() if name != "submit"]
        # outcomes are always ["Yes", "No"] — TradingSubmitTool needs them at init
        tools.append(TradingSubmitTool(outcomes=["Yes", "No"], board=board))

        new_env = ForecastEnvironment(tools=tools, board=board)

        cfg = mp_config
        self._agent = DefaultForecastAgent(
            model=model,
            env=new_env,
            context_manager=context_manager,
            system_template=SYSTEM_TEMPLATE,
            instance_template=INSTANCE_TEMPLATE,  # seed queries injected in run()
            step_limit=cfg.step_limit if cfg else 20,
            search_limit=cfg.search_limit if cfg else 3,
            cost_limit=cfg.cost_limit if cfg else 1.0,
            show_current_time=cfg.show_current_time if cfg else True,
        )

    def run(
        self,
        title: str,
        outcomes: list[str],
        ground_truth: dict[str, int] | None = None,
        **runtime_kwargs: Any,
    ) -> dict[str, Any]:
        info = self._market_info.get(title, {})
        market = info.get("market")
        seed_queries: list[str] = info.get("seed_queries", [])

        # Inject MarketDataTool for this specific market
        if market:
            self._agent.env._tools["get_market_data"] = MarketDataTool(market)

        # Format instance template with seed queries
        seed_block = ""
        if seed_queries:
            formatted = "\n".join(f"- {q}" for q in seed_queries)
            seed_block = (
                "The review stage has suggested these search queries to start with:\n"
                f"{formatted}"
            )
        self._agent.config.instance_template = INSTANCE_TEMPLATE.replace(
            "{seed_queries_block}", seed_block
        )

        result = self._agent.run(
            title=title,
            outcomes=outcomes,
            ground_truth=ground_truth,
            **runtime_kwargs,
        )

        # Extract rationale from exit messages
        rationale = ""
        for msg in reversed(self._agent.messages):
            if msg.get("role") == "exit":
                rationale = msg.get("extra", {}).get("rationale", "")
                break

        self._rationale_store[title] = rationale

        # Propagate cost/stats for EvalBatchAgentWrapper
        self.messages = self._agent.messages
        self.model_cost = getattr(self._agent, "model_cost", 0.0)
        self.search_cost = getattr(self._agent, "search_cost", 0.0)
        self.total_cost = getattr(self._agent, "total_cost", 0.0)
        self.n_calls = getattr(self._agent, "n_calls", 0)
        self.n_searches = getattr(self._agent, "n_searches", 0)

        return result


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


class MiniProphetForecastStage(PipelineStage):
    """Replaces Search + Forecast using mini-prophet's agentic loop.

    For each market selected by the Review stage this stage:

    1. Wraps the pipeline's ``LLMClient`` in :class:`LLMClientBridge`
    2. Calls ``batch_forecast`` with :class:`TradingForecastAgent` and the
       bridge as the shared ``model``
    3. Extracts probabilities and rationales for the Action stage

    Markets are processed in parallel (``config.batch_workers`` threads).

    The stage name is ``"forecast"`` so that the downstream ActionStage
    can read ``previous_results["forecast"]`` unchanged.
    """

    def __init__(self, llm_client: LLMClient, config: MiniProphetConfig) -> None:
        super().__init__(llm_client=llm_client)
        self.config = config

    @property
    def name(self) -> str:
        return "forecast"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        review_result = previous_results.get("review")
        if not review_result or not review_result.success:
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="Review stage did not succeed",
            )

        review_items = review_result.data.get("review", [])

        # Build ForecastProblems + market lookup
        problems: list[ForecastProblem] = []
        market_info: dict[str, dict[str, Any]] = {}

        for item in review_items:
            market_id = item["market_id"]
            market = tick_ctx.get_candidate(market_id)
            if not market:
                logger.warning("Market %s not found in tick context, skipping", market_id)
                continue

            problems.append(
                ForecastProblem(
                    task_id=market_id,
                    title=market.question,
                    outcomes=["Yes", "No"],
                )
            )
            market_info[market.question] = {
                "market": market,
                "seed_queries": item.get("queries", []),
                "market_id": market_id,
            }

        if not problems:
            return StageResult(
                stage_name=self.name, success=True, data={"forecasts": {}}
            )

        workers = min(len(problems), self.config.batch_workers)
        rationale_store: dict[str, str] = {}

        logger.info(
            "Running batch_forecast: %d markets, %d workers, timeout=%ds",
            len(problems), workers, int(self.config.batch_timeout),
        )

        # Wrap the pipeline's LLMClient as a mini-prophet Model
        bridge = LLMClientBridge(self.llm_client)

        # Override the mini-prophet config to use Exa as the default search backend
        override_config = {
            "search": {
                "search_class": "exa",
            },
        }

        results = batch_forecast(
            problems,
            config=override_config,
            model=bridge,
            workers=workers,
            timeout_seconds=self.config.batch_timeout,
            agent_class=TradingForecastAgent,
            agent_kwargs={
                "market_info": market_info,
                "mp_config": self.config,
                "rationale_store": rationale_store,
            },
        )

        # Convert batch results → pipeline forecast format
        forecasts: dict[str, dict[str, Any]] = {}
        for r in results:
            info = market_info.get(r.title)
            if not info:
                continue
            mid = info["market_id"]
            market = info["market"]

            if r.submission and "Yes" in r.submission:
                p_yes = r.submission["Yes"]
                rationale = rationale_store.get(r.title, "")
                logger.info("Forecast for %s: p_yes=%.3f (status=%s)", mid, p_yes, r.status)
            else:
                p_yes = market.yes_mark
                rationale = f"Agent status: {r.status}, error: {r.error or 'no submission'}"
                logger.warning("Fallback for %s: p_yes=%.3f (%s)", mid, p_yes, r.status)

            forecasts[mid] = {"p_yes": p_yes, "rationale": rationale}

        return StageResult(
            stage_name=self.name,
            success=True,
            data={"forecasts": forecasts},
        )
