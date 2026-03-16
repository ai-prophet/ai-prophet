"use client";

import type { SystemLogEntry } from "@/lib/api";

function levelCls(level: string): string {
  switch (level) {
    case "ERROR":
      return "text-loss";
    case "WARNING":
      return "text-warn";
    case "INFO":
      return "text-accent";
    case "HEARTBEAT":
      return "text-profit";
    default:
      return "text-txt-muted";
  }
}

export function LiveActivity({ logs }: { logs: SystemLogEntry[] }) {
  if (logs.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No system activity
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      <div className="max-h-[380px] overflow-y-auto">
        {logs.map((log) => {
          const time = new Date(log.created_at).toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
          });

          return (
            <div
              key={log.id}
              className={`px-3 py-1 font-mono text-[10px] leading-relaxed border-b border-t-border/20 hover:bg-t-panel-hover transition-colors ${
                log.level === "ERROR" ? "bg-loss/[0.03]" : ""
              }`}
            >
              <span className="text-txt-muted">{time}</span>{" "}
              <span className={`font-medium ${levelCls(log.level)}`}>
                {log.level.slice(0, 4)}
              </span>{" "}
              <span className="text-txt-muted">{log.component}</span>{" "}
              <span className="text-txt-secondary">{log.message}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
