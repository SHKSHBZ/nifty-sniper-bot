"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Shield, TrendingUp, TrendingDown, Terminal,
  Zap, RefreshCw, Lock, ExternalLink, CheckCircle2,
  XCircle, Wifi, WifiOff,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

import IndicesTicker from "@/components/IndicesTicker";
import BotControl from "@/components/BotControl";
import EngineStatePanel from "@/components/EngineStatePanel";
import NearMissFeed from "@/components/NearMissFeed";
import PnlSparkline from "@/components/PnlSparkline";
import TradesTable from "@/components/TradesTable";
import { useLiveStream } from "@/lib/useLiveStream";

import type {
  PortfolioStats, BotStatus, BotName,
} from "@/types";

const BOT_LIST: BotName[] = ["NIFTY", "SENSEX", "NIFTY_REGIME", "SENSEX_REGIME", "NIFTY_T1"];
type Tab = "live" | "control" | "logs" | "trades";

export default function Dashboard() {
  // ----- API base + selected bot --------------------------------------
  const [apiBase, setApiBase] = useState("");
  const [activeBot, setActiveBot] = useState<BotName>("NIFTY");
  const [activeTab, setActiveTab] = useState<Tab>("live");

  useEffect(() => {
    const host = window.location.hostname;
    const isHttps = window.location.protocol === "https:";
    const base = isHttps ? `https://${host}:8443` : `http://${host}:8000`;
    setApiBase(base);
  }, []);

  // ----- Live SSE stream ----------------------------------------------
  const { tick, connected } = useLiveStream(apiBase, activeBot);

  const indices = tick?.indices ?? [];
  const engineState = tick?.engine_state ?? null;
  const nearMiss = tick?.near_miss ?? null;
  const pnlToday = tick?.pnl ?? null;
  const statuses: BotStatus[] = tick?.status ?? [];

  // ----- Auxiliary fetches (not on the SSE stream) --------------------
  const [stats, setStats] = useState<PortfolioStats | null>(null);
  const [isAuth, setIsAuth] = useState(false);
  const [logs, setLogs] = useState("");
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [authUrl, setAuthUrl] = useState("");
  const [authError, setAuthError] = useState("");
  const [redirectUrl, setRedirectUrl] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const logEndRef = useRef<HTMLDivElement>(null);

  // Stats (capital + open position + history) — refreshed every 5 s
  useEffect(() => {
    if (!apiBase) return;
    let cancelled = false;
    const fetchStats = async () => {
      try {
        const r = await fetch(`${apiBase}/stats/${activeBot}`);
        if (r.ok && !cancelled) setStats(await r.json());
      } catch {}
    };
    fetchStats();
    const id = setInterval(fetchStats, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [apiBase, activeBot]);

  // Auth status
  useEffect(() => {
    if (!apiBase) return;
    const fetchAuth = async () => {
      try {
        const r = await fetch(`${apiBase}/auth/status`);
        if (r.ok) {
          const d = await r.json();
          setIsAuth(!!d.is_authenticated);
        }
      } catch {}
    };
    fetchAuth();
    const id = setInterval(fetchAuth, 30_000);
    return () => clearInterval(id);
  }, [apiBase]);

  // Logs
  useEffect(() => {
    if (!apiBase) return;
    const fetchLogs = async () => {
      try {
        const r = await fetch(`${apiBase}/logs/${activeBot}`);
        if (r.ok) {
          const d = await r.json();
          setLogs(d.logs ?? "");
        }
      } catch {}
    };
    fetchLogs();
    const id = setInterval(fetchLogs, 3000);
    return () => clearInterval(id);
  }, [apiBase, activeBot]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // ----- Actions ------------------------------------------------------
  const toggleBot = async (name: BotName, isRunning: boolean) => {
    if (!isAuth && !isRunning) {
      setShowAuthModal(true);
      return;
    }
    const action = isRunning ? "stop" : "start";
    setLoading((prev) => ({ ...prev, [name]: true }));
    try {
      await fetch(`${apiBase}/${action}/${name}`, { method: "POST" });
    } catch {}
    setTimeout(() => {
      setLoading((prev) => ({ ...prev, [name]: false }));
    }, 800);
  };

  const startLogin = async () => {
    setAuthError("");
    setAuthUrl("");
    try {
      const r = await fetch(`${apiBase}/auth/url`);
      if (!r.ok) {
        let detail = "";
        try {
          const e = await r.json();
          detail = e.detail || JSON.stringify(e);
        } catch {
          detail = await r.text();
        }
        setAuthError(
          `Backend returned ${r.status}. ${detail || ""} ` +
            "Check that your .env has UPSTOX_API_KEY, UPSTOX_API_SECRET, and UPSTOX_REDIRECT_URI."
        );
        return;
      }
      const d = await r.json();
      setAuthUrl(d.url);
      // Try to open in new tab; popup blockers may block it after the
      // async/await, but the URL is also rendered as a clickable link
      // in the modal below so the user can always proceed manually.
      try {
        window.open(d.url, "_blank", "noopener");
      } catch {
        // Ignore — link in modal handles the manual case
      }
    } catch (e) {
      setAuthError(
        `Cannot reach backend at ${apiBase}. Is the "Sniper Backend" window still open? ` +
          (e instanceof Error ? e.message : String(e))
      );
    }
  };

  const submitAuth = async () => {
    if (!redirectUrl) return;
    setAuthLoading(true);
    try {
      const r = await fetch(`${apiBase}/auth/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: redirectUrl }),
      });
      if (r.ok) {
        setIsAuth(true);
        setShowAuthModal(false);
        setRedirectUrl("");
        setAuthUrl("");
      } else {
        const e = await r.json();
        alert("Failed: " + (e.detail || "Unknown"));
      }
    } catch {
      alert("Connection error.");
    }
    setAuthLoading(false);
  };

  // ----- Derived ------------------------------------------------------
  const totalPnl = useMemo(
    () => stats?.trade_history.reduce((a, t) => a + t.pnl, 0) ?? 0,
    [stats],
  );

  return (
    <div className="min-h-screen flex flex-col">
      {/* ── HEADER ── */}
      <header className="sticky top-0 z-40 glass border-b border-white/5 px-4 py-3 md:px-8 md:py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-3">
          <h1 className="text-lg md:text-2xl font-black tracking-tight bg-gradient-to-r from-blue-400 to-violet-400 bg-clip-text text-transparent truncate">
            SNIPER BOT
          </h1>
          <div className="flex items-center gap-2 flex-shrink-0">
            <select
              value={activeBot}
              onChange={(e) => setActiveBot(e.target.value as BotName)}
              className="text-[11px] md:text-xs font-bold bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 focus:outline-none focus:border-blue-500"
            >
              {BOT_LIST.map((b) => (
                <option key={b} value={b}>
                  {b.replace("_REGIME", " REGIME")}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 text-[11px] font-bold">
              {connected ? (
                <>
                  <Wifi className="w-3.5 h-3.5 text-emerald-400" />
                  <span className="text-emerald-400 hidden sm:inline">LIVE</span>
                </>
              ) : (
                <>
                  <WifiOff className="w-3.5 h-3.5 text-rose-400" />
                  <span className="text-rose-400 hidden sm:inline">OFFLINE</span>
                </>
              )}
            </div>
            <button
              onClick={() => setShowAuthModal(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 text-[11px] font-bold active:scale-95 transition-transform"
            >
              {isAuth ? (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span className="text-emerald-400 hidden sm:inline">AUTH</span>
                </>
              ) : (
                <>
                  <Lock className="w-3.5 h-3.5 text-amber-400" />
                  <span className="text-amber-400">LOGIN</span>
                </>
              )}
            </button>
          </div>
        </div>
      </header>

      {/* ── INDICES TICKER ── */}
      <section className="px-4 md:px-8 pt-3 md:pt-4">
        <div className="max-w-6xl mx-auto">
          <IndicesTicker quotes={indices} />
        </div>
      </section>

      {/* ── HEADLINE STATS ── */}
      <section className="px-4 md:px-8 pt-3 md:pt-4">
        <div className="max-w-6xl mx-auto grid grid-cols-3 gap-2 md:gap-4">
          <StatCard
            label={`Capital (${activeBot.replace("_REGIME", " REGIME")})`}
            value={`₹${(stats?.capital ?? 0).toLocaleString("en-IN", {
              maximumFractionDigits: 0,
            })}`}
            icon={<Shield className="w-4 h-4 md:w-5 md:h-5 text-blue-400" />}
          />
          <StatCard
            label="Total P&L"
            value={`${totalPnl >= 0 ? "+" : ""}₹${totalPnl.toLocaleString("en-IN", {
              maximumFractionDigits: 0,
            })}`}
            icon={
              totalPnl >= 0 ? (
                <TrendingUp className="w-4 h-4 md:w-5 md:h-5 text-emerald-400" />
              ) : (
                <TrendingDown className="w-4 h-4 md:w-5 md:h-5 text-rose-400" />
              )
            }
            valueColor={totalPnl >= 0 ? "text-emerald-400" : "text-rose-400"}
          />
          <StatCard
            label="Position"
            value={stats?.open_position ? `${stats.open_position.strike}` : "—"}
            sub={stats?.open_position ? stats.open_position.trade_type : "No trade"}
            icon={
              <Zap
                className={`w-4 h-4 md:w-5 md:h-5 ${
                  stats?.open_position ? "text-amber-400" : "text-slate-500"
                }`}
              />
            }
          />
        </div>
      </section>

      {/* ── MOBILE TAB BAR ── */}
      <div className="md:hidden px-4 pt-4">
        <div className="flex rounded-xl bg-white/5 p-1 gap-1">
          {(["live", "control", "logs", "trades"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 py-2.5 text-xs font-bold uppercase rounded-lg transition-all ${
                activeTab === tab
                  ? "bg-blue-600 text-white shadow-lg shadow-blue-600/25"
                  : "text-slate-400"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      <main className="flex-1 px-4 md:px-8 py-4 md:py-6">
        <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6">
          {/* LEFT — controls */}
          <div
            className={`md:col-span-1 space-y-3 ${
              activeTab !== "control" ? "hidden md:block" : ""
            }`}
          >
            <BotControl
              bots={BOT_LIST}
              statuses={statuses}
              loading={loading}
              onToggle={toggleBot}
            />
            {!isAuth ? (
              <div className="glass p-4 border-l-4 border-l-amber-500">
                <div className="flex items-center gap-2 text-amber-400 mb-2">
                  <Lock className="w-4 h-4" />
                  <span className="font-bold text-sm">Login Required</span>
                </div>
                <p className="text-xs text-slate-400 mb-3">
                  Authorize Upstox to start trading.
                </p>
                <button
                  onClick={() => setShowAuthModal(true)}
                  className="w-full py-3 bg-amber-500 text-black font-bold text-sm rounded-xl active:scale-95 transition-transform flex items-center justify-center gap-2"
                >
                  <ExternalLink className="w-4 h-4" /> LOGIN TO UPSTOX
                </button>
              </div>
            ) : null}
          </div>

          {/* MIDDLE — engine + P&L */}
          <div
            className={`md:col-span-1 space-y-4 ${
              activeTab !== "live" ? "hidden md:block" : ""
            }`}
          >
            <PnlSparkline pnl={pnlToday} />
            <EngineStatePanel state={engineState} />
          </div>

          {/* RIGHT — near-miss feed */}
          <div
            className={`md:col-span-1 ${
              activeTab !== "live" ? "hidden md:block" : ""
            }`}
          >
            <NearMissFeed payload={nearMiss} />
          </div>

          {/* LOGS — full width */}
          <div
            className={`md:col-span-3 ${
              activeTab !== "logs" ? "hidden md:block" : ""
            }`}
          >
            <div
              className="glass overflow-hidden flex flex-col"
              style={{ height: "min(360px, 50vh)" }}
            >
              <div className="flex items-center gap-2 p-3 border-b border-white/5 bg-black/20">
                <Terminal className="w-4 h-4 text-emerald-400" />
                <span className="text-xs font-bold text-slate-400">
                  Live Logs · {activeBot.replace("_REGIME", " REGIME")}
                </span>
              </div>
              <div className="flex-1 p-3 font-mono text-[11px] leading-relaxed overflow-y-auto bg-black/30">
                {logs ? (
                  logs.split("\n").map((line, i) => (
                    <div
                      key={i}
                      className={`py-0.5 break-all ${
                        line.includes("ERROR")
                          ? "text-rose-400"
                          : line.includes("WARNING")
                          ? "text-amber-400"
                          : line.includes("INFO")
                          ? "text-slate-300"
                          : "text-slate-600"
                      }`}
                    >
                      {line}
                    </div>
                  ))
                ) : (
                  <div className="text-slate-600 text-center pt-8">
                    Waiting for logs...
                  </div>
                )}
                <div ref={logEndRef} />
              </div>
            </div>
          </div>

          {/* TRADES — full width */}
          <div
            className={`md:col-span-3 ${
              activeTab !== "trades" ? "hidden md:block" : ""
            }`}
          >
            <TradesTable trades={stats?.trade_history ?? []} />
          </div>
        </div>
      </main>

      {/* ── AUTH MODAL ── */}
      <AnimatePresence>
        {showAuthModal && (
          <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setShowAuthModal(false)}
              className="absolute inset-0 bg-black/80"
            />
            <motion.div
              initial={{ y: 100, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: 100, opacity: 0 }}
              transition={{ type: "spring", damping: 25, stiffness: 300 }}
              className="glass w-full md:max-w-md relative z-10 p-6 md:p-8 rounded-t-[2rem] md:rounded-[2rem] border-t border-white/10 space-y-5"
            >
              <div className="md:hidden w-10 h-1 bg-white/20 rounded-full mx-auto -mt-1 mb-2" />
              <div className="text-center space-y-1">
                <div className="w-12 h-12 bg-amber-500/15 rounded-2xl flex items-center justify-center mx-auto">
                  <Shield className="w-6 h-6 text-amber-500" />
                </div>
                <h2 className="text-lg font-black">UPSTOX LOGIN</h2>
                <p className="text-xs text-slate-400">Connect your broker account</p>
              </div>
              {!authUrl ? (
                <>
                  <button
                    onClick={startLogin}
                    className="w-full py-4 bg-blue-600 text-white font-bold rounded-2xl active:scale-95 transition-transform flex items-center justify-center gap-2"
                  >
                    <ExternalLink className="w-5 h-5" /> OPEN UPSTOX LOGIN
                  </button>
                  {authError ? (
                    <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-300 text-xs font-medium flex items-start gap-2">
                      <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                      <span className="break-words">{authError}</span>
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="space-y-3">
                  <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-400 text-xs font-medium flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                    <span>
                      If a new tab didn't open automatically (your browser may
                      block popups), click the link below. After logging in,
                      copy the full URL from the browser's address bar and
                      paste it in the box.
                    </span>
                  </div>
                  <a
                    href={authUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block w-full py-4 bg-blue-600 hover:bg-blue-500 text-white font-bold text-center rounded-2xl active:scale-95 transition-transform"
                  >
                    OPEN UPSTOX LOGIN PAGE →
                  </a>
                  <input
                    type="text"
                    value={redirectUrl}
                    onChange={(e) => setRedirectUrl(e.target.value)}
                    placeholder="Paste the redirect URL here..."
                    className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3.5 text-sm focus:outline-none focus:border-blue-500 transition-colors"
                  />
                  <button
                    onClick={submitAuth}
                    disabled={authLoading || !redirectUrl}
                    className="w-full py-4 bg-emerald-500 disabled:bg-slate-700 text-black font-bold rounded-2xl active:scale-95 transition-transform flex items-center justify-center gap-2"
                  >
                    {authLoading ? (
                      <RefreshCw className="w-5 h-5 animate-spin" />
                    ) : (
                      <Zap className="w-5 h-5" />
                    )}
                    {authLoading ? "VERIFYING..." : "COMPLETE LOGIN"}
                  </button>
                  <button
                    onClick={() => setAuthUrl("")}
                    className="w-full text-center text-xs text-slate-500 py-1"
                  >
                    Restart
                  </button>
                </div>
              )}
              <button
                onClick={() => setShowAuthModal(false)}
                className="absolute top-4 right-4 p-2 text-slate-500 active:scale-90 transition-transform"
              >
                <XCircle className="w-5 h-5" />
              </button>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}

// --- Stat Card ---
function StatCard({
  label, value, sub, icon, valueColor,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ReactNode;
  valueColor?: string;
}) {
  return (
    <div className="glass p-3 md:p-5 space-y-1 md:space-y-2 animate-fade-in">
      <div className="flex items-center gap-1.5">
        <div className="p-1.5 md:p-2 bg-white/5 rounded-lg">{icon}</div>
        <span className="text-[10px] md:text-xs text-slate-500 font-bold uppercase">
          {label}
        </span>
      </div>
      <div className={`text-base md:text-2xl font-black truncate ${valueColor || ""}`}>
        {value}
      </div>
      {sub && <div className="text-[10px] md:text-xs text-slate-500 truncate">{sub}</div>}
    </div>
  );
}
