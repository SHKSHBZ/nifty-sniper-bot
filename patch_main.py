import os
import re

main_path = 'main.py'
with open(main_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. premiums
old_premiums = """            # Build premium dict for position's direction
            if self.engine.position:
                pos_dir = self.engine.position["direction"]
                for s in self.engine.position["strikes"]:
                    premiums[int(s)] = ltp_map.get(f"{int(s)}_{pos_dir}", 0)"""
new_premiums = """            # Build premium dict for position's direction
            if self.engine.position:
                pos_dir = self.engine.position["direction"]
                for s in self.engine.position["strikes"]:
                    premiums[int(s)] = ltp_map.get(f"{int(s)}_{pos_dir}", 0)
            
            if getattr(self.engine, 'ic_position', None):
                for leg_key, s_val in self.engine.ic_position["strikes"].items():
                    s_int = int(s_val)
                    opt_dir = "CE" if "ce" in leg_key else "PE"
                    premiums[f"{s_int}_{opt_dir}"] = ltp_map.get(f"{s_int}_{opt_dir}", 0)"""
content = content.replace(old_premiums, new_premiums)

# 2. Main tick
old_tick = """        # Main tick
        action = self.engine.tick(spot, now, oi_snapshot, premiums, self.fetcher)

        if action is None:
            return

        if action["action"] == "entry":
            self._execute_oi_flow_entry(action, action.get("premiums", {}), now)
        elif action["action"] == "exit":
            self._execute_oi_flow_exit(action["reason"], premiums, now)
        elif action["action"] == "partial_exit":
            self._execute_oi_flow_partial(premiums, now)"""

new_tick = """        # Main tick
        actions = self.engine.tick(spot, now, oi_snapshot, premiums, self.fetcher)

        if not actions:
            return

        for action in actions:
            if action["action"] == "entry":
                self._execute_oi_flow_entry(action, action.get("premiums", {}), now)
            elif action["action"] == "exit":
                self._execute_oi_flow_exit(action["reason"], premiums, now)
            elif action["action"] == "partial_exit":
                self._execute_oi_flow_partial(premiums, now)
            elif action["action"] == "entry_ic":
                self._execute_ic_entry(action, action.get("premiums", {}), now)
            elif action["action"] == "exit_ic":
                self._execute_ic_exit(action["reason"], action.get("credit", 0.0), action.get("exit_cost", 0.0), now)"""
content = content.replace(old_tick, new_tick)

# 3. Add methods
methods = """
    def _execute_ic_entry(self, signal: dict, premiums: dict, now):
        strikes = signal["strikes"]
        lots = 12
        self.engine.open_ic_position(signal, premiums, now, lots)
        credit = signal.get("credit", 0.0)
        logger.info(f"[OI-Flow] IRON CONDOR ENTRY | Credit: {credit:.1f} | Lots: {lots}")
        trade = {
            "entry_time": now.isoformat(),
            "trade_type": "IRON_CONDOR",
            "strike": 0,
            "opt_type": "IC",
            "entry_price": credit,
            "qty": lots * self.lot_size,
            "sl_price": credit * 1.5,
            "target_price": credit * 0.5,
        }
        self.portfolio["open_position"] = trade

    def _execute_ic_exit(self, reason: str, entry_credit: float, exit_cost: float, now):
        lots = 12
        if self.engine.ic_position:
            lots = self.engine.ic_position.get("lots", 12)
        pnl = (entry_credit - exit_cost) * lots * self.lot_size
        ic_signal = {"close_cost": exit_cost, "reason": reason}
        self.engine.close_ic_position(ic_signal, {}, now)
        self.portfolio["capital"] += pnl
        if "trade_history" not in self.portfolio:
            self.portfolio["trade_history"] = []
        trade = self.portfolio.get("open_position", {})
        trade.update({
            "exit_time": now.isoformat(),
            "exit_price": exit_cost,
            "pnl": pnl,
            "reason": reason
        })
        self.portfolio["trade_history"].append(trade)
        self.portfolio["open_position"] = None
        logger.info(f"[OI-Flow] IRON CONDOR EXIT | Reason: {reason} | Exit Cost: {exit_cost:.1f} | P&L: Rs.{pnl:.0f}")

    def _run_oi_flow_tick(self, now):"""

content = content.replace("    def _run_oi_flow_tick(self, now):", methods)

# 4. Sleep
old_sleep = """                  if self.engine_mode == "oi_flow":
                      self._run_oi_flow_tick(now)
                      if self.engine.position is not None:
                          time.sleep(3)"""
new_sleep = """                  if self.engine_mode == "oi_flow":
                      self._run_oi_flow_tick(now)
                      if self.engine.position is not None or getattr(self.engine, 'ic_position', None) is not None:
                          time.sleep(3)"""
content = content.replace(old_sleep, new_sleep)

with open(main_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated main.py successfully')
