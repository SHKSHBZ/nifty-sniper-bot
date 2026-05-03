"use client";

import type { Trade } from "@/types";

interface Props {
  trades: Trade[];
}

/**
 * Trades section — mobile-first card view, desktop table view.
 * Newest trade at the top, with green/red P&L pill.
 */
export default function TradesTable({ trades }: Props) {
  const ordered = [...trades].slice().reverse();
  const empty = ordered.length === 0;

  return (
    <div>
      <div className="text-sm font-bold text-slate-400 mb-2 flex items-center gap-2">
        Trade History
        <span className="text-[10px] bg-white/5 px-1.5 py-0.5 rounded font-mono">
          {trades.length}
        </span>
      </div>

      {/* Mobile: card view */}
      <div className="md:hidden space-y-2">
        {empty ? (
          <div className="glass p-8 text-center text-slate-600 text-sm">
            No trades yet
          </div>
        ) : (
          ordered.map((t, i) => <TradeCard key={i} t={t} />)
        )}
      </div>

      {/* Desktop: table view */}
      <div className="hidden md:block glass overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-black/20 text-slate-500 text-xs uppercase">
              <tr>
                <th className="px-4 py-3">Exit Time</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Strike</th>
                <th className="px-4 py-3">Entry → Exit</th>
                <th className="px-4 py-3">P&L</th>
                <th className="px-4 py-3">Reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {empty ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-8 text-center text-slate-600"
                  >
                    No trades yet
                  </td>
                </tr>
              ) : (
                ordered.map((t, i) => (
                  <tr key={i} className="hover:bg-white/5">
                    <td className="px-4 py-3 text-slate-400 font-mono text-xs">
                      {formatTime(t.exit_time)}
                    </td>
                    <td className="px-4 py-3 font-bold">{t.trade_type}</td>
                    <td className="px-4 py-3">{t.strike}</td>
                    <td className="px-4 py-3 font-mono text-xs">
                      ₹{t.entry_price.toFixed(2)} → ₹{t.exit_price.toFixed(2)}
                    </td>
                    <td
                      className={`px-4 py-3 font-bold tabular-nums ${
                        t.pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {t.pnl >= 0 ? "+" : ""}₹{t.pnl.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {t.reason}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function TradeCard({ t }: { t: Trade }) {
  const win = t.pnl >= 0;
  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="font-bold text-sm">{t.trade_type}</span>
        <span
          className={`font-black text-sm tabular-nums ${
            win ? "text-emerald-400" : "text-rose-400"
          }`}
        >
          {win ? "+" : ""}₹{t.pnl.toFixed(0)}
        </span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-slate-400">
        <span>Strike {t.strike}</span>
        <span className="font-mono">
          ₹{t.entry_price} → ₹{t.exit_price}
        </span>
      </div>
      <div className="text-[10px] text-slate-500 mt-1">{t.reason}</div>
    </div>
  );
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    const date = d.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
    });
    const time = d.toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${date} ${time}`;
  } catch {
    return ts;
  }
}
