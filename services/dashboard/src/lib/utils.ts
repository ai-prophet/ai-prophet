// Shared formatting and styling utilities for the trading dashboard

export function pnlCls(v: number): string {
  return v > 0 ? "text-profit" : v < 0 ? "text-loss" : "text-txt-muted";
}

export function fmtDollar(v: number): string {
  const sign = v >= 0 ? "" : "-";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

export function fmtPct(v: number, decimals = 1): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}%`;
}

export function fmtCents(v: number): string {
  return `${(v * 100).toFixed(0)}c`;
}

export function fmtEdge(v: number): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}pp`;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtTimeShort(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

export const TOOLTIP_STYLE = {
  backgroundColor: "#141a22",
  border: "1px solid #1c2433",
  borderRadius: "4px",
  color: "#e8edf5",
  fontSize: 10,
  fontFamily: "'JetBrains Mono', monospace",
  padding: "6px 10px",
};

export const TOOLTIP_LABEL_STYLE = { color: "#7a879b", fontSize: 9 };

export const CHART_COLORS = {
  profit: "#00d26a",
  loss: "#ff4757",
  accent: "#3b82f6",
  muted: "#5a6577",
  grid: "#1c2433",
  reference: "#2a3545",
  warn: "#f0b429",
  purple: "#8b5cf6",
  cyan: "#06b6d4",
};
