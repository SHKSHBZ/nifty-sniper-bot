"use client";

import type { PnlToday } from "@/types";

interface Props {
  pnl: PnlToday | null | undefined;
}

/**
 * Cumulative-P&L sparkline for the day. SVG-based, no charting lib.
 * Shows the running net P&L from open to last trade. Two extra
 * tiles flank the chart: realized P&L total and W/L badge.
 */
export default function PnlSparkline({ pnl }: Props) {
  if (!pnl) {
    return (
      <div className="glass p-4 text-xs text-slate-500">
        Waiting for P&L data...
      </div>
    );
  }

  const series = pnl.pnl_timeseries ?? [];
  const wins = pnl.win_count;
  const losses = pnl.loss_count;
  const realized = pnl.realized_pnl;
  const realizedSign = realized >= 0 ? "+" : "";
  const realizedColor = realized >= 0 ? "text-emerald-400" : "text-rose-400";

  return (
    <div className="glass p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[10px] font-bold uppercase text-slate-500 tracking-wider">
            Today's P&L
          </div>
          <div className={`text-2xl md:text-3xl font-black tabular-nums ${realizedColor}`}>
            {realizedSign}₹{realized.toLocaleString("en-IN", {
              maximumFractionDigits: 0,
            })}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] font-bold uppercase text-slate-500 tracking-wider mb-0.5">
            Trades
          </div>
          <div className="flex items-center gap-1 text-sm font-mono">
            <span className="text-emerald-400 font-bold">{wins}W</span>
            <span className="text-slate-600">/</span>
            <span className="text-rose-400 font-bold">{losses}L</span>
          </div>
        </div>
      </div>

      {series.length >= 2 ? (
        <SparklineSVG
          values={series.map((p) => p.cumulative_pnl)}
          positive={realized >= 0}
        />
      ) : (
        <div className="h-[48px] flex items-center justify-center text-[11px] text-slate-600">
          Need at least 2 closed trades to render the curve
        </div>
      )}
    </div>
  );
}

function SparklineSVG({
  values,
  positive,
}: {
  values: number[];
  positive: boolean;
}) {
  const W = 320;
  const H = 48;
  const PAD = 2;

  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const range = max - min || 1;

  const stepX = values.length > 1 ? (W - 2 * PAD) / (values.length - 1) : 0;

  const points = values.map((v, i) => {
    const x = PAD + i * stepX;
    const y = PAD + (1 - (v - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const path = `M ${points.join(" L ")}`;

  // Zero line
  const zeroY = PAD + (1 - (0 - min) / range) * (H - 2 * PAD);

  const stroke = positive ? "rgb(52 211 153)" : "rgb(251 113 133)"; // emerald-400 / rose-400

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="w-full h-12"
    >
      <line
        x1={0}
        x2={W}
        y1={zeroY}
        y2={zeroY}
        stroke="rgba(255,255,255,0.08)"
        strokeDasharray="2 3"
      />
      <path d={path} stroke={stroke} strokeWidth="2" fill="none" />
    </svg>
  );
}
