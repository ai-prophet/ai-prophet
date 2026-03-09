"""MiniProphetForecastStage — replaces Search + Forecast with an agentic loop."""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.config import MiniProphetConfig
from ai_prophet.trade.llm import LLMClient

from ..stages.base import PipelineStage, StageResult
from .bridge import LLMClientBridge
from .prompts import INSTANCE_TEMPLATE, SYSTEM_TEMPLATE
from .tools import MarketDataTool, TradingSubmitTool

logger = logging.getLogger(__name__)


class MiniProphetForecastStage(PipelineStage):
    """Replaces Search + Forecast using mini-prophet's agentic loop.

    For each market selected by the Review stage, this stage:
    1. Creates a mini-prophet ``DefaultForecastAgent`` with a bridge LLM
    2. Equips it with search, source-curation, and market-data tools
    3. Runs the iterative research loop until the agent submits a forecast
    4. Extracts the probability and rationale for the Action stage

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
        forecasts: dict[str, dict[str, Any]] = {}

        for item in review_items:
            market_id = item["market_id"]
            market = tick_ctx.get_candidate(market_id)
            if not market:
                logger.warning(f"Market {market_id} not found in tick context, skipping")
                continue

            seed_queries = item.get("queries", [])
            try:
                result, rationale = self._run_agent_for_market(
                    market, seed_queries, tick_ctx
                )
                p_yes = result.get("submission", {}).get("Yes", 0.5)
                forecasts[market_id] = {"p_yes": p_yes, "rationale": rationale}
                logger.info(f"Forecast for {market_id}: p_yes={p_yes:.3f}")
            except Exception as e:
                logger.error(f"Mini-prophet agent failed for {market_id}: {e}", exc_info=True)
                forecasts[market_id] = {
                    "p_yes": market.yes_mark,
                    "rationale": f"Agent error, falling back to market price: {e}",
                }

        return StageResult(
            stage_name=self.name,
            success=True,
            data={"forecasts": forecasts},
        )

    def _run_agent_for_market(
        self,
        market: Any,
        seed_queries: list[str],
        tick_ctx: TickContext,
    ) -> tuple[dict[str, Any], str]:
        """Run the mini-prophet agent for a single market.

        Returns (ForecastResult dict, rationale string).
        """
        from miniprophet.agent.context import SlidingWindowContextManager
        from miniprophet.agent.default import DefaultForecastAgent
        from miniprophet.environment.forecast_env import ForecastEnvironment, create_default_tools
        from miniprophet.environment.source_board import SourceBoard
        from miniprophet.tools.search.exa import ExaSearchBackend

        # Search backend
        search_backend = ExaSearchBackend()

        # Source board + tools
        board = SourceBoard()
        outcomes = ["Yes", "No"]

        default_tools = create_default_tools(
            search_tool=search_backend,
            outcomes=outcomes,
            board=board,
            search_limit=self.config.search_limit,
        )

        # Replace the default SubmitTool with our TradingSubmitTool
        trading_submit = TradingSubmitTool(outcomes=outcomes, board=board)
        tools = [t for t in default_tools if t.name != "submit"]
        tools.append(trading_submit)

        # Add market data tool
        tools.append(MarketDataTool(market))

        # Environment
        env = ForecastEnvironment(tools=tools, board=board)

        # Bridge + context manager
        bridge = LLMClientBridge(self.llm_client)
        context_mgr = SlidingWindowContextManager(window_size=self.config.context_window)

        # Format instance template with seed queries
        seed_block = ""
        if seed_queries:
            formatted = "\n".join(f"- {q}" for q in seed_queries)
            seed_block = (
                f"The review stage has suggested these search queries to start with:\n"
                f"{formatted}"
            )

        instance_template = INSTANCE_TEMPLATE.replace(
            "{seed_queries_block}", seed_block
        )

        # Create and run agent
        agent = DefaultForecastAgent(
            model=bridge,
            env=env,
            context_manager=context_mgr,
            system_template=SYSTEM_TEMPLATE,
            instance_template=instance_template,
            step_limit=self.config.step_limit,
            search_limit=self.config.search_limit,
            cost_limit=self.config.cost_limit,
            show_current_time=self.config.show_current_time,
        )

        result = agent.run(title=market.question, outcomes=outcomes)

        # Extract rationale from exit message
        rationale = ""
        for msg in reversed(agent.messages):
            if msg.get("role") == "exit":
                rationale = msg.get("extra", {}).get("rationale", "")
                break

        return result, rationale
