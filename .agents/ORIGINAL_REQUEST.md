# Original User Request

## Initial Request — 2026-08-01T16:54:41Z

An Option Seller Bot that specifically activates during sideways markets and volatility crushes to profit from Theta Decay, acting as the counter-strategy to the directional Option Buyer bot.

Working directory: `C:\Users\shaik\OneDrive\Desktop\New folder\option_seller_bot`
Integrity mode: development

## Requirements

### R1. Multi-Regime Execution
The Option Seller bot should operate in two distinct modes:
1. **Volatility Crush Mode**: When the directional bot is locked out due to sideways chop, the Option Seller bot activates to sell straddles/strangles.
2. **Directional Hedging Mode**: When the directional bot takes a trade (e.g., buys a CE), the Option Seller bot simultaneously shorts the opposing leg (e.g., shorts the PE) to double the profits by capturing theta decay on the losing side.

### R2. Short Premium Strategy Execution
The bot should dynamically execute Short Straddles (selling ATM CE and PE) or Short Strangles (selling OTM CE and PE) to harvest decaying premiums.

### R3. Strict Risk Management for Sellers
Option selling carries theoretically unlimited risk. The bot must implement hard stop-losses on the short legs (e.g., exiting if the combined premium spikes by X%, or individual legs spike beyond structural resistance).

## Acceptance Criteria

### Integration & Execution
- [ ] Programmatic verification: The bot can successfully receive a "Volatility Crush" signal and simulate opening a short straddle/strangle position.
- [ ] Risk Management: The bot successfully closes short positions for a loss if the underlying Spot breaks out of the defined sideways range, simulating a sudden volatility spike.
