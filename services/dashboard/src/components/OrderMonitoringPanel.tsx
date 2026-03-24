"use client";

import { useEffect, useState } from "react";

function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);

  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;

  const month = date.toLocaleDateString('en-US', { month: 'short' });
  const day = date.getDate();
  const time = date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  return `${month} ${day}, ${time}`;
}

interface PendingOrder {
  order_id: string;
  ticker: string;
  market_title?: string;
  side: string;
  count: number;
  filled_shares?: number;
  price_cents: number;
  created_at: string;
  age_minutes: number;
  is_stale: boolean;
}

interface OrderMonitoring {
  pending_orders: PendingOrder[];
  stale_orders: any[];
  recent_cancellations: any[];
  status_breakdown: Record<string, number>;
  alert_level: "ok" | "warning" | "critical";
}

interface Alert {
  id: number;
  message: string;
  component: string;
  created_at: string;
}

interface SystemAlerts {
  alerts: Alert[];
  errors: Alert[];
  alert_count: number;
  error_count: number;
  has_critical_alerts: boolean;
}

export function OrderMonitoringPanel({
  instance,
  apiUrl,
  onMarketClick,
}: {
  instance: string;
  apiUrl: string;
  onMarketClick?: (ticker: string) => void;
}) {
  const [orderData, setOrderData] = useState<OrderMonitoring | null>(null);
  const [alertData, setAlertData] = useState<SystemAlerts | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [orderRes, alertRes] = await Promise.all([
          fetch(`${apiUrl}/order-monitoring?instance_name=${instance}`),
          fetch(`${apiUrl}/system-alerts?instance_name=${instance}&hours=24`),
        ]);

        if (orderRes.ok) setOrderData(await orderRes.json());
        if (alertRes.ok) setAlertData(await alertRes.json());
      } catch (error) {
        console.error("Failed to fetch monitoring data:", error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, [instance, apiUrl]);

  if (loading || !orderData || !alertData) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        Loading monitoring data...
      </div>
    );
  }

  const hasPending = orderData.pending_orders.length > 0;
  const hasCancelled = orderData.recent_cancellations.length > 0;

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Order Monitoring
        </h3>
        <div className="flex items-center gap-2 text-[9px] font-mono">
          {orderData.pending_orders.length > 0 && (
            <span className="text-accent">{orderData.pending_orders.length}P</span>
          )}
          {orderData.stale_orders.length > 0 && (
            <span className="text-loss">{orderData.stale_orders.length}S</span>
          )}
        </div>
      </div>

      {/* Orders list */}
      <div className="max-h-[320px] overflow-y-auto">
        {!hasPending && !hasCancelled ? (
          <div className="p-6 text-center text-txt-muted text-[10px]">
            No pending orders or recent cancellations
          </div>
        ) : (
          <div>
            {/* Pending orders section */}
            {hasPending && (
              <div>
                <div className="px-3 py-1.5 text-[8px] font-bold uppercase tracking-wider text-txt-muted bg-t-bg">
                  Pending ({orderData.pending_orders.length})
                </div>
                <div className="divide-y divide-t-border/30">
                  {orderData.pending_orders.map((order, idx) => (
                    <div
                      key={order.order_id || idx}
                      className="flex items-start gap-2 px-3 py-2"
                    >
                      <div className="flex-1 min-w-0">
                        <button
                          type="button"
                          onClick={() => onMarketClick?.(order.ticker)}
                          className="w-full text-left transition-colors hover:bg-t-panel-hover rounded-sm -mx-1 px-1 py-0.5"
                        >
                          <div className="flex items-center justify-between mb-0.5">
                            <div className="flex items-center gap-1.5">
                              <span
                                className={`text-[8px] font-bold uppercase tracking-wider px-1.5 py-px rounded border ${
                                  order.is_stale
                                    ? "bg-loss/15 text-loss border-loss/20"
                                    : "bg-accent/15 text-accent border-accent/20"
                                }`}
                              >
                                {order.is_stale ? "STALE" : "PENDING"}
                              </span>
                              <span className="text-[9px] font-mono text-txt-muted">
                                {order.ticker}
                              </span>
                            </div>
                            <span className="text-[9px] text-txt-muted">
                              {formatTimestamp(order.created_at)}
                            </span>
                          </div>

                          {order.market_title && (
                            <p className="text-[10px] text-txt-primary leading-snug mb-0.5">
                              {order.market_title}
                            </p>
                          )}

                          <div className="flex items-center gap-2 mt-0.5">
                            <span
                              className={`text-[9px] px-1 py-px rounded ${
                                order.side === "yes"
                                  ? "bg-profit/20 text-profit"
                                  : "bg-loss/20 text-loss"
                              }`}
                            >
                              {order.count} {order.side.toUpperCase()}
                            </span>
                            <span className="text-[9px] text-txt-muted">
                              @ {order.price_cents}¢
                            </span>
                            {order.age_minutes != null && (
                              <span className={`text-[9px] ${order.is_stale ? "text-loss" : "text-txt-muted"}`}>
                                {order.age_minutes < 60
                                  ? `${Math.floor(order.age_minutes)}m`
                                  : `${(order.age_minutes / 60).toFixed(1)}h`}
                              </span>
                            )}
                          </div>

                          {order.filled_shares != null && order.filled_shares > 0 && (
                            <span className="text-[9px] text-accent mt-0.5 inline-block">
                              Partial: {order.filled_shares}/{order.count}
                            </span>
                          )}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Cancelled orders section */}
            {hasCancelled && (
              <div>
                <div className="px-3 py-1.5 text-[8px] font-bold uppercase tracking-wider text-txt-muted bg-t-bg">
                  Cancelled ({orderData.recent_cancellations.length})
                </div>
                <div className="divide-y divide-t-border/30">
                  {orderData.recent_cancellations.slice(0, 5).map((order: any, idx: number) => (
                    <div
                      key={order.order_id || idx}
                      className="flex items-start gap-2 px-3 py-2"
                    >
                      <div className="flex-1 min-w-0">
                        <button
                          type="button"
                          onClick={() => onMarketClick?.(order.ticker)}
                          className="w-full text-left transition-colors hover:bg-t-panel-hover rounded-sm -mx-1 px-1 py-0.5"
                        >
                          <div className="flex items-center justify-between mb-0.5">
                            <div className="flex items-center gap-1.5">
                              <span className="text-[8px] font-bold uppercase tracking-wider px-1.5 py-px rounded border bg-txt-muted/15 text-txt-muted border-txt-muted/20">
                                CANCELLED
                              </span>
                              <span className="text-[9px] font-mono text-txt-muted">
                                {order.ticker}
                              </span>
                            </div>
                            {order.created_at && (
                              <span className="text-[9px] text-txt-muted">
                                {formatTimestamp(order.created_at)}
                              </span>
                            )}
                          </div>

                          <div className="flex items-center gap-2 mt-0.5">
                            <span
                              className={`text-[9px] px-1 py-px rounded ${
                                order.side === "yes"
                                  ? "bg-profit/20 text-profit"
                                  : "bg-loss/20 text-loss"
                              }`}
                            >
                              {order.count} {order.side.toUpperCase()}
                            </span>
                            {order.price_cents && (
                              <span className="text-[9px] text-txt-muted">
                                @ {order.price_cents}¢
                              </span>
                            )}
                          </div>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer with count */}
      {(hasPending || hasCancelled) && (orderData.pending_orders.length + orderData.recent_cancellations.length > 5) && (
        <div className="px-3 py-1.5 border-t border-t-border text-center text-[9px] text-txt-muted">
          {orderData.pending_orders.length} pending, {orderData.recent_cancellations.length} cancelled
        </div>
      )}
    </div>
  );
}
