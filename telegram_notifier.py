import os
import requests
import threading
import time
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("TelegramNotifier")

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.enabled = bool(self.token and self.chat_id)
        if self.enabled:
            logger.info("Telegram alerts ENABLED")
        else:
            logger.warning("Telegram alerts DISABLED — add token & chat_id to config.yaml")

    def send_message(self, message):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id, 
                "text": f"🎯 NIFTY SNIPER BOT\n{message}\nTime: {datetime.now().strftime('%H:%M:%S')}",
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Telegram failed: {e}")

    def start_heartbeat(self, engine):
        """Pings your phone every 60 seconds with account status."""
        def heartbeat_loop():
            while True:
                try:
                    # Professional Check: Only send heartbeat during market hours
                    now = datetime.now().time()
                    if datetime.strptime("09:00:00", "%H:%M:%S").time() <= now <= datetime.strptime("15:30:00", "%H:%M:%S").time():
                        executor = getattr(engine, 'paper_executor', None)
                        capital = executor.get_current_capital() if executor else 0
                        trade_targets = getattr(engine, 'trade_targets', {})
                        open_pos = len(trade_targets)
                        pnl = executor.get_total_pnl() if executor else 0
                        
                        msg = (f"*❤️ HEARTBEAT*\n"
                               f"Capital: ₹{capital:,.0f}\n"
                               f"Open Positions: {open_pos}\n"
                               f"PnL Today: ₹{pnl:,.0f}\n"
                               f"Status: `RUNNING`")
                        self.send(msg)
                except Exception as e:
                    logger.error(f"Heartbeat error: {e}")
                time.sleep(60)

        thread = threading.Thread(target=heartbeat_loop, daemon=True)
        thread.start()
