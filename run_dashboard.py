import subprocess
import time
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()

def cleanup():
    """Kill any processes running on ports 8000 and 3000."""
    if os.name == 'nt':
        print("[*] Cleaning up existing dashboard processes...")
        # Kill processes on port 8000 (FastAPI) and 3000 (Next.js)
        for port in [8000, 3000]:
            try:
                # Find PID on port
                output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode()
                for line in output.splitlines():
                    if 'LISTENING' in line:
                        pid = line.strip().split()[-1]
                        subprocess.run(f'taskkill /F /PID {pid} /T', shell=True, capture_output=True)
            except Exception:
                pass
    time.sleep(1)

def start_backend():
    print("[*] Starting FastAPI Backend...")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=BASE_DIR
    )

def start_frontend():
    print("[*] Starting Next.js Dashboard...")
    # Bind to 0.0.0.0 to allow access via Tailscale/Local IP
    return subprocess.Popen(
        "npm run dev -- -H 0.0.0.0",
        shell=True,
        cwd=BASE_DIR / "dashboard"
    )

if __name__ == "__main__":
    backend_proc = None
    frontend_proc = None
    cleanup()
    try:
        backend_proc = start_backend()
        time.sleep(2) # Give backend a moment
        frontend_proc = start_frontend()
        
        print("\n" + "="*50)
        print("[+] DASHBOARD IS RUNNING")
        print("[>] Local Access: http://localhost:3000")
        print("[>] API Access:   http://localhost:8000")
        print("="*50 + "\n")
        
        # Keep the script running
        while True:
            if backend_proc.poll() is not None:
                print("[!] Backend stopped unexpectedly. Restarting...")
                backend_proc = start_backend()
            if frontend_proc.poll() is not None:
                print("[!] Frontend stopped unexpectedly. Restarting...")
                frontend_proc = start_frontend()
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n[!] Shutting down dashboard...")
        if backend_proc: backend_proc.terminate()
        if frontend_proc: 
            # On Windows, npm run dev starts child processes, we might need taskkill
            if os.name == 'nt':
                subprocess.run(f"taskkill /F /T /PID {frontend_proc.pid}", shell=True, capture_output=True)
            else:
                frontend_proc.terminate()
        print("[*] Goodbye!")
