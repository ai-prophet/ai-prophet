"use client";

import { useMemo } from "react";
import type { Alert } from "@/lib/api";
import { fmtTime } from "@/lib/utils";

const SEVERITY_CONFIG = {
  error: {
    dot: "bg-loss",
    badge: "bg-loss/15 text-loss border-loss/20",
    order: 0,
  },
  warning: {
    dot: "bg-warn",
    badge: "bg-warn/15 text-warn border-warn/20",
    order: 1,
  },
  info: {
    dot: "bg-accent",
    badge: "bg-accent/15 text-accent border-accent/20",
    order: 2,
  },
} as const;

export function AlertsPanel({
  alerts,
  onAlertClick,
  onAlertClear,
  clearingAlertKey,
}: {
  alerts: Alert[];
  onAlertClick?: (marketId: string) => void;
  onAlertClear?: (alertKey: string) => void;
  clearingAlertKey?: string | null;
}) {
  const sortedAlerts = useMemo(() => {
    return [...alerts].sort((a, b) => {
      const sevA = SEVERITY_CONFIG[a.severity]?.order ?? 3;
      const sevB = SEVERITY_CONFIG[b.severity]?.order ?? 3;
      if (sevA !== sevB) return sevA - sevB;
      return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
    });
  }, [alerts]);

  const counts = useMemo(() => {
    const c = { error: 0, warning: 0, info: 0 };
    for (const a of alerts) {
      if (a.severity in c) c[a.severity]++;
    }
    return c;
  }, [alerts]);

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Alerts
        </h3>
        {alerts.length > 0 && (
          <div className="flex gap-2 text-[9px] font-mono">
            {counts.error > 0 && (
              <span className="text-loss">{counts.error}E</span>
            )}
            {counts.warning > 0 && (
              <span className="text-warn">{counts.warning}W</span>
            )}
            {counts.info > 0 && (
              <span className="text-accent">{counts.info}I</span>
            )}
          </div>
        )}
      </div>

      {/* Alerts list */}
      <div className="max-h-[320px] overflow-y-auto">
        {sortedAlerts.length === 0 ? (
          <div className="p-6 text-center text-txt-muted text-[10px]">
            No active alerts
          </div>
        ) : (
          <div className="divide-y divide-t-border/30">
            {sortedAlerts.map((alert, idx) => {
              const config = SEVERITY_CONFIG[alert.severity] ?? SEVERITY_CONFIG.info;
              const isClearing = clearingAlertKey === alert.key;
              const content = (
                <>
                  <div className="flex items-start gap-2">
                    <span
                      className={`w-1.5 h-1.5 rounded-full mt-1 shrink-0 ${config.dot}`}
                    />

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span
                          className={`text-[8px] font-bold uppercase tracking-wider px-1.5 py-px rounded border ${config.badge}`}
                        >
                          {alert.type}
                        </span>
                        <span className="text-[9px] font-mono text-txt-muted">
                          {fmtTime(alert.timestamp)}
                        </span>
                      </div>

                      <p className="text-[10px] text-txt-primary leading-snug">
                        {alert.message}
                      </p>

                      {alert.market_id && (
                        <span className="text-[8px] font-mono text-txt-muted mt-0.5 inline-block">
                          {alert.market_id}
                        </span>
                      )}
                    </div>
                  </div>
                </>
              );

              return (
                <div
                  key={`${alert.type}-${alert.timestamp}-${idx}`}
                  className="flex items-start gap-2 px-3 py-2"
                >
                  <div className="flex-1 min-w-0">
                    {alert.market_id ? (
                      <button
                        type="button"
                        onClick={() => onAlertClick?.(alert.market_id!)}
                        className="w-full text-left transition-colors hover:bg-t-panel-hover rounded-sm -mx-1 px-1 py-0.5"
                      >
                        {content}
                      </button>
                    ) : (
                      <div className="px-1 py-0.5">
                        {content}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => onAlertClear?.(alert.key)}
                    disabled={isClearing}
                    className="shrink-0 mt-0.5 text-[9px] font-mono uppercase tracking-wider px-2 py-1 rounded border border-t-border text-txt-muted hover:text-txt-primary hover:border-t-border-light disabled:opacity-50 disabled:cursor-wait"
                  >
                    {isClearing ? "Clearing" : "Clear"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer with count */}
      {alerts.length > 5 && (
        <div className="px-3 py-1.5 border-t border-t-border text-center text-[9px] text-txt-muted">
          {alerts.length} active alerts
        </div>
      )}
    </div>
  );
}
