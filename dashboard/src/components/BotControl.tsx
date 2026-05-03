"use client";

import { Play, Square, RefreshCw } from "lucide-react";
import type { BotStatus, BotName } from "@/types";

interface Props {
  bots: BotName[];
  statuses: BotStatus[];
  loading: Record<string, boolean>;
  onToggle: (name: BotName, isRunning: boolean) => void;
}

/**
 * Big, glowy start/stop control. When a bot is RUNNING the Stop
 * button glows red; when STOPPED the Start button glows green.
 * The shadow-glow makes the active state obvious at a glance —
 * no hunting for which button does what.
 */
export default function BotControl({
  bots,
  statuses,
  loading,
  onToggle,
}: Props) {
  return (
    <div className="space-y-3">
      {bots.map((bot) => {
        const status = statuses.find((x) => x.name === bot);
        const running = status?.status === "running";
        const busy = !!loading[bot];

        return (
          <div
            key={bot}
            className={`glass p-4 md:p-5 flex items-center justify-between gap-4 transition-all ${
              running ? "border-emerald-500/30" : ""
            }`}
          >
            <div className="min-w-0 flex-1">
              <div className="font-bold text-sm md:text-base truncate">
                {bot}
              </div>
              <div
                className={`text-[11px] font-bold uppercase tracking-wider ${
                  running ? "text-emerald-400" : "text-slate-500"
                }`}
              >
                {running ? (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
                    RUNNING
                  </span>
                ) : (
                  "○ STOPPED"
                )}
                {status?.pid ? (
                  <span className="ml-2 text-slate-600 normal-case">
                    pid {status.pid}
                  </span>
                ) : null}
              </div>
            </div>
            <button
              onClick={() => onToggle(bot, running)}
              disabled={busy}
              aria-label={running ? `Stop ${bot}` : `Start ${bot}`}
              className={`w-16 h-16 md:w-20 md:h-20 rounded-2xl flex items-center justify-center transition-all active:scale-90 flex-shrink-0 font-black text-white text-xs md:text-sm uppercase tracking-wider ${
                running
                  ? "bg-rose-500 shadow-lg shadow-rose-500/50 hover:shadow-rose-500/70 hover:bg-rose-400"
                  : "bg-emerald-500 shadow-lg shadow-emerald-500/50 hover:shadow-emerald-500/70 hover:bg-emerald-400"
              } ${busy ? "opacity-50" : ""}`}
            >
              {busy ? (
                <RefreshCw className="w-7 h-7 md:w-8 md:h-8 animate-spin" />
              ) : running ? (
                <Square className="w-7 h-7 md:w-8 md:h-8 fill-white" />
              ) : (
                <Play className="w-7 h-7 md:w-8 md:h-8 fill-white" />
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
