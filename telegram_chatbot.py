import time
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger("TelegramChatbot")
logging.basicConfig(level=logging.INFO, format='%(message)s')

class TradingChatbot:
    """
    Advanced Conversational UI (The 'OpenClaw' Experience)
    Uses Llama 3.1 via Groq to translate mechanical trading data into 
    human-like text messages over Telegram.
    """
    def __init__(self, config, engine=None):
        self.telegram_token = config.get('telegram_token')
        self.groq_api_key = config.get('groq_api_key', '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')
        self.engine = engine
        self.allowed_chat_id = str(config.get('telegram_chat_id'))
        
        self.offset = 0 # Tracks the last read message from Telegram
        self.session = requests.Session()

    def get_trading_stats_string(self):
        """Grabs the raw numerical data from the Python Engine."""
        if not self.engine:
            return "Bot is OFFLINE. No active PnL."
            
        try:
            executor = getattr(self.engine, 'paper_executor', None)
            capital = executor.get_current_capital() if executor else 500000
            pnl = executor.get_total_pnl() if executor else 0
            open_pos = list(self.engine.active_naked_positions.keys())
            
            return f"[SYSTEM DATA] Capital: {capital}, Daily PnL: {pnl}, Active Trades: {open_pos}"
        except Exception as e:
            return f"[SYSTEM DATA ERROR] {e}"

    def ask_llama(self, user_message, system_data):
        """Sends the user's text and the bot's hidden data to Llama 3.1 to generate a human response."""
        system_prompt = (
            "You are an elite, highly professional personal trading assistant. "
            "You run a high-frequency Nifty Options Sniper algorithmic bot on the user's home PC. "
            "The user is at the office and texting you to check on their money. "
            f"Here is your strict current raw backend data: {system_data}. "
            "Translate this data into a warm, confident, and incredibly concise text message reply. "
            "Do NOT hallucinate trades that aren't in the system data. "
            "If the user asks you to 'shut down' or 'stop', confirm you will protect their capital."
        )
        
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.4
        }
        
        try:
            resp = self.session.post("https://api.groq.com/openai/v1/chat/completions", 
                                     headers={"Authorization": f"Bearer {self.groq_api_key}"},
                                     json=payload)
            resp.raise_for_status()
            ai_reply = resp.json()['choices'][0]['message']['content']
            return ai_reply
        except Exception as e:
            logger.error(f"Groq API Error: {e}")
            return "Sorry boss, my AI uplink is currently disconnected. Basic PnL is active."

    def reply_to_telegram(self, chat_id, text):
        """Sends the Llama 3.1 generated text message back to the user's phone."""
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Telegram reply: {e}")

    def listen_loop(self):
        """Infinite loop that checks Telegram for new text messages from you."""
        if not self.telegram_token or self.telegram_token == "YOUR_BOT_TOKEN":
            logger.error("🛑 Telegram Token missing! Please get one from @BotFather and add to config.yaml.")
            return

        logger.info("🎧 Nifty Sniper Conversational AI is now LISTENING on Telegram...")
        
        while True:
            try:
                url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates?offset={self.offset}&timeout=10"
                resp = requests.get(url, timeout=15)
                updates = resp.json().get("result", [])
                
                for update in updates:
                    self.offset = update["update_id"] + 1
                    
                    if "message" in update and "text" in update["message"]:
                        chat_id = str(update["message"]["chat"]["id"])
                        user_text = update["message"]["text"]
                        
                        logger.info(f"📱 Received Text: '{user_text}'")
                        
                        # Only reply to YOU (security)
                        if self.allowed_chat_id != "YOUR_CHAT_ID" and chat_id != self.allowed_chat_id:
                            logger.warning(f"Unauthorized chat attempt from CID: {chat_id}")
                            continue
                            
                        # Handle Command: Kill Switch
                        if "stop" in user_text.lower() or "shut down" in user_text.lower():
                            if self.engine: self.engine.is_market_safe = lambda: (False, "USER OVERRIDE: Shutdown Requested")
                        
                        # 1. Grab mechanical backend data
                        sys_data = self.get_trading_stats_string()
                        
                        # 2. Ask Groq Llama 3.1 to converse
                        logger.info("🧠 Asking Llama 3.1 to generate response...")
                        ai_reply = self.ask_llama(user_text, sys_data)
                        
                        # 3. Fire the text message
                        self.reply_to_telegram(chat_id, ai_reply)
                        logger.info(f"🚀 Sent Reply: {ai_reply}\n")
                        
            except Exception as e:
                pass
            time.sleep(1) # Prevent CPU burn

if __name__ == "__main__":
    import yaml
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        
    chatbot = TradingChatbot(config, engine=None)
    chatbot.listen_loop()
