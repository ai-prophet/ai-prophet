"use client";

import type { HealthData } from "@/lib/api";

export function SystemHealth({ health }: { health: HealthData | null }) {
  if (!health) {
    return (
      <div className="flex items-center gap-1.5 text-txt-muted text-[10px] font-mono">
        <span className="w-1.5 h-1.5 rounded-full bg-t-border-light animate-pulse" />
        connecting
      </div>
    );
  }

  const ok = health.status === "ok" && health.worker === "healthy";

  return (
    <div className="flex items-center gap-2.5">
      <div className="flex items-center gap-1.5 text-[10px] font-mono text-txt-secondary">
        <span
          className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-profit" : "bg-warn"} animate-pulse`}
        />
        <span className="hidden sm:inline">
          db:{health.database === "connected" ? "ok" : "err"}
        </span>
        <span className="hidden sm:inline text-t-border-light">/</span>
        <span className="hidden sm:inline">
          wkr:{health.worker === "healthy" ? "ok" : health.worker}
        </span>
      </div>
      <span
        className={`text-[9px] font-bold tracking-widest uppercase px-1.5 py-0.5 rounded ${
          health.mode === "live"
            ? "bg-profit-dim text-profit border border-profit/20"
            : "bg-warn-dim text-warn border border-warn/20"
        }`}
      >
        {health.mode === "live" ? "LIVE" : "DRY RUN"}
      </span>
    </div>
  );
}
