"use client";

import { useEffect, useState } from "react";

interface PendingOrder {
  order_id: string;
  ticker: string;
  side: string;
  count: number;
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

export function OrderMonitoringPanel({ instance, apiUrl }: { instance: string; apiUrl: string }) {
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
      <div className="p-4 bg-bg-primary border border-border rounded-lg">
        <div className="text-txt-muted text-sm">Loading monitoring data...</div>
      </div>
    );
  }

  const alertLevelColor = {
    ok: "text-green-400 bg-green-400/10",
    warning: "text-yellow-400 bg-yellow-400/10",
    critical: "text-red-400 bg-red-400/10",
  }[orderData.alert_level];

  return (
    <div className="space-y-4">
      {/* Alert Banner */}
      {(orderData.alert_level !== "ok" || alertData.has_critical_alerts) && (
        <div
          className={`p-3 border rounded-lg ${
            orderData.alert_level === "critical" || alertData.has_critical_alerts
              ? "bg-red-500/10 border-red-500/30"
              : "bg-yellow-500/10 border-yellow-500/30"
          }`}
        >
          <div className="flex items-center gap-2 text-sm font-medium">
            <span className="text-lg">⚠️</span>
            <span>
              {orderData.stale_orders.length > 0 &&
                `${orderData.stale_orders.length} stale order(s) detected. `}
              {alertData.has_critical_alerts && `${alertData.alert_count} system alert(s).`}
            </span>
          </div>
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Pending Orders"
          value={orderData.pending_orders.length}
          color="text-blue-400"
        />
        <StatCard
          label="Stale Orders"
          value={orderData.stale_orders.length}
          color={orderData.stale_orders.length > 0 ? "text-red-400" : "text-green-400"}
        />
        <StatCard
          label="System Alerts"
          value={alertData.alert_count}
          color={alertData.alert_count > 0 ? "text-red-400" : "text-green-400"}
        />
        <StatCard
          label="Errors (24h)"
          value={alertData.error_count}
          color={alertData.error_count > 0 ? "text-yellow-400" : "text-green-400"}
        />
      </div>

      {/* Order Status Breakdown */}
      <div className="p-4 bg-bg-primary border border-border rounded-lg">
        <div className="text-sm font-medium text-txt-secondary mb-3">Order Status</div>
        <div className="flex flex-wrap gap-3">
          {Object.entries(orderData.status_breakdown).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <span
                className={`px-2 py-0.5 text-[10px] font-mono rounded ${getStatusColor(
                  status
                )}`}
              >
                {status}
              </span>
              <span className="text-sm text-txt-muted">{count}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Pending Orders Table */}
      {orderData.pending_orders.length > 0 && (
        <div className="p-4 bg-bg-primary border border-border rounded-lg">
          <div className="text-sm font-medium text-txt-secondary mb-3">
            Pending Orders ({orderData.pending_orders.length})
          </div>
          <div className="space-y-2">
            {orderData.pending_orders.slice(0, 10).map((order) => (
              <div
                key={order.order_id}
                className={`flex items-center justify-between p-2 rounded text-xs ${
                  order.is_stale ? "bg-red-500/10 border border-red-500/30" : "bg-bg-secondary"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-txt-secondary">{order.ticker}</span>
                  <span
                    className={`px-1.5 py-0.5 rounded ${
                      order.side === "yes"
                        ? "bg-green-500/20 text-green-400"
                        : "bg-red-500/20 text-red-400"
                    }`}
                  >
                    {order.count} {order.side.toUpperCase()}
                  </span>
                  <span className="text-txt-muted">@ {order.price_cents}¢</span>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`${
                      order.is_stale ? "text-red-400 font-medium" : "text-txt-muted"
                    }`}
                  >
                    {order.age_minutes < 60
                      ? `${Math.floor(order.age_minutes)}m`
                      : `${(order.age_minutes / 60).toFixed(1)}h`}
                  </span>
                  {order.is_stale && (
                    <span className="text-[10px] px-1.5 py-0.5 bg-red-500/20 text-red-400 rounded">
                      STALE
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Cancellations */}
      {orderData.recent_cancellations.length > 0 && (
        <div className="p-4 bg-bg-primary border border-border rounded-lg">
          <div className="text-sm font-medium text-txt-secondary mb-3">
            Recent Cancellations (24h)
          </div>
          <div className="space-y-1.5 max-h-40 overflow-y-auto">
            {orderData.recent_cancellations.slice(0, 5).map((order) => (
              <div key={order.order_id} className="flex items-center justify-between text-xs">
                <span className="font-mono text-txt-muted">{order.ticker}</span>
                <span className="text-txt-muted">
                  {order.count} {order.side.toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* System Alerts */}
      {alertData.alerts.length > 0 && (
        <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
          <div className="text-sm font-medium text-red-400 mb-3">
            🚨 Critical Alerts ({alertData.alert_count})
          </div>
          <div className="space-y-2 max-h-60 overflow-y-auto">
            {alertData.alerts.map((alert) => (
              <div key={alert.id} className="p-2 bg-bg-secondary rounded text-xs">
                <div className="text-txt-secondary">{alert.message}</div>
                <div className="text-txt-muted mt-1">
                  {alert.component} • {new Date(alert.created_at).toLocaleTimeString()}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="p-3 bg-bg-primary border border-border rounded-lg">
      <div className="text-[10px] text-txt-muted uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
    </div>
  );
}

function getStatusColor(status: string): string {
  switch (status.toUpperCase()) {
    case "FILLED":
      return "bg-green-500/20 text-green-400";
    case "PENDING":
      return "bg-blue-500/20 text-blue-400";
    case "CANCELLED":
      return "bg-gray-500/20 text-gray-400";
    case "ERROR":
      return "bg-red-500/20 text-red-400";
    case "DRY_RUN":
      return "bg-purple-500/20 text-purple-400";
    default:
      return "bg-gray-500/20 text-gray-400";
  }
}
