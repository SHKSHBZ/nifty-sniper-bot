#!/usr/bin/env python3
"""
Upstox Authentication Module
Standalone authentication system for Upstox API
Handles login, session management, and token persistence
"""

import json
import os
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import pytz
import requests
from dotenv import load_dotenv

load_dotenv()

IST = pytz.timezone('Asia/Kolkata')

class UpstoxAuth:
    """
    Handles all Upstox authentication and session management
    """
    
    def __init__(self):
        self.client_id = None
        self.client_secret = None
        self.redirect_uri = None
        self.access_token = None
        
        # File paths
        self.config_file = Path('config/upstox_config.json')
        self.session_file = Path('state/upstox_session.json')
        
        print("[AUTH] Upstox Authentication System")
        print("=" * 40)
        
    def setup_credentials(self, client_id: str = None, client_secret: str = None, redirect_uri: str = None):
        """
        Setup and save API credentials
        """
        if not client_id:
            print("\n[INFO] Get your credentials from: https://pro.upstox.com/")
            print("   1. Login to Upstox Pro")
            print("   2. Go to 'Apps' -> 'My Apps'") 
            print("   3. Create new app or use existing")
            print("   4. Copy API Key (Client ID), Secret, and Redirect URI")
            print()
            
            client_id = input("[KEY] Enter your Client ID (API Key): ").strip()
            client_secret = input("[SECRET] Enter your Client Secret: ").strip()
            redirect_uri = input("[URI] Enter your Redirect URI: ").strip()
        
        if not client_id or not client_secret or not redirect_uri:
            print("[ERROR] Client ID, Secret, and Redirect URI are required!")
            return False
        
        # Save credentials
        config_data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'created_at': datetime.now(IST).isoformat(),
            'setup_version': '1.0'
        }
        
        try:
            # Ensure config directory exists
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
            
            print(f"\n[OK] Credentials saved to {self.config_file}")
            print(f"Client ID: {client_id}")
            
            self.client_id = client_id
            self.client_secret = client_secret
            self.redirect_uri = redirect_uri
            return True
            
        except Exception as e:
            print(f"[FAIL] Failed to save credentials: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def load_credentials(self):
        """
        Load saved API credentials
        """
        try:
            # 1. Try loading from .env file first
            self.client_id = os.getenv('UPSTOX_API_KEY')
            self.client_secret = os.getenv('UPSTOX_API_SECRET')
            self.redirect_uri = os.getenv('UPSTOX_REDIRECT_URI')
            
            if self.client_id and self.client_secret and self.redirect_uri:
                print(f"[OK] Credentials loaded directly from .env file")
                print(f"Client ID: {self.client_id}")
                return True
            
            # 2. Fallback to old config file
            if not self.config_file.exists():
                print("[FAIL] No credentials found in .env or config file. Run setup first.")
                return False
            
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            
            self.client_id = config.get('client_id')
            self.client_secret = config.get('client_secret')
            self.redirect_uri = config.get('redirect_uri')
            
            if self.client_id and self.client_secret and self.redirect_uri:
                print(f"[OK] Credentials loaded from config file")
                print(f"Client ID: {self.client_id}")
                return True
            else:
                print("[FAIL] Invalid credentials in config file")
                return False
                
        except Exception as e:
            print(f"[FAIL] Error loading credentials: {e}")
            return False
    
    def auto_authenticate(self):
        """
        Automatically authenticate: use valid session if available, else prompt for login.
        """
        if not self.load_credentials():
            print("No credentials found. Run setup first.")
            if not self.setup_credentials():
                return False
        
        if self.load_session():
            print("[OK] Using existing session (auto)")
            return True
        
        print("Session expired or missing. Starting login...")
        return self.fresh_login()
    
    def authenticate(self):
        return self.auto_authenticate()
    
    def fresh_login(self):
        """
        Perform fresh Upstox login with browser
        """
        try:
            # Generate login URL
            login_url = (
                f"https://api.upstox.com/v2/login/authorization/dialog"
                f"?response_type=code"
                f"&client_id={self.client_id}"
                f"&redirect_uri={self.redirect_uri}"
            )
            
            print(f"\n[UPSTOX LOGIN PROCESS]")
            print(f"=" * 30)
            print(f"1. Browser will open automatically")
            print(f"2. Login with your Upstox credentials")
            print(f"3. Authorize the application")
            print(f"4. Copy the complete redirect URL")
            print(f"=" * 30)
            
            # Open browser
            print(f"\n[Opening browser...]")
            webbrowser.open(login_url)
            
            # Get redirect URL from user
            print(f"\n[After successful login and authorization:]")
            redirect_url = input("[URL] Paste the complete redirect URL here: ").strip()
            
            if not redirect_url:
                print("[FAIL] No URL provided")
                return False
            
            # Extract code
            parsed_url = urlparse(redirect_url)
            query_params = parse_qs(parsed_url.query)
            
            code = query_params.get('code', [None])[0]
            
            if not code:
                print("[FAIL] No authorization code found in URL")
                return False
            
            print(f"[AUTH] Auth Code extracted: {code[:10]}...")
            
            # Exchange code for access token
            url = 'https://api.upstox.com/v2/login/authorization/token'
            headers = {
                'accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            }
            data = {
                'code': code,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'redirect_uri': self.redirect_uri,
                'grant_type': 'authorization_code',
            }
            
            response = requests.post(url, headers=headers, data=data)
            
            if response.status_code == 200:
                json_response = response.json()
                self.access_token = json_response['access_token']
                
                print(f"\n[OK] AUTHENTICATION SUCCESS!")
                
                # Verify and save session
                # We can call a profile API here if needed, or just save
                self.save_session(json_response)
                return True
            else:
                print(f"[FAIL] Token exchange failed: {response.text}")
                return False
            
        except Exception as e:
            print(f"\n[FAIL] Authentication failed: {e}")
            return False
    
    def save_session(self, token_data):
        """
        Save session data for reuse
        """
        try:
            now = datetime.now(IST)
            # Upstox tokens usually valid for a day, but check documentation if 'expires_in' is provided
            # Typically valid until 6:00 AM next day or explicit expiry?
            # Using safe default of 1 day or next 6 AM
            
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now.hour >= 6:
                next_6am += timedelta(days=1)
            
            session_data = {
                'access_token': self.access_token,
                'client_id': self.client_id,
                'created_at': now.isoformat(),
                'expires_at': next_6am.isoformat(),
                'token_data': token_data, # Store full response just in case
                'broker': 'UPSTOX',
                'session_version': '1.0'
            }
            
            # Ensure state directory exists
            os.makedirs(os.path.dirname(self.session_file), exist_ok=True)
            
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
            
            print(f"[SAVE] Session saved until: {next_6am.strftime('%Y-%m-%d 06:00:00')}")
            
        except Exception as e:
            print(f"[FAIL] Failed to save session: {e}")
    
    def load_session(self):
        """
        Load and validate existing session
        """
        try:
            if not self.session_file.exists():
                return False
            
            with open(self.session_file, 'r') as f:
                session_data = json.load(f)
            
            if session_data.get('client_id') != self.client_id:
                print("[WARNING] Session Client ID mismatch")
                return False
            
            expires_at = datetime.fromisoformat(session_data['expires_at'])
            now = datetime.now(IST)
            
            if now >= expires_at:
                print(f"[EXPIRED] Session expired at {expires_at}")
                return False
            
            self.access_token = session_data['access_token']
            
            print(f"[OK] Session restored")
            return True
            
        except Exception as e:
            print(f"[WARNING] Session validation failed: {e}")
            return False

    def is_session_valid(self) -> bool:
        try:
            if not self.load_credentials():
                return False
            return self.load_session()
        except Exception:
            return False
            
    def get_access_token(self):
        if not self.client_id:
            self.load_credentials()
            
        if self.access_token:
            return self.access_token
        if self.load_session():
            return self.access_token
        return None

    def get_client_id(self):
        if self.client_id:
            return self.client_id
        if self.load_credentials():
            return self.client_id
        return None

if __name__ == "__main__":
    auth = UpstoxAuth()
    auth.authenticate()
