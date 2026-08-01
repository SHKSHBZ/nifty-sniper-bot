# Project: Option Seller Bot

## Architecture & Overview
The Option Seller Bot is designed to operate in sideways markets and volatility crushes to profit from Theta Decay, serving as the counter-strategy / hedge to the directional Option Buyer bot.

## Requirements Mapping
- **R1: Multi-Regime Execution**
  - **Volatility Crush Mode**: Activates when directional bot is locked out due to chop/volatility crush (declining straddle premium >3% over 15m while spot range <30 pts) to sell short straddles/strangles. (DONE - Verified & Remediated in M1)
  - **Directional Hedging Mode**: Activates when directional bot buys CE/PE to simultaneously short the opposing leg (e.g. short PE when buying CE) to harvest theta decay on the losing counter-leg. (DONE - Verified & Remediated in M1)
- **R2: Short Premium Strategy Execution**
  - Dynamically execute Short Straddles (ATM CE + PE) and Short Strangles (OTM CE + PE). (DONE - Verified & Remediated in M1)
- **R3: Strict Risk Management for Sellers**
  - Hard stop-losses on short legs (exiting if combined/individual premium spikes by X%, or individual leg breaches structural resistance ceiling, or Spot breaks sideways range). (IN_PROGRESS - M2)
  - Proper Premium S/R Tracking: Map True Premium Floor at entry (AGENTS.md Rule 3). (IN_PROGRESS - M2)
  - Volatility Crush Chop Filter: Track straddle premium decay over 15 min (AGENTS.md Rule 4). (IN_PROGRESS - M2)

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 0 | Exploration & Arch Blueprint | Codebase investigation & architecture blueprint | None | DONE |
| 1 | Multi-Regime & Strategy Core | R1, R2: Volatility Crush Mode, Directional Hedging Mode, Short Straddle & Strangle execution, SELL order executor | M0 | DONE |
| 2 | Risk Management & Premium Floor Engine | R3 & AGENTS.md: Hard SL (Spike %, Ceiling Breach, Spot Range Breakout), True Premium Floor Tracker, 15m Straddle Decay Filter | M1 | IN_PROGRESS |
| 3 | E2E Integration & Verification | Integration into `main.py` pipeline, Pytest suite `tests/test_option_seller_bot.py`, Forensic Audit verification | M2 | PLANNED |

## Interface Contracts
- **Signal & Regime Engine ↔ Seller Engine**: `VOLATILITY_CRUSH` signal locks out buyer (`buyer_locked = True`) and triggers short straddle/strangle tactics. Directional buyer entries emit `DIRECTIONAL_BUY_CE` / `DIRECTIONAL_BUY_PE` payloads triggering short PE / short CE counter-legs.
- **Option Seller Engine ↔ Order Executor**: `place_short_option_order` & `place_short_spread` with SELL limit buffer `LTP * (1 - limit_buffer_pct)`. Position-closing orders bypass initial opening margin checks and partial leg failures execute compensating rollbacks.
- **Option Seller Engine ↔ Risk Manager**: `OptionSellerRiskManager` evaluates live premium LTPs against entry credit, premium floor/ceiling, and spot range bounds. Emits exit signals on SL/TP/Breakout.

## Code Layout
- `regime/classifier.py`, `regime/router.py`, `regime/dispatcher.py`, `oi_flow_engine.py`: Regime classification & Volatility Crush detection.
- `order_executor.py`, `executor/paper.py`: SELL order execution & multi-leg payload support with compensating rollback.
- `tactics/seller_tactics.py` & `regime/seller_engine.py`: Short Straddle, Short Strangle, and Directional Hedge tactics.
- `risk/seller_risk.py` & `premium_analyzer.py`: Option seller risk management, True Premium Floor, and Spot Range breakout monitoring.
- `main.py`: Pipeline integration for live/paper execution.
- `tests/`: Unit tests (`test_option_seller_m1.py`, `test_empirical_challenger_m1.py`, `test_empirical_m1_challenger.py`, `test_option_seller_m2_risk.py`).
