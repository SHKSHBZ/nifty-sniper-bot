"use client";

import React, { useState, useEffect, useRef } from 'react';
import { 
  Play, Square, TrendingUp, TrendingDown, 
  Terminal, Shield, Zap, RefreshCw,
  Lock, ExternalLink, CheckCircle2, XCircle, Wifi, WifiOff
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';


// --- Types ---
interface Position {
  entry_time: string;
  trade_type: string;
  strike: number;
  opt_type: string;
  entry_price: number;
  qty: number;
  sl_price: number;
  target_price: number;
}

interface Trade {
  entry_time: string;
  exit_time: string;
  trade_type: string;
  strike: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  reason: string;
}

interface Stats {
  capital: number;
  open_position: Position | null;
  trade_history: Trade[];
}

interface BotStatus {
  name: string;
  status: 'running' | 'stopped';
  pid: number | null;
}

export default function Dashboard() {
  const [apiBase, setApiBase] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [statuses, setStatuses] = useState<BotStatus[]>([]);
  const [isAuth, setIsAuth] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [logs, setLogs] = useState("");
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [authUrl, setAuthUrl] = useState("");
  const [redirectUrl, setRedirectUrl] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'control' | 'logs' | 'trades'>('control');
  const [activeLogBot, setActiveLogBot] = useState<'NIFTY' | 'SENSEX' | 'NIFTY_SCALPER' | 'SENSEX_SCALPER'>('SENSEX_SCALPER');
  const logEndRef = useRef<HTMLDivElement>(null);

  // Determine API base URL on client mount
  useEffect(() => {
    const host = window.location.hostname;
    const isHttps = window.location.protocol === 'https:';
    const base = isHttps ? `https://${host}:8443` : `http://${host}:8000`;
    setApiBase(base);
  }, []);

  // --- Fetchers (only run when apiBase is set) ---
  const fetchAll = async (base: string, bot: string) => {
    try {
      const res = await fetch(`${base}/stats/${bot}`);
      if (res.ok) { setStats(await res.json()); setIsConnected(true); }
    } catch { setIsConnected(false); }

    try {
      const res = await fetch(`${base}/status`);
      if (res.ok) setStatuses(await res.json());
    } catch {}

    try {
      const res = await fetch(`${base}/auth/status`);
      if (res.ok) { const d = await res.json(); setIsAuth(d.is_authenticated); }
    } catch {}
  };

  const fetchLogs = async (base: string, bot: string) => {
    try {
      const res = await fetch(`${base}/logs/${bot}`);
      if (res.ok) { const d = await res.json(); setLogs(d.logs); }
    } catch {}
  };

  // Only start polling once apiBase is ready
  useEffect(() => {
    if (!apiBase) return;
    fetchAll(apiBase, activeLogBot);
    const i = setInterval(() => fetchAll(apiBase, activeLogBot), 3000);
    return () => clearInterval(i);
  }, [apiBase, activeLogBot]);

  useEffect(() => {
    if (!apiBase) return;
    fetchLogs(apiBase, activeLogBot);
    const i = setInterval(() => fetchLogs(apiBase, activeLogBot), 3000);
    return () => clearInterval(i);
  }, [apiBase, activeLogBot]);
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);

  const toggleBot = async (name: string, isRunning: boolean) => {
    if (!isAuth && !isRunning) { setShowAuthModal(true); return; }
    const action = isRunning ? 'stop' : 'start';
    setLoading(prev => ({ ...prev, [name]: true }));
    try {
      await fetch(`${apiBase}/${action}/${name}`, { method: 'POST' });
      await fetchAll(apiBase, activeLogBot);
    } catch {}
    setLoading(prev => ({ ...prev, [name]: false }));
  };

  const startLogin = async () => {
    try {
      const res = await fetch(`${apiBase}/auth/url`);
      if (res.ok) { const d = await res.json(); setAuthUrl(d.url); window.open(d.url, '_blank'); }
    } catch { alert("Cannot reach server. Check connection."); }
  };

  const submitAuth = async () => {
    if (!redirectUrl) return;
    setAuthLoading(true);
    try {
      const res = await fetch(`${apiBase}/auth/submit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: redirectUrl })
      });
      if (res.ok) { setIsAuth(true); setShowAuthModal(false); setRedirectUrl(""); setAuthUrl(""); }
      else { const e = await res.json(); alert("Failed: " + (e.detail || "Unknown")); }
    } catch { alert("Connection error."); }
    setAuthLoading(false);
  };

  const totalPnl = stats?.trade_history.reduce((a, t) => a + t.pnl, 0) || 0;

  return (
    <div className="min-h-screen flex flex-col">
      {/* ── HEADER ── */}
      <header className="sticky top-0 z-40 glass border-b border-white/5 px-4 py-3 md:px-8 md:py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="min-w-0">
            <h1 className="text-lg md:text-2xl font-black tracking-tight bg-gradient-to-r from-blue-400 to-violet-400 bg-clip-text text-transparent truncate">
              SNIPER BOT
            </h1>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {/* Connection */}
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 text-[11px] font-bold">
              {isConnected 
                ? <><Wifi className="w-3.5 h-3.5 text-emerald-400" /><span className="text-emerald-400 hidden sm:inline">LIVE</span></>
                : <><WifiOff className="w-3.5 h-3.5 text-rose-400" /><span className="text-rose-400 hidden sm:inline">OFFLINE</span></>
              }
            </div>
            {/* Auth */}
            <button 
              onClick={() => setShowAuthModal(true)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 text-[11px] font-bold active:scale-95 transition-transform"
            >
              {isAuth 
                ? <><CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /><span className="text-emerald-400 hidden sm:inline">AUTH</span></>
                : <><Lock className="w-3.5 h-3.5 text-amber-400" /><span className="text-amber-400">LOGIN</span></>
              }
            </button>
            {/* Refresh */}
            <button onClick={() => fetchAll(apiBase, activeLogBot)} className="p-1.5 rounded-lg bg-white/5 active:scale-90 transition-transform">
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </header>

      {/* ── STAT CARDS ── */}
      <section className="px-4 md:px-8 pt-4 md:pt-6">
        <div className="max-w-6xl mx-auto grid grid-cols-3 gap-2 md:gap-4">
          <StatCard 
            label={`Capital (${activeLogBot.replace('_SCALPER', ' SCALP')})`} 
            value={`₹${(stats?.capital || 0).toLocaleString('en-IN', {maximumFractionDigits: 0})}`}
            icon={<Shield className="w-4 h-4 md:w-5 md:h-5 text-blue-400" />}
          />
          <StatCard 
            label="P&L"
            value={`${totalPnl >= 0 ? '+' : ''}₹${totalPnl.toLocaleString('en-IN', {maximumFractionDigits: 0})}`}
            icon={totalPnl >= 0 ? <TrendingUp className="w-4 h-4 md:w-5 md:h-5 text-emerald-400" /> : <TrendingDown className="w-4 h-4 md:w-5 md:h-5 text-rose-400" />}
            valueColor={totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}
          />
          <StatCard 
            label="Position"
            value={stats?.open_position ? `${stats.open_position.strike}` : "—"}
            sub={stats?.open_position ? stats.open_position.trade_type : "No trade"}
            icon={<Zap className={`w-4 h-4 md:w-5 md:h-5 ${stats?.open_position ? 'text-amber-400' : 'text-slate-500'}`} />}
          />
        </div>
      </section>

      {/* ── MOBILE TAB BAR ── */}
      <div className="md:hidden px-4 pt-4">
        <div className="flex rounded-xl bg-white/5 p-1 gap-1">
          {(['control', 'logs', 'trades'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 py-2.5 text-xs font-bold uppercase rounded-lg transition-all ${
                activeTab === tab ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/25' : 'text-slate-400'
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
          
          {/* Control Panel */}
          <div className={`md:col-span-1 space-y-3 ${activeTab !== 'control' ? 'hidden md:block' : ''}`}>
            {["NIFTY", "SENSEX", "NIFTY_SCALPER", "SENSEX_SCALPER"].map(bot => {
              const s = statuses.find(x => x.name === bot);
              const running = s?.status === 'running';
              return (
                <div key={bot} className="glass p-4 md:p-5 flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="font-bold text-sm md:text-base">{bot}</div>
                    <div className={`text-[11px] font-bold uppercase ${running ? 'text-emerald-400' : 'text-slate-500'}`}>
                      {running ? '● Running' : '○ Stopped'}
                    </div>
                  </div>
                  <button
                    onClick={() => toggleBot(bot, running)}
                    disabled={loading[bot]}
                    className={`w-14 h-14 rounded-2xl flex items-center justify-center transition-all active:scale-90 flex-shrink-0 ${
                      running 
                        ? 'bg-rose-500 shadow-lg shadow-rose-500/30' 
                        : 'bg-emerald-500 shadow-lg shadow-emerald-500/30'
                    } ${loading[bot] ? 'opacity-50' : ''}`}
                  >
                    {loading[bot] 
                      ? <RefreshCw className="w-6 h-6 text-white animate-spin" /> 
                      : running 
                        ? <Square className="w-6 h-6 text-white fill-white" /> 
                        : <Play className="w-6 h-6 text-white fill-white" />
                    }
                  </button>
                </div>
              );
            })}

            {/* Auth Warning */}
            {!isAuth && (
              <div className="glass p-4 border-l-4 border-l-amber-500">
                <div className="flex items-center gap-2 text-amber-400 mb-2">
                  <Lock className="w-4 h-4" />
                  <span className="font-bold text-sm">Login Required</span>
                </div>
                <p className="text-xs text-slate-400 mb-3">Authorize Upstox to start trading.</p>
                <button 
                  onClick={() => setShowAuthModal(true)}
                  className="w-full py-3 bg-amber-500 text-black font-bold text-sm rounded-xl active:scale-95 transition-transform flex items-center justify-center gap-2"
                >
                  <ExternalLink className="w-4 h-4" /> LOGIN TO UPSTOX
                </button>
              </div>
            )}
          </div>

          {/* Logs */}
          <div className={`md:col-span-2 space-y-3 ${activeTab !== 'logs' ? 'hidden md:block' : ''}`}>
            <div className="glass overflow-hidden flex flex-col" style={{ height: 'min(400px, 50vh)' }}>
              <div className="flex items-center gap-2 p-3 border-b border-white/5 bg-black/20">
                <Terminal className="w-4 h-4 text-emerald-400" />
                <span className="text-xs font-bold text-slate-400">Live Logs</span>
                <div className="flex items-center ml-auto gap-1">
                   {["NIFTY", "SENSEX", "NIFTY_SCALPER", "SENSEX_SCALPER"].map(b => (
                     <button key={b} onClick={() => setActiveLogBot(b as any)} className={`text-[10px] px-2 py-1 rounded transition-colors ${activeLogBot === b ? 'bg-blue-600/50 text-white font-bold' : 'bg-white/5 text-slate-400 hover:bg-white/10'}`}>
                       {b.replace("_SCALPER", " SCALP")}
                     </button>
                   ))}
                </div>
              </div>
              <div className="flex-1 p-3 font-mono text-[11px] leading-relaxed overflow-y-auto bg-black/30">
                {logs ? logs.split('\n').map((line, i) => (
                  <div key={i} className={`py-0.5 break-all ${
                    line.includes('ERROR') ? 'text-rose-400' : 
                    line.includes('WARNING') ? 'text-amber-400' :
                    line.includes('INFO') ? 'text-slate-300' : 'text-slate-600'
                  }`}>{line}</div>
                )) : (
                  <div className="text-slate-600 text-center pt-8">Waiting for logs...</div>
                )}
                <div ref={logEndRef} />
              </div>
            </div>
          </div>

          {/* Trades (full width on desktop) */}
          <div className={`md:col-span-3 ${activeTab !== 'trades' ? 'hidden md:block' : ''}`}>
            <div className="text-sm font-bold text-slate-400 mb-2 flex items-center gap-2">
              Trade History
              <span className="text-[10px] bg-white/5 px-1.5 py-0.5 rounded">
                {stats?.trade_history.length || 0}
              </span>
            </div>

            {/* Mobile: Card view */}
            <div className="md:hidden space-y-2">
              {stats?.trade_history.length ? stats.trade_history.slice().reverse().map((t, i) => (
                <div key={i} className="glass p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-bold text-sm">{t.trade_type}</span>
                    <span className={`font-black text-sm ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {t.pnl >= 0 ? '+' : ''}₹{t.pnl.toFixed(0)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>Strike {t.strike}</span>
                    <span>₹{t.entry_price} → ₹{t.exit_price}</span>
                  </div>
                  <div className="text-[10px] text-slate-500 mt-1">{t.reason}</div>
                </div>
              )) : (
                <div className="glass p-8 text-center text-slate-600 text-sm">No trades yet</div>
              )}
            </div>

            {/* Desktop: Table view */}
            <div className="hidden md:block glass overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="bg-black/20 text-slate-500 text-xs uppercase">
                    <tr>
                      <th className="px-4 py-3">Time</th>
                      <th className="px-4 py-3">Type</th>
                      <th className="px-4 py-3">Strike</th>
                      <th className="px-4 py-3">Entry/Exit</th>
                      <th className="px-4 py-3">P&L</th>
                      <th className="px-4 py-3">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {stats?.trade_history.slice().reverse().map((t, i) => (
                      <tr key={i} className="hover:bg-white/5">
                        <td className="px-4 py-3 text-slate-400">{new Date(t.exit_time).toLocaleTimeString()}</td>
                        <td className="px-4 py-3 font-bold">{t.trade_type}</td>
                        <td className="px-4 py-3">{t.strike}</td>
                        <td className="px-4 py-3">₹{t.entry_price} → ₹{t.exit_price}</td>
                        <td className={`px-4 py-3 font-bold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {t.pnl >= 0 ? '+' : ''}₹{t.pnl.toFixed(2)}
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-500">{t.reason}</td>
                      </tr>
                    ))}
                    {(!stats || !stats.trade_history.length) && (
                      <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-600">No trades yet</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

        </div>
      </main>

      {/* ── AUTH MODAL ── */}
      <AnimatePresence>
        {showAuthModal && (
          <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center">
            <motion.div 
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
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
              {/* Drag handle (mobile) */}
              <div className="md:hidden w-10 h-1 bg-white/20 rounded-full mx-auto -mt-1 mb-2" />

              <div className="text-center space-y-1">
                <div className="w-12 h-12 bg-amber-500/15 rounded-2xl flex items-center justify-center mx-auto">
                  <Shield className="w-6 h-6 text-amber-500" />
                </div>
                <h2 className="text-lg font-black">UPSTOX LOGIN</h2>
                <p className="text-xs text-slate-400">Connect your broker account</p>
              </div>

              {!authUrl ? (
                <button 
                  onClick={startLogin}
                  className="w-full py-4 bg-blue-600 text-white font-bold rounded-2xl active:scale-95 transition-transform flex items-center justify-center gap-2"
                >
                  <ExternalLink className="w-5 h-5" /> OPEN UPSTOX LOGIN
                </button>
              ) : (
                <div className="space-y-3">
                  <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-400 text-xs font-medium flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                    <span>Login page opened. Copy the URL from the address bar after logging in, then paste it below.</span>
                  </div>
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
                    {authLoading ? <RefreshCw className="w-5 h-5 animate-spin" /> : <Zap className="w-5 h-5" />}
                    {authLoading ? "VERIFYING..." : "COMPLETE LOGIN"}
                  </button>
                  <button onClick={() => setAuthUrl("")} className="w-full text-center text-xs text-slate-500 py-1">
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
function StatCard({ label, value, sub, icon, valueColor }: { 
  label: string; value: string; sub?: string; icon: React.ReactNode; valueColor?: string;
}) {
  return (
    <div className="glass p-3 md:p-5 space-y-1 md:space-y-2 animate-fade-in">
      <div className="flex items-center gap-1.5">
        <div className="p-1.5 md:p-2 bg-white/5 rounded-lg">{icon}</div>
        <span className="text-[10px] md:text-xs text-slate-500 font-bold uppercase">{label}</span>
      </div>
      <div className={`text-base md:text-2xl font-black truncate ${valueColor || ''}`}>{value}</div>
      {sub && <div className="text-[10px] md:text-xs text-slate-500 truncate">{sub}</div>}
    </div>
  );
}
