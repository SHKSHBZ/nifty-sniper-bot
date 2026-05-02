"use client";

import { Activity, Target, Compass, Gauge } from "lucide-react";
import type { EngineState } from "@/types";

interface Props {
  state: EngineState | null | undefined;
}

/**
 * Engine state panel — what the bot is currently seeing/computing.
 * Read-only mirror of data/engine_state_<INDEX>.json (written by
 * main.py every loop, consumed by the dashboard backend).
 *
 * Layout: 2-column grid of "labelled value" tiles, with the regime
 * shown prominently up top.
 */
export default function EngineStatePanel({ state }: Props) {
  if (!state || !state.available) {
    return (
      <div className="glass p-5 text-center">
        <Activity className="w-8 h-8 text-slate-700 mx-auto mb-2" />
        <div className="text-sm font-bold text-slate-500">Bot is not publishing state</div>
        <div className="text-xs text-slate-600 mt-1">
          Start the bot to see live regime, spot, signal, and OI data.
        </div>
      </div>
    );
  }

  const regime = state.regime ?? "UNKNOWN";
  const regimeStyle = regimeColor(regime);
  const sigDir = state.last_signal?.direction ?? null;

  return (
    <div className="space-y-3">
      {/* Regime banner */}
      <div className={`glass p-4 ${regimeStyle.border}`}>
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <Compass className={`w-4 h-4 ${regimeStyle.text}`} />
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Regime
            </span>
          </div>
          <span className="text-[10px] text-slate-600 font-mono">
            {state.engine_mode}
          </span>
        </div>
        <div className={`text-xl md:text-2xl font-black ${regimeStyle.text}`}>
          {regime}
        </div>
        {state.is_market_open === false ? (
          <div className="text-[11px] text-slate-500 mt-1">Market closed</div>
        ) : null}
      </div>

      {/* Spot + indicators grid */}
      <div className="grid grid-cols-2 gap-2 md:gap-3">
        <Tile label="Spot" value={fmt(state.spot, 2)} accent="text-blue-400" />
        <Tile label="VWAP" value={fmt(state.vwap, 2)} />
        <Tile label="EMA9 (5m)" value={fmt(state.ema9_5m, 2)} />
        <Tile label="EMA21 (5m)" value={fmt(state.ema21_5m, 2)} />
        <Tile
          label="Focus PCR"
          value={fmt(state.focus_pcr, 2)}
          accent={pcrColor(state.focus_pcr)}
        />
        <Tile
          label="VIX"
          value={fmt(state.vix_level, 2)}
          accent={vixColor(state.vix_level)}
        />
        <Tile
          label="Support"
          value={state.support_strike ? String(state.support_strike) : "—"}
          accent="text-emerald-400"
        />
        <Tile
          label="Resistance"
          value={state.resistance_strike ? String(state.resistance_strike) : "—"}
          accent="text-rose-400"
        />
      </div>

      {/* OI deltas */}
      {(state.ce_oi_change || state.pe_oi_change) ? (
        <div className="glass p-3 md:p-4">
          <div className="flex items-center gap-2 mb-2">
            <Gauge className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              OI Δ (focus zone)
            </span>
          </div>
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="text-slate-300">
              CE: <span className="font-mono">{fmtCount(state.ce_oi_change)}</span>
            </span>
            <span className="text-slate-300">
              PE: <span className="font-mono">{fmtCount(state.pe_oi_change)}</span>
            </span>
          </div>
        </div>
      ) : null}

      {/* Last signal */}
      {state.last_signal ? (
        <div className="glass p-3 md:p-4">
          <div className="flex items-center gap-2 mb-2">
            <Target className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Latest signal
            </span>
            {state.last_signal.near_miss_count > 0 ? (
              <span className="ml-auto text-[10px] px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 font-bold">
                {state.last_signal.near_miss_count} near-miss
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2 mb-1">
            {sigDir === "CE" ? (
              <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-bold">
                CE
              </span>
            ) : sigDir === "PE" ? (
              <span className="text-[10px] px-2 py-0.5 rounded bg-rose-500/15 text-rose-400 font-bold">
                PE
              </span>
            ) : (
              <span className="text-[10px] px-2 py-0.5 rounded bg-slate-700/40 text-slate-400 font-bold">
                NO TRADE
              </span>
            )}
            <span className="text-xs font-bold text-slate-300">
              {state.last_signal.tactic_name ?? "—"}
            </span>
          </div>
          {state.last_signal.reasons?.length ? (
            <div className="text-[11px] text-slate-500 leading-relaxed">
              {state.last_signal.reasons.slice(0, 2).join(" · ")}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Tile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="glass p-2.5 md:p-3">
      <div className="text-[10px] font-bold uppercase text-slate-500 tracking-wider mb-0.5">
        {label}
      </div>
      <div className={`text-sm md:text-base font-black tabular-nums ${accent ?? ""}`}>
        {value}
      </div>
    </div>
  );
}

function fmt(n: number | undefined, dp = 2): string {
  if (n === undefined || n === null || !isFinite(n) || n === 0) return "—";
  return n.toLocaleString("en-IN", {
    maximumFractionDigits: dp,
    minimumFractionDigits: dp,
  });
}

function fmtCount(n: number | undefined): string {
  if (!n) return "—";
  if (Math.abs(n) >= 1_00_000) return `${(n / 1_00_000).toFixed(2)}L`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function pcrColor(pcr: number | undefined): string {
  if (pcr === undefined) return "";
  if (pcr >= 1.2) return "text-emerald-400";
  if (pcr <= 0.8) return "text-rose-400";
  return "text-slate-300";
}

function vixColor(vix: number | undefined): string {
  if (vix === undefined) return "";
  if (vix >= 20) return "text-rose-400";
  if (vix >= 16) return "text-amber-400";
  return "text-slate-300";
}

function regimeColor(regime: string): { text: string; border: string } {
  if (regime.includes("TREND_UP")) {
    return { text: "text-emerald-400", border: "border-l-4 border-l-emerald-500" };
  }
  if (regime.includes("TREND_DOWN")) {
    return { text: "text-rose-400", border: "border-l-4 border-l-rose-500" };
  }
  if (regime === "RANGE") {
    return { text: "text-blue-400", border: "border-l-4 border-l-blue-500" };
  }
  if (regime === "EXPIRY") {
    return { text: "text-amber-400", border: "border-l-4 border-l-amber-500" };
  }
  if (regime === "CHOP" || regime === "NO_TRADE") {
    return { text: "text-slate-400", border: "border-l-4 border-l-slate-500" };
  }
  return { text: "text-slate-300", border: "" };
}
