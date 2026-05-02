"use client";

import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import type { IndexQuote } from "@/types";

interface Props {
  quotes: IndexQuote[];
}

/**
 * Horizontal indices ticker: one tile per index, colour-coded green
 * when up, red when down. Sub-second updates flow in via the SSE
 * stream — this component is purely presentational.
 *
 * On mobile the row scrolls horizontally; on desktop it stretches
 * to fill the width.
 */
export default function IndicesTicker({ quotes }: Props) {
  if (!quotes || quotes.length === 0) {
    return (
      <div className="glass px-4 py-3 text-xs text-slate-500">
        Waiting for indices feed... (login + start data feed to populate)
      </div>
    );
  }

  return (
    <div className="overflow-x-auto -mx-4 md:mx-0 px-4 md:px-0 scrollbar-thin">
      <div className="flex gap-2 md:gap-3 min-w-max md:grid md:grid-cols-5 md:min-w-0">
        {quotes.map((q) => (
          <IndexTile key={q.instrument_key} q={q} />
        ))}
      </div>
    </div>
  );
}

function IndexTile({ q }: { q: IndexQuote }) {
  const up = q.change > 0;
  const down = q.change < 0;
  const flat = !up && !down;
  const colorClass = up
    ? "text-emerald-400"
    : down
    ? "text-rose-400"
    : "text-slate-400";
  const accentClass = up
    ? "from-emerald-500/15 to-transparent"
    : down
    ? "from-rose-500/15 to-transparent"
    : "from-slate-500/15 to-transparent";

  return (
    <div
      className={`glass p-3 md:p-4 min-w-[150px] md:min-w-0 relative overflow-hidden ${
        q.stale ? "opacity-60" : ""
      }`}
    >
      <div
        className={`absolute inset-0 bg-gradient-to-br ${accentClass} pointer-events-none`}
      />
      <div className="relative">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] md:text-xs font-bold uppercase text-slate-400 tracking-wide">
            {q.symbol}
          </span>
          {up && <TrendingUp className={`w-3.5 h-3.5 ${colorClass}`} />}
          {down && <TrendingDown className={`w-3.5 h-3.5 ${colorClass}`} />}
          {flat && <Minus className={`w-3.5 h-3.5 ${colorClass}`} />}
        </div>
        <div className="text-base md:text-xl font-black tabular-nums">
          {q.ltp > 0
            ? q.ltp.toLocaleString("en-IN", {
                maximumFractionDigits: 2,
                minimumFractionDigits: 2,
              })
            : "—"}
        </div>
        <div className={`text-[11px] md:text-xs font-semibold tabular-nums ${colorClass}`}>
          {q.change > 0 ? "+" : ""}
          {q.change.toFixed(2)} ({q.change > 0 ? "+" : ""}
          {q.change_pct.toFixed(2)}%)
        </div>
      </div>
    </div>
  );
}
