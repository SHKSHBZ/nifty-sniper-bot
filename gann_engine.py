import math

class GannSquareOf9:
    def __init__(self, base_price: float):
        """Initializes the Gann engine with a reference anchor price (yesterday's close)."""
        if base_price <= 0:
            raise ValueError("Base price must be greater than zero.")
        self.base_price = base_price
        self.sqrt_base = math.sqrt(base_price)
        # Generate 24 levels of angles (from 45 to 1080 degrees)
        self.angles = [i * 45 for i in range(1, 25)]
        self.levels = self.generate_levels()

    def generate_levels(self) -> dict:
        """Generates dictionary mapping specific structural angles to exact price points."""
        levels = {"base": self.base_price, "buy": {}, "sell": {}}

        for angle in self.angles:
            factor = angle / 360.0
            # Upside Resistance Calculations
            levels["buy"][angle] = round((self.sqrt_base + factor) ** 2, 2)
            # Downside Support Calculations
            levels["sell"][angle] = round((self.sqrt_base - factor) ** 2, 2)

        return levels

    def get_active_levels(self, open_spot: float) -> dict:
        """
        Finds the Gann levels just above and below the opening spot price,
        and returns dynamic triggers, targets, and stop losses.
        """
        # Build unified sorted list of all Gann price levels
        all_prices = []
        for angle in sorted(self.angles, reverse=True):
            all_prices.append(self.levels["sell"][angle])
        all_prices.append(self.base_price)
        for angle in sorted(self.angles):
            all_prices.append(self.levels["buy"][angle])

        all_prices = sorted(list(set(all_prices)))

        # Find where open_spot fits
        lower_idx = -1
        for i, p in enumerate(all_prices):
            if p <= open_spot:
                lower_idx = i
            else:
                break

        # Fallback bounds handling
        if lower_idx < 1:
            lower_idx = 1
        if lower_idx > len(all_prices) - 3:
            lower_idx = len(all_prices) - 3

        # Map dynamic triggers, targets, and SLs
        ce_trigger = all_prices[lower_idx + 1]
        ce_target = all_prices[lower_idx + 2]
        ce_sl = all_prices[lower_idx]

        pe_trigger = all_prices[lower_idx]
        pe_target = all_prices[lower_idx - 1]
        pe_sl = all_prices[lower_idx + 1]

        return {
            "ce_trigger": ce_trigger,
            "ce_target": ce_target,
            "ce_sl": ce_sl,
            "pe_trigger": pe_trigger,
            "pe_target": pe_target,
            "pe_sl": pe_sl
        }
