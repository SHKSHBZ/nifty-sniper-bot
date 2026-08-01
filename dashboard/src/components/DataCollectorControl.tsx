"use client";

import { Play, Square, RefreshCw, Database } from "lucide-react";
import type { BotStatus } from "@/types";

interface Props {
  statuses: BotStatus[];
  loading: Record<string, boolean>;
  apiBase: string;
  onToggle: (name: string, isRunning: boolean) => void;
}

const DC_LIST = ["NIFTY_DATA", "SENSEX_DATA"] as const;

export default function DataCollectorControl({
  statuses,
  loading,
  apiBase: _apiBase,
  onToggle,
}: Props) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 mb-1">
        <Database className="w-4 h-4 text-cyan-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
          DATA DOWNLOAD
        </span>
      </div>
      {DC_LIST.map((dc) => {
        const status = statuses.find((x) => x.name === dc);
        const running = status?.status === "running";
        const busy = !!loading[dc];
        const label = dc.replace("_DATA", "");

        return (
          <div
            key={dc}
            className={`glass p-3 flex items-center justify-between gap-3 transition-all ${
              running ? "border-cyan-500/30" : ""
            }`}
          >
            <div className="min-w-0 flex-1">
              <div className="font-bold text-xs truncate">
                {label} Data
              </div>
              <div
                className={`text-[10px] font-bold uppercase tracking-wider ${
                  running ? "text-cyan-400" : "text-slate-500"
                }`}
              >
                {running ? (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-pulse" />
                    COLLECTING
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
              onClick={() => onToggle(dc, running)}
              disabled={busy}
              aria-label={running ? `Stop ${dc}` : `Start ${dc}`}
              className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all active:scale-90 flex-shrink-0 font-black text-white text-[10px] ${
                running
                  ? "bg-rose-500 shadow-md shadow-rose-500/40 hover:bg-rose-400"
                  : "bg-cyan-500 shadow-md shadow-cyan-500/40 hover:bg-cyan-400"
              } ${busy ? "opacity-50" : ""}`}
            >
              {busy ? (
                <RefreshCw className="w-4 h-4 animate-spin" />
              ) : running ? (
                <Square className="w-4 h-4 fill-white" />
              ) : (
                <Play className="w-4 h-4 fill-white" />
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
