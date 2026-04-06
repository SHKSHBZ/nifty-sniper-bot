import requests
import json
import time

def analyze_headline_with_groq(headline, api_key):
    """
    Sends a news headline to the Free Groq API (Llama 3.1)
    and asks it to return ONLY a numerical sentiment score.
    """
    print(f"📡 Sending Headline to Groq Supercomputers: '{headline}'")
    
    # We use the standard OpenAI-compatible API format that Groq supports
    url = "https://api.groq.com/openai/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # The exact instruction given to the AI
    system_prompt = (
        "You are a strictly quantitative financial analyst for the Indian Stock Market. "
        "Analyze the following news headline and its impact on the Nifty 50 Index. "
        "You must return a JSON object with exactly two keys:\n"
        "1. 'score': A float between -1.0 (Extreme Crash Expected) and +1.0 (Massive Rally Expected). 0.0 is Neutral.\n"
        "2. 'rationale': A strict, 1-sentence professional explanation of why you gave this score.\n"
        "Do not include any markdown formatting, just the raw JSON."
    )
    
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": headline}
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}  # Force Groq to return standard JSON
    }
    
    start_time = time.time()
    
    # Make the connection over the internet (This is where the magic happens)
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status() # Check for errors
        
        data = response.json()
        
        # Parse the JSON reply
        ai_reply_json = json.loads(data['choices'][0]['message']['content'])
        end_time = time.time()
        
        score = float(ai_reply_json.get('score', 0.0))
        rationale = ai_reply_json.get('rationale', 'No rationale provided.')
        
        print(f"✅ Fast API Response received in {end_time - start_time:.3f} seconds!")
        print(f"🔢 Quantized Score: {score:.2f}")
        print(f"🧠 AI Rationale: {rationale}\n")
        
        return score
        
    except Exception as e:
        print(f"❌ API Request Failed: {e}")
        if 'response' in locals() and response.status_code == 401:
            print("Hint: Your API key is likely missing or invalid.")
        return 0.0

if __name__ == "__main__":
    print("="*60)
    print("🚀 FREE CLOUD LLM DEMONSTRATION 🚀")
    print("="*60)
    
    # --- GETTING YOUR FREE KEY ---
    # 1. Go to https://console.groq.com/keys
    # 2. Sign in with Google or GitHub
    # 3. Click "Create API Key"
    # 4. Paste it below!
    
    YOUR_FREE_API_KEY = "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" # REPLACE THIS with your real key!
    
    if YOUR_FREE_API_KEY == "gsk_...":
        print("⚠️ ACTION REQUIRED: You need to paste your free Groq API key into the script on line 51!")
        print("Go to https://console.groq.com/keys to get one instantly for free.")
        exit()

    # Let's test 3 different types of news headlines to see how the mathematical AI scores it
    headlines = [
        "RBI completely unexpectedly slashes interest rates by 100 basis points to spur aggressive growth.",
        "Nifty Bank sector sees routine monthly volume changes, trading remains flat.",
        "Major Indian banks report massive hidden losses. Hindenburg drops new short-seller report on Adani."
    ]
    
    for news in headlines:
        score = analyze_headline_with_groq(news, YOUR_FREE_API_KEY)
        time.sleep(1) # Small pause so we can read the terminal output
