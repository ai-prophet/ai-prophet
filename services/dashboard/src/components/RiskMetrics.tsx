"use client";

import type { AnalyticsSummary } from "@/lib/api";
import { fmtDollar, fmtPct, CHART_COLORS } from "@/lib/utils";

const WIN_RATE_TOOLTIP =
  "A win means positive realized P&L. Losses have negative realized P&L. Zero realized P&L counts as neither, so this is not the same as final market resolution.";

function metricColor(value: number, greenAbove: number, redBelow: number): string {
  if (value >= greenAbove) return "text-profit";
  if (value <= redBelow) return "text-loss";
  return "text-txt-primary";
}

function InfoDot({ text }: { text: string }) {
  return (
    <span
      className="inline-flex items-center justify-center w-3 h-3 ml-1 rounded border border-txt-muted/30 text-[7px] text-txt-muted cursor-help hover:border-accent hover:text-accent transition-colors align-middle"
      title={text}
    >
      ?
    </span>
  );
}

export function RiskMetrics({
  analytics,
}: {
  analytics: AnalyticsSummary | null;
}) {
  if (!analytics) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        Waiting for trade data — risk metrics appear after trades are placed
      </div>
    );
  }

  if (analytics.total_trades === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No completed trades yet — risk metrics require trade history
      </div>
    );
  }

  const winRatePct = analytics.win_rate * 100;
  const avgWinLossRatio =
    analytics.avg_loss !== 0
      ? Math.abs(analytics.avg_win / analytics.avg_loss)
      : analytics.avg_win > 0
        ? Infinity
        : 0;

  return (
    <div className="bg-t-panel border border-t-border rounded">
      <div className="px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Risk & Performance
        </h3>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-px bg-t-border/40">
        {/* Sharpe Ratio */}
        <MetricCell
          label="Sharpe Ratio"
          value={analytics.sharpe_ratio.toFixed(2)}
          colorClass={metricColor(analytics.sharpe_ratio, 1, 0)}
        />

        {/* Max Drawdown */}
        <MetricCell
          label="Max Drawdown"
          value={fmtDollar(analytics.max_drawdown)}
          sub={fmtPct(-(analytics.max_drawdown_pct * 100))}
          colorClass="text-loss"
        />

        {/* Volatility */}
        <MetricCell
          label="Volatility"
          value={`${(analytics.volatility * 100).toFixed(1)}%`}
          colorClass="text-txt-primary"
        />

        {/* Profit Factor */}
        <MetricCell
          label="Profit Factor"
          value={analytics.profit_factor === Infinity ? "INF" : analytics.profit_factor.toFixed(2)}
          colorClass={metricColor(analytics.profit_factor, 1, 0)}
        />

        {/* Win Rate with bar */}
        <div className="bg-t-panel p-3 flex flex-col gap-1.5">
          <span className="text-[9px] text-txt-muted uppercase tracking-wider flex items-center">
            Win Rate
            <InfoDot text={WIN_RATE_TOOLTIP} />
          </span>
          <span className="text-sm font-mono font-medium text-txt-primary">
            {winRatePct.toFixed(1)}%
          </span>
          <div className="w-full h-1 rounded-full bg-t-border overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(winRatePct, 100)}%`,
                backgroundColor:
                  winRatePct >= 50
                    ? CHART_COLORS.profit
                    : CHART_COLORS.loss,
              }}
            />
          </div>
        </div>

        {/* Avg Win / Avg Loss */}
        <MetricCell
          label="Win/Loss Ratio"
          value={avgWinLossRatio === Infinity ? "INF" : avgWinLossRatio.toFixed(2)}
          sub={`${fmtDollar(analytics.avg_win)} / ${fmtDollar(analytics.avg_loss)}`}
          colorClass={metricColor(avgWinLossRatio, 1, 0)}
        />

        {/* Trade counts */}
        <div className="bg-t-panel p-3 flex flex-col gap-1">
          <span className="text-[9px] text-txt-muted uppercase tracking-wider">
            Trades
          </span>
          <span className="text-sm font-mono font-medium text-txt-primary">
            {analytics.total_trades}
          </span>
          <div className="flex gap-2 text-[9px] font-mono">
            <span className="text-profit">{analytics.winning_trades}W</span>
            <span className="text-loss">{analytics.losing_trades}L</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricCell({
  label,
  value,
  sub,
  colorClass,
}: {
  label: string;
  value: string;
  sub?: string;
  colorClass: string;
}) {
  return (
    <div className="bg-t-panel p-3 flex flex-col gap-1">
      <span className="text-[9px] text-txt-muted uppercase tracking-wider">
        {label}
      </span>
      <span className={`text-sm font-mono font-medium ${colorClass}`}>
        {value}
      </span>
      {sub && (
        <span className="text-[9px] font-mono text-txt-muted">{sub}</span>
      )}
    </div>
  );
}
