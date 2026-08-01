# Workspace Rules

### Options Trading Logic & Stop-Losses

1. **Why Profits Occur (The Winning Setup)**:
   The highest probability profits occur when directional entries are triggered purely by Structural Breakouts/Reversals (Spot Levels) that are strictly validated by **Live OI Accumulation**. Exits should only happen when the option premium itself reaches its historical ceiling (Pillar 4 Resistance).

2. **Why Whipsaw Losses Occur (The Trap)**:
   Most false stop-outs happen when stop-losses are bound to arbitrary underlying Spot distances (e.g., exiting because Spot dropped 15 points). Spot prices are noisy. An option's premium can stay completely flat (supported by writers) even while the Spot price swings wildly.

3. **Proper Premium S/R Tracking (The Solution)**:
   Never use rigid point-based spot reversals as a primary stop loss for options. Instead, you MUST map the **True Premium Floor** (the historical lowest traded premium for that structure) at the moment of entry. The hard stop-loss is placed strictly at this Premium Floor. The trade is only invalidated if the premium itself breaks structural support.

4. **Volatility Crush (Chop Filter)**:
   Sideways markets bleed Option Buyers via Theta Decay. Bots must track the combined Straddle Premium (CE+PE). If the straddle decays significantly over 15 minutes while Spot remains range-bound, it signals a Volatility Crush. Option Buyer bots must lock down entirely during this phase. Option Seller bots should activate.
