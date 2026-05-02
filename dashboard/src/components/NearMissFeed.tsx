"use client";

import { AlertTriangle } from "lucide-react";
import type { NearMissPayload, MissedEntry } from "@/types";

interface Props {
  payload: NearMissPayload | null | undefined;
}

/**
 * Near-miss feed — a chronological list of today's "almost-trades".
 * For each, shows the blocker gate and (once finalised) the
 * hypothetical P&L if we had taken the trade.
 *
 * Pulls from data/missed_today_<INDEX>.json which the bot's
 * LiveMissedTracker keeps fresh on every loop iteration.
 */
export default function NearMissFeed({ payload }: Props) {
  const list = payload?.missed ?? [];

  if (list.length === 0) {
    return (
      <div className="glass p-5 text-center">
        <AlertTriangle className="w-8 h-8 text-slate-700 mx-auto mb-2" />
        <div className="text-sm font-bold text-slate-500">No near-misses today</div>
        <div className="text-xs text-slate-600 mt-1">
          As tactics get blocked by a single gate, they appear here with a
          hypothetical P&L tracked over the next ~90 min.
        </div>
      </div>
    );
  }

  // Newest first
  const ordered = [...list].sort((a, b) =>
    (b.ts ?? "").localeCompare(a.ts ?? "")
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-[11px] font-bold uppercase tracking-wider text-slate-400 px-1">
        <span>Today's Near-Misses</span>
        <span className="text-slate-500 normal-case font-mono">{list.length}</span>
      </div>
      <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
        {ordered.map((m, i) => (
          <NearMissRow key={`${m.ts}-${i}`} m={m} />
        ))}
      </div>
    </div>
  );
}

function NearMissRow({ m }: { m: MissedEntry }) {
  const finalised = m.hypothetical_outcome !== "";
  const tone = outcomeTone(m.hypothetical_outcome);

  let timeStr = "";
  try {
    timeStr = new Date(m.ts).toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    timeStr = m.ts;
  }

  return (
    <div className="glass p-3 space-y-1.5">
      <div className="flex items-center gap-2 text-xs">
        <span className="font-mono text-slate-500">{timeStr}</span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${
            m.direction === "CE"
              ? "bg-emerald-500/15 text-emerald-400"
              : "bg-rose-500/15 text-rose-400"
          }`}
        >
          {m.direction}
        </span>
        <span className="font-bold text-slate-300 truncate">{m.tactic}</span>
        {finalised ? (
          <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded font-bold ${tone.badge}`}>
            {m.hypothetical_outcome}
          </span>
        ) : (
          <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded font-bold bg-amber-500/15 text-amber-400">
            tracking
          </span>
        )}
      </div>
      <div className="text-[11px] text-slate-400 break-words">
        Blocked by{" "}
        <span className="font-mono text-slate-300">{m.blocked_by}</span>
        {m.blocker_detail ? (
          <span className="text-slate-500"> — {m.blocker_detail}</span>
        ) : null}
      </div>
      {finalised ? (
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-slate-500">
            ₹{m.hypothetical_entry_premium.toFixed(0)} →{" "}
            ₹{m.hypothetical_exit_premium.toFixed(0)}
          </span>
          <span className={`font-bold tabular-nums ${tone.text}`}>
            {m.hypothetical_pnl >= 0 ? "+" : ""}₹{m.hypothetical_pnl.toFixed(0)}
          </span>
        </div>
      ) : (
        <div className="text-[11px] text-slate-500">
          Tracking strike {m.hypothetical_strike} for {m.time_stop_min}m...
        </div>
      )}
    </div>
  );
}

function outcomeTone(outcome: MissedEntry["hypothetical_outcome"]): {
  badge: string;
  text: string;
} {
  switch (outcome) {
    case "WIN":
      return {
        badge: "bg-rose-500/15 text-rose-400",
        text: "text-rose-400",
      };
    case "LOSS":
      return {
        badge: "bg-emerald-500/15 text-emerald-400",
        text: "text-emerald-400",
      };
    case "BREAKEVEN":
      return {
        badge: "bg-slate-500/15 text-slate-400",
        text: "text-slate-400",
      };
    default:
      return {
        badge: "bg-slate-700/40 text-slate-500",
        text: "text-slate-500",
      };
  }
}
