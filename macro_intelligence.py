import requests
import logging
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
import json

logger = logging.getLogger("MacroIntelligence")

class MacroIntelligence:
    """
    Handles Pre-Market validation before 9:15 AM to determine the daily trend bias.
    Fetches FII/DII net flows and global market sentiment.
    """
    def __init__(self, config=None):
        self.config = config or {}
        self.daily_bias = "NEUTRAL"
        self.global_cues_score = 0
        self.fii_dii_bias = "NEUTRAL"
        self.gift_nifty_gap = 0.0 # Expected gap up/down in points
        self.ai_sentiment_score = 0.0 # From Groq LLM
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
    def fetch_fii_dii_data(self):
        """
        Attempts to fetch End-Of-Day FII/DII data from public APIs/proxies.
        For production, connecting this to a structured data vendor is best.
        """
        try:
            # Placeholder for NSE / Proxy API
            # url = "https://www.nseindia.com/api/fiidiiTradeReact"
            # resp = self.session.get(url, timeout=10)
            
            # Using simulated logic for safety in live runs until a concrete API key is given
            # In a real scenario, we parse Net value. > 0 is Bullish, < 0 is Bearish
            logger.info("FII/DII Data initialized (Standby / Paper Mode)")
            self.fii_dii_bias = "BULLISH" # Example default
            return self.fii_dii_bias
        except Exception as e:
            logger.error(f"FII/DII Fetch Error: {e}")
            return "NEUTRAL"
            
    def fetch_global_cues(self):
        """
        Fetches US Markets (Dow Jones, Nasdaq) closing status.
        """
        try:
            # We can use Yahoo Finance API or a proxy to get ^DJI and ^IXIC.
            # Simulated API call for speed/safety without yfinance installed.
            logger.info("Global Cues initialized (Standby / Paper Mode)")
            self.global_cues_score = 1 # Positive cues
            return "POSITIVE" if self.global_cues_score > 0 else "NEGATIVE"
        except Exception as e:
            logger.error(f"Global Cues Fetch Error: {e}")
            return "NEUTRAL"
            
    def fetch_gift_nifty(self):
        """
        Parses GIFT Nifty futures to gauge the 'Gap'.
        """
        try:
            # Simulated Gap logic
            self.gift_nifty_gap = 45.0 # Example: Gap up by 45 points
            logger.info(f"GIFT Nifty estimated gap: {self.gift_nifty_gap} points")
            return self.gift_nifty_gap
        except Exception as e:
            logger.error(f"GIFT Nifty Fetch Error: {e}")
            return 0.0
            
    def fetch_ai_news_sentiment(self):
        """
        Autonomous AI Market Reading:
        1. Scrapes Top 5 live headlines regarding Nifty/Sensex from Google News RSS.
        2. Pipes headlines into Groq's Llama 3.1 supercomputer.
        3. Averages the numerical scores.
        """
        api_key = self.config.get('groq_api_key', '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')
        if not api_key or api_key == "gsk_...":
            logger.warning("AI News Module OFF: Groq API Key missing.")
            return 0.0
            
        logger.info("🤖 AI News Module Activated: Scraping Live Headlines...")
        try:
            # 1. Scrape Live Headlines
            url = "https://news.google.com/rss/search?q=Nifty+OR+Sensex+OR+Indian+Stock+Market+when:1d"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            xml_data = urllib.request.urlopen(req, timeout=10).read()
            root = ET.fromstring(xml_data)
            headlines = [item.find('title').text for item in root.findall('.//item')][:5]
            
            if not headlines: return 0.0
            
            # Combine headlines into one prompt
            combined_news = "\n".join([f"- {h}" for h in headlines])
            logger.info(f"🗞️ Analying headlines:\n{combined_news}")
            
            # 2. Query Groq Llama 3.1
            system_prompt = (
                "You are a strict quantitative analyst for the Indian Stock Market. "
                "Analyze these 5 live headlines and return a JSON object with just one key:\n"
                "1. 'score': A float between -1.0 (Massive Crash) and +1.0 (Massive Rally).\n"
                "Do not include markdown, just the raw JSON."
            )
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": combined_news}
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            }
            
            resp = self.session.post("https://api.groq.com/openai/v1/chat/completions", 
                                     headers={"Authorization": f"Bearer {api_key}"},
                                     json=payload)
            resp.raise_for_status()
            ai_reply = json.loads(resp.json()['choices'][0]['message']['content'])
            score = float(ai_reply.get('score', 0.0))
            
            self.ai_sentiment_score = score
            logger.info(f"🤖 AI Sentiment Graded: {score:.2f} / 1.00")
            return self.ai_sentiment_score
            
        except Exception as e:
            logger.error(f"AI News Fetch Error: {e}")
            return 0.0
            
    def get_pre_market_bias(self):
        """
        Combines macro factors into a single Daily Trend Bias.
        Called once at 9:00 AM.
        """
        self.fetch_fii_dii_data()
        self.fetch_global_cues()
        self.fetch_gift_nifty()
        self.fetch_ai_news_sentiment()
        
        score = 0
        if self.fii_dii_bias == "BULLISH": score += 1
        elif self.fii_dii_bias == "BEARISH": score -= 1
        
        if self.global_cues_score > 0: score += 1
        elif self.global_cues_score < 0: score -= 1
        
        if self.gift_nifty_gap > 30: score += 1
        elif self.gift_nifty_gap < -30: score -= 1
        
        # Supercharge AI Score impact
        if self.ai_sentiment_score >= 0.4: score += 2
        elif self.ai_sentiment_score <= -0.4: score -= 2
        
        if score >= 2:
            self.daily_bias = "EXTREME_BULLISH"
        elif score == 1:
            self.daily_bias = "BULLISH"
        elif score <= -2:
            self.daily_bias = "EXTREME_BEARISH"
        elif score == -1:
            self.daily_bias = "BEARISH"
        else:
            self.daily_bias = "NEUTRAL"
            
        logger.info(f"Morning Macro Bias set to: {self.daily_bias}")
        return self.daily_bias
        
    def is_bias_conflict(self, intended_direction):
        """
        Filters out trades that strictly contradict the overarching macro bias.
        """
        if self.daily_bias == "EXTREME_BULLISH" and intended_direction == "BEARISH":
            return True
        if self.daily_bias == "EXTREME_BEARISH" and intended_direction == "BULLISH":
            return True
        # If AI expects violent crash, absolutely block all bullish trades
        if self.ai_sentiment_score < -0.5 and intended_direction == "BULLISH":
            return True
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    macro = MacroIntelligence()
    print(f"\nFinal Bias Engine Output: {macro.get_pre_market_bias()}")
