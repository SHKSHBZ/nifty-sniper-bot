import pandas as pd
import glob
import logging
from pathlib import Path

logger = logging.getLogger("PremiumAnalyzer")

class PremiumAnalyzer:
    def __init__(self, logs_dir="logs", index="nifty"):
        base_dir = Path(__file__).parent
        self.logs_dir = base_dir / logs_dir
        self.index = index.lower()
        
    def _load_week_data(self) -> pd.DataFrame:
        """Loads all focus_zone CSVs for the current expiry week."""
        # We look for all focus_zone files for this index
        pattern = f"focus_zone_{self.index}_expiry_*.csv"
        files = glob.glob(str(self.logs_dir / pattern))
        
        if not files:
            logger.warning(f"No focus_zone CSVs found for {self.index}.")
            return pd.DataFrame()
            
        dfs = []
        for f in files:
            try:
                df = pd.read_csv(f)
                dfs.append(df)
            except Exception as e:
                logger.error(f"Failed to read {f}: {e}")
                
        if not dfs:
            return pd.DataFrame()
            
        merged_df = pd.concat(dfs, ignore_index=True)
        if 'timestamp' in merged_df.columns:
            merged_df['timestamp'] = pd.to_datetime(merged_df['timestamp'], format='mixed', dayfirst=True)
            merged_df = merged_df.sort_values('timestamp')
            
        return merged_df

    def get_structural_spot_levels(self, window=20) -> dict:
        """
        Finds true structural support and resistance levels for the Spot price
        by identifying local maxima and minima.
        """
        df = self._load_week_data()
        if df.empty or 'spot' not in df.columns:
            return {"support": [], "resistance": []}
            
        spot_series = df['spot'].drop_duplicates().reset_index(drop=True)
        
        resistances = []
        supports = []
        
        for i in range(window, len(spot_series) - window):
            window_slice = spot_series.iloc[i-window : i+window]
            current_val = spot_series.iloc[i]
            
            if current_val == window_slice.max():
                resistances.append(current_val)
            elif current_val == window_slice.min():
                supports.append(current_val)
                
        cleaned_res = self._cluster_levels(resistances, threshold=15)
        cleaned_sup = self._cluster_levels(supports, threshold=15)
        
        return {
            "support": sorted(cleaned_sup),
            "resistance": sorted(cleaned_res)
        }

    def get_premium_historical_levels(self, strike: int, opt_type: str) -> dict:
        """
        Finds the absolute historical Support (floor) and Resistance (ceiling) 
        for a specific option premium.
        """
        df = self._load_week_data()
        if df.empty:
            return {"support": None, "resistance": None}
            
        strike_df = df[df['strike'] == strike]
        if strike_df.empty:
            return {"support": None, "resistance": None}
            
        col = 'ce_ltp' if opt_type.upper() == 'CE' else 'pe_ltp'
        if col not in strike_df.columns:
            return {"support": None, "resistance": None}
            
        min_prem = strike_df[col].min()
        max_prem = strike_df[col].max()
        
        return {
            "support": min_prem,
            "resistance": max_prem
        }
        
    def get_premium_at_spot_level(self, strike: int, opt_type: str, target_spot: float, tolerance: float = 10.0) -> float:
        """
        CORRELATION MAPPING:
        Finds what the Premium was worth the last time the Spot price was near the `target_spot`.
        """
        df = self._load_week_data()
        if df.empty:
            return 0.0
            
        strike_df = df[df['strike'] == strike]
        if strike_df.empty:
            return 0.0
            
        mask = (strike_df['spot'] >= target_spot - tolerance) & (strike_df['spot'] <= target_spot + tolerance)
        matched = strike_df[mask]
        
        if matched.empty:
            return 0.0
            
        col = 'ce_ltp' if opt_type.upper() == 'CE' else 'pe_ltp'
        recent_val = matched.iloc[-1][col]
        return recent_val

    def get_live_oi_change(self, strike: int, window_minutes: int = 15) -> str:
        """
        PILLAR 2: BREAKOUT VS REVERSAL CHECK
        Looks at the most recent OI data for the given strike over the last `window_minutes`.
        Returns "REVERSAL_CONFIRMED" if CE writing dominates (Resistance holding).
        Returns "BREAKOUT_WARNING" if PE writing dominates (Support pushing up).
        Returns "NEUTRAL" otherwise.
        """
        df = self._load_week_data()
        if df.empty:
            return "NEUTRAL"
            
        strike_df = df[df['strike'] == strike].copy()
        if strike_df.empty:
            return "NEUTRAL"
            
        # Get the most recent timestamp
        last_ts = strike_df['timestamp'].max()
        cutoff_ts = last_ts - pd.Timedelta(minutes=window_minutes)
        
        recent_df = strike_df[strike_df['timestamp'] >= cutoff_ts]
        if len(recent_df) < 2:
            return "NEUTRAL"
            
        ce_oi_start = recent_df.iloc[0]['ce_oi']
        ce_oi_end = recent_df.iloc[-1]['ce_oi']
        pe_oi_start = recent_df.iloc[0]['pe_oi']
        pe_oi_end = recent_df.iloc[-1]['pe_oi']
        
        ce_chg = ce_oi_end - ce_oi_start
        pe_chg = pe_oi_end - pe_oi_start
        
        if ce_chg > pe_chg * 1.5 and ce_chg > 0:
            return "REVERSAL_CONFIRMED" # Resistance is heavy
        elif pe_chg > ce_chg * 1.5 and pe_chg > 0:
            return "BREAKOUT_WARNING"   # Support is pushing up through resistance
            
        return "NEUTRAL"

    def _cluster_levels(self, levels: list, threshold: float) -> list:
        """Groups nearby levels together and takes the average."""
        if not levels:
            return []
            
        levels = sorted(levels)
        clusters = []
        current_cluster = [levels[0]]
        
        for i in range(1, len(levels)):
            if levels[i] - current_cluster[-1] <= threshold:
                current_cluster.append(levels[i])
            else:
                clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [levels[i]]
                
        clusters.append(sum(current_cluster) / len(current_cluster))
        return clusters

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    analyzer = PremiumAnalyzer()
    
    spot_levels = analyzer.get_structural_spot_levels()
    print("Structural Supports:", spot_levels["support"][-3:])
    print("Structural Resistances:", spot_levels["resistance"][-3:])
    
    prem_levels = analyzer.get_premium_historical_levels(24100, "PE")
    print(f"24100 PE Historical Support Floor: {prem_levels['support']}")
    
    corr_prem = analyzer.get_premium_at_spot_level(24100, "PE", 23960.0)
    print(f"When Nifty hits 23960, 24100 PE premium is roughly: {corr_prem}")
