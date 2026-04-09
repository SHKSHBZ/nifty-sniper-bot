from telegram_notifier import TelegramNotifier

print("Testing Telegram Webhooks...")
bot = TelegramNotifier()

if bot.enabled:
    print("✅ Keys found! Sending test message to your phone...")
    bot.send_message("✅ Nifty Sniper Bot Telegram Connection Successful! The bot is ready to send live trade alerts.")
    print("Message sent! Check your Telegram app.")
else:
    print("❌ TELEGRAM DISABLED: You need to add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to your .env file!")
