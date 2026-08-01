// Shared types — must mirror backend/server.py response shapes.
// Update both sides when changing fields.

export interface Position {
  entry_time: string;
  trade_type: string;
  strike: number;
  opt_type: string;
  entry_price: number;
  qty: number;
  sl_price: number;
  target_price: number;
  is_expiry_day?: boolean;
  tactic_name?: string;
  tsl_active?: boolean;
  dynamic_sl?: number;
  sl_pct?: number;
  tp_pct?: number;
}

export interface Trade {
  entry_time: string;
  exit_time: string;
  trade_type: string;
  strike?: number;
  entry_price?: number;
  exit_price?: number;
  pnl: number;
  reason: string;
  strikes?: number[];
  entry_premiums?: Record<string, number>;
  exit_premiums?: Record<string, number>;
}

export interface PortfolioStats {
  capital: number;
  open_position: Position | null;
  trade_history: Trade[];
}

export interface BotStatus {
  name: string;
  status: 'running' | 'stopped';
  pid: number | null;
}

export interface IndexQuote {
  symbol: string;
  instrument_key: string;
  ltp: number;
  prev_close: number;
  change: number;
  change_pct: number;
  ts: string;
  stale: boolean;
}

export interface LastSignal {
  ts: string;
  direction: 'CE' | 'PE' | null;
  tactic_name: string | null;
  reasons: string[];
  near_miss_count: number;
}

export interface EngineState {
  available?: boolean;
  bot_type?: string;
  index?: string;
  engine_mode?: string;
  regime?: string;
  spot?: number;
  vwap?: number;
  ema9_5m?: number;
  ema21_5m?: number;
  atr_5m?: number;
  day_open?: number;
  day_high?: number;
  day_low?: number;
  or_high?: number;
  or_low?: number;
  focus_pcr?: number;
  support_strike?: number;
  resistance_strike?: number;
  vix_level?: number;
  ce_oi_change?: number;
  pe_oi_change?: number;
  in_position?: boolean;
  open_position?: Position | null;
  last_signal?: LastSignal | null;
  missed_today_count?: number;
  is_market_open?: boolean;
  journal_day_started?: boolean;
  last_update_ts?: string;
}

export interface MissedEntry {
  ts: string;
  tactic: string;
  direction: 'CE' | 'PE';
  blocked_by: string;
  blocker_detail: string;
  hypothetical_strike: number;
  hypothetical_entry_premium: number;
  hypothetical_exit_premium: number;
  hypothetical_pnl: number;
  hypothetical_outcome: '' | 'WIN' | 'LOSS' | 'BREAKEVEN' | 'UNKNOWN';
  hypothetical_explanation: string;
  sl_pct: number;
  tp_pct: number;
  time_stop_min: number;
  poll_count: number;
  regime?: string;
  spot_at_miss?: number | string;
}

export interface NearMissPayload {
  available?: boolean;
  bot_type?: string;
  index?: string;
  last_update_ts?: string;
  missed: MissedEntry[];
}

export interface PnlPoint {
  ts: string;
  pnl: number;
  cumulative_pnl: number;
}

export interface PnlToday {
  date: string;
  bot_type: string;
  starting_capital: number;
  current_capital: number;
  realized_pnl: number;
  trade_count: number;
  win_count: number;
  loss_count: number;
  trades: Trade[];
  pnl_timeseries: PnlPoint[];
}

export interface StreamTick {
  type: 'tick' | 'error';
  ts?: string;
  indices?: IndexQuote[];
  engine_state?: EngineState | null;
  near_miss?: NearMissPayload | null;
  pnl?: PnlToday;
  status?: BotStatus[];
  message?: string;
}

export type BotName = 'NIFTY' | 'SENSEX' | 'NIFTY_SELLER';
