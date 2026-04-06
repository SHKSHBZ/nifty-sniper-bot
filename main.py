import multiprocessing
import yaml
import time
import logging
import sys
from upstox_auth import UpstoxAuth
from feed_handler import FeedHandler
from trading_engine import TradingEngine

# Setup main orchestrator logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Orchestrator")

def run_feed(config):
    """Target function for FeedHandler process."""
    # Process-specific logging configuration
    logging.basicConfig(level=logging.INFO)
    try:
        handler = FeedHandler(config)
        handler.run()
    except Exception as e:
        logger.error(f"FeedHandler Process Error: {e}")

def run_engine(config):
    """Target function for TradingEngine process."""
    # Process-specific logging configuration
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("data/trading_log.log"),
            logging.StreamHandler()
        ]
    )
    try:
        engine = TradingEngine(config)
        engine.run()
    except Exception as e:
        logger.error(f"TradingEngine Process Error: {e}")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 SWARM-HFT UNIFIED ORCHESTRATOR")
    print("="*60)

    # 1. Load Configuration
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
        logger.info("[OK] Configuration loaded.")
    except Exception as e:
        logger.error(f"Failed to load config.yaml: {e}")
        sys.exit(1)

    # 2. Unified Authentication
    # We do this in the main process before spawning sub-processes.
    # This ensures that both sub-processes have access to a valid session.
    logger.info("Checking Upstox session...")
    auth = UpstoxAuth()
    if not auth.is_session_valid():
        logger.warning("Session invalid or expired. Starting authentication...")
        if not auth.authenticate():
            logger.error("Authentication failed. Cannot start services.")
            sys.exit(1)
    else:
        logger.info("[OK] Session is valid.")

    # 3. Start Processes
    logger.info("Spawning sub-services...")
    
    # Use 'spawn' to ensure clean memory space for each service
    # (Important for ZeroMQ and file handlers)
    multiprocessing.set_start_method('spawn', force=True)

    feed_proc = multiprocessing.Process(target=run_feed, args=(config,), name="FeedService")
    engine_proc = multiprocessing.Process(target=run_engine, args=(config,), name="EngineService")

    # Start Feed first
    feed_proc.start()
    logger.info(f"[OK] Feed Service started (PID: {feed_proc.pid})")
    
    # Short delay to allow Feed to bind ZMQ port
    time.sleep(3)
    
    # Start Engine
    engine_proc.start()
    logger.info(f"[OK] Engine Service started (PID: {engine_proc.pid})")

    print("="*60)
    print("🔥 BOT IS LIVE. Press Ctrl+C to stop all services.")
    print("="*60 + "\n")

    try:
        while True:
            time.sleep(5)
            # Health checks
            if not feed_proc.is_alive():
                logger.error("Feed Service died unexpectedly!")
                break
            if not engine_proc.is_alive():
                logger.error("Engine Service died unexpectedly!")
                break
    except KeyboardInterrupt:
        print("\n" + "-"*60)
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.error(f"Main loop error: {e}")
    finally:
        logger.info("Stopping services...")
        feed_proc.terminate()
        engine_proc.terminate()
        
        feed_proc.join(timeout=5)
        engine_proc.join(timeout=5)
        
        if feed_proc.is_alive(): feed_proc.kill()
        if engine_proc.is_alive(): engine_proc.kill()
        
        logger.info("[DONE] All services stopped.")
        print("-"*60 + "\n")
