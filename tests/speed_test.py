import time
import sys
import logging

# Ensure Windows prints emojis and logs nicely
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(message)s')

from macro_intelligence import MacroIntelligence

def run_speed_test():
    print(f"\n{'='*50}")
    print("⚡ COMMENCING LATENCY SPEED TEST ⚡")
    print(f"{'='*50}")
    
    macro = MacroIntelligence()
    
    # Start the absolute stopwatch
    start_time = time.time()
    
    print("\n[Timing Started] ➝ Searching Google News RSS & Ping Groq Llama 3.1...")
    
    # 1. Scraping and 2. AI Inference
    score = macro.fetch_ai_news_sentiment()
    
    # Stop the stopwatch
    end_time = time.time()
    total_time = end_time - start_time
    
    print(f"\n{'-'*50}")
    print(f"✅ AI Sentiment Generated: {score:.2f}")
    if total_time < 3.0:
        print(f"🚀 TOTAL EXECUTION TIME: {total_time:.3f} SECONDS (Blazing Fast)")
    else:
        print(f"🐢 TOTAL EXECUTION TIME: {total_time:.3f} SECONDS (Network Lag)")
    print(f"{'-'*50}\n")

if __name__ == "__main__":
    run_speed_test()
