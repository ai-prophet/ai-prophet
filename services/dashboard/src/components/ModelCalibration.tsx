"use client";

import { useState, useMemo } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Scatter,
  ScatterChart,
  ZAxis,
  ComposedChart,
} from "recharts";
import type { ModelCalibrationData } from "@/lib/api";
import { TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE, CHART_COLORS } from "@/lib/utils";

type View = "calibration" | "brier";

export function ModelCalibration({
  calibration,
}: {
  calibration: ModelCalibrationData | null;
}) {
  const [view, setView] = useState<View>("calibration");
  const [selectedModel, setSelectedModel] = useState<string>("all");

  const chartData = useMemo(() => {
    if (!calibration) return [];

    const bins =
      selectedModel === "all"
        ? calibration.calibration
        : calibration.by_model[selectedModel]?.calibration ?? [];

    return bins.map((bin) => ({
      predicted: parseFloat((bin.predicted_avg * 100).toFixed(1)),
      observed: parseFloat((bin.observed_freq * 100).toFixed(1)),
      count: bin.count,
      perfect: parseFloat((bin.predicted_avg * 100).toFixed(1)),
    }));
  }, [calibration, selectedModel]);

  // Diagonal line data for perfect calibration
  const diagonalData = useMemo(() => {
    return Array.from({ length: 11 }, (_, i) => ({
      predicted: i * 10,
      perfect: i * 10,
    }));
  }, []);

  if (!calibration || (calibration.calibration.length === 0 && Object.keys(calibration.by_model).length === 0)) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No resolved markets yet — calibration requires expired markets with known outcomes
      </div>
    );
  }

  const currentBrier =
    selectedModel === "all"
      ? calibration.brier_score
      : calibration.by_model[selectedModel]?.brier_score ?? null;

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Model Calibration
        </h3>
        <div className="flex gap-1">
          {(["calibration", "brier"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                view === v
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {v === "calibration" ? "Calibration Plot" : "Brier Score"}
            </button>
          ))}
        </div>
      </div>

      {/* Model selector */}
      {calibration.models.length > 0 && (
        <div className="px-3 py-2 border-b border-t-border/50 flex flex-wrap gap-1">
          <button
            onClick={() => setSelectedModel("all")}
            className={`px-2 py-0.5 text-[9px] rounded border transition-colors ${
              selectedModel === "all"
                ? "border-accent text-accent bg-accent/10"
                : "border-t-border text-txt-muted hover:text-txt-secondary"
            }`}
          >
            All Models
          </button>
          {calibration.models.map((model) => (
            <button
              key={model}
              onClick={() => setSelectedModel(model)}
              className={`px-2 py-0.5 text-[9px] rounded border transition-colors ${
                selectedModel === model
                  ? "border-accent text-accent bg-accent/10"
                  : "border-t-border text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {model}
            </button>
          ))}
        </div>
      )}

      <div className="p-3">
        {view === "calibration" ? (
          chartData.length === 0 ? (
            <div className="text-center text-txt-muted text-[10px] py-8">
              No calibration bins available for this model
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart
                data={chartData}
                margin={{ left: 5, right: 15, top: 10, bottom: 5 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke={CHART_COLORS.grid}
                />
                <XAxis
                  dataKey="predicted"
                  stroke="transparent"
                  fontSize={9}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fill: CHART_COLORS.muted }}
                  tickFormatter={(v) => `${v}%`}
                  domain={[0, 100]}
                  label={{
                    value: "Predicted Probability",
                    position: "insideBottom",
                    offset: -2,
                    style: { fill: CHART_COLORS.muted, fontSize: 9 },
                  }}
                />
                <YAxis
                  stroke="transparent"
                  fontSize={9}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fill: CHART_COLORS.muted }}
                  tickFormatter={(v) => `${v}%`}
                  domain={[0, 100]}
                  width={40}
                  label={{
                    value: "Observed Frequency",
                    angle: -90,
                    position: "insideLeft",
                    offset: 10,
                    style: { fill: CHART_COLORS.muted, fontSize: 9 },
                  }}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  formatter={(value: number, name: string) => {
                    if (name === "observed") return [`${value}%`, "Observed"];
                    return [`${value}%`, "Perfect"];
                  }}
                  labelFormatter={(v) => `Predicted: ${v}%`}
                />
                {/* Perfect calibration diagonal */}
                <ReferenceLine
                  segment={[
                    { x: 0, y: 0 },
                    { x: 100, y: 100 },
                  ]}
                  stroke={CHART_COLORS.muted}
                  strokeDasharray="6 3"
                  strokeOpacity={0.5}
                />
                {/* Actual calibration line */}
                <Line
                  type="monotone"
                  dataKey="observed"
                  stroke={CHART_COLORS.accent}
                  strokeWidth={2}
                  dot={{
                    r: 3,
                    fill: CHART_COLORS.accent,
                    stroke: "#0f1419",
                    strokeWidth: 1.5,
                  }}
                  activeDot={{
                    r: 4,
                    fill: CHART_COLORS.accent,
                    stroke: "#0f1419",
                    strokeWidth: 2,
                  }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          )
        ) : (
          /* Brier Score view */
          <div className="space-y-3">
            {/* Model vs Market baseline comparison */}
            <div className="grid grid-cols-2 gap-2">
              <div className="flex items-center justify-between p-3 rounded border border-t-border/60 bg-t-bg/30">
                <div>
                  <div className="text-[9px] text-txt-muted uppercase tracking-wider">
                    Model Brier Score
                  </div>
                  <div className="text-[10px] text-txt-muted mt-0.5">
                    Lower is better (0 = perfect)
                  </div>
                </div>
                <span
                  className={`text-xl font-mono font-medium ${
                    calibration.brier_score < 0.15
                      ? "text-profit"
                      : calibration.brier_score < 0.25
                        ? "text-warn"
                        : "text-loss"
                  }`}
                >
                  {calibration.brier_score.toFixed(4)}
                </span>
              </div>
              <div className="flex items-center justify-between p-3 rounded border border-t-border/60 bg-t-bg/30">
                <div>
                  <div className="text-[9px] text-txt-muted uppercase tracking-wider">
                    Market Baseline
                  </div>
                  <div className="text-[10px] text-txt-muted mt-0.5">
                    Using mkt price as prediction
                  </div>
                </div>
                <div className="text-right">
                  <span className="text-xl font-mono font-medium text-txt-secondary">
                    {(calibration.market_baseline_brier ?? 0.25).toFixed(4)}
                  </span>
                  {calibration.market_baseline_brier != null && (
                    <div className={`text-[9px] font-mono mt-0.5 ${
                      calibration.brier_score < calibration.market_baseline_brier
                        ? "text-profit"
                        : "text-loss"
                    }`}>
                      {calibration.brier_score < calibration.market_baseline_brier
                        ? `▼ ${((calibration.market_baseline_brier - calibration.brier_score) * 100).toFixed(2)}pp better`
                        : `▲ ${((calibration.brier_score - calibration.market_baseline_brier) * 100).toFixed(2)}pp worse`}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Per-model Brier scores */}
            {Object.keys(calibration.by_model).length > 0 && (
              <div className="space-y-1">
                <div className="text-[9px] text-txt-muted uppercase tracking-wider px-1">
                  By Model
                </div>
                {Object.entries(calibration.by_model)
                  .sort(([, a], [, b]) => a.brier_score - b.brier_score)
                  .map(([model, data]) => (
                    <div
                      key={model}
                      className="flex items-center justify-between p-2 rounded border border-t-border/40 hover:border-t-border transition-colors"
                    >
                      <div className="flex flex-col gap-0.5">
                        <span className="text-xs text-txt-primary">{model}</span>
                        <span className="text-[9px] font-mono text-txt-muted">
                          {data.total_predictions} predictions
                        </span>
                      </div>
                      <span
                        className={`text-sm font-mono font-medium ${
                          data.brier_score < 0.15
                            ? "text-profit"
                            : data.brier_score < 0.25
                              ? "text-warn"
                              : "text-loss"
                        }`}
                      >
                        {data.brier_score.toFixed(4)}
                      </span>
                    </div>
                  ))}
              </div>
            )}

            {/* Reference scale */}
            <div className="flex items-center gap-2 text-[9px] text-txt-muted px-1">
              <span className="text-profit">0.00 perfect</span>
              <span className="text-t-border-light">|</span>
              <span className="text-profit">{"<"}0.15 good</span>
              <span className="text-t-border-light">|</span>
              <span className="text-warn">0.15-0.25 fair</span>
              <span className="text-t-border-light">|</span>
              <span className="text-loss">{">"}0.25 poor</span>
            </div>
          </div>
        )}
      </div>

      {/* Footer with current Brier score in calibration view */}
      {view === "calibration" && currentBrier !== null && (
        <div className="px-3 py-2 border-t border-t-border flex gap-4 text-[10px] font-mono text-txt-muted">
          <span>
            Brier Score:{" "}
            <span
              className={
                currentBrier < 0.15
                  ? "text-profit"
                  : currentBrier < 0.25
                    ? "text-warn"
                    : "text-loss"
              }
            >
              {currentBrier.toFixed(4)}
            </span>
          </span>
          <span>
            Bins: <span className="text-txt-primary">{chartData.length}</span>
          </span>
          <span>
            Predictions:{" "}
            <span className="text-txt-primary">
              {chartData.reduce((s, d) => s + d.count, 0)}
            </span>
          </span>
        </div>
      )}
    </div>
  );
}
