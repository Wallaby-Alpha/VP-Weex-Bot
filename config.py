import os
from dotenv import load_dotenv

load_dotenv()

# --- WEEX API Credentials ---
WEEX_API_KEY = os.getenv("WEEX_API_KEY", "")
WEEX_API_SECRET = os.getenv("WEEX_API_SECRET", "")
WEEX_PASSPHRASE = os.getenv("WEEX_PASSPHRASE", "")
WEEX_BASE_URL = os.getenv("WEEX_BASE_URL", "https://api-contract.weex.com")

# --- Telegram Alert Settings ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Trading Mode & Sizing ---
DRY_RUN = os.getenv("DRY_RUN", "True").lower() in ("true", "1", "yes")
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.10"))  # 10% of balance per trade
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "3"))        # 3x isolated leverage
MAX_CONCURRENT_TRADES = int(os.getenv("MAX_CONCURRENT_TRADES", "4"))  # Max simultaneous active positions (v2)
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03"))   # Account daily 3% loss limit guard
MAX_OPEN_CORR = float(os.getenv("MAX_OPEN_CORR", "0.85"))             # Max 30-bar return correlation cap

# --- 5-Minute Volume Profile Strategy Parameters ---
TIMEFRAME = "5m"
LOOKBACK_BARS = 72          # 6 hours lookback (72 * 5m)
NUM_BINS = 30
VAL_PCT = 0.70
ATR_PERIOD = 14
ATR_MULT_STOP = 1.6
MIN_RR = 1.80               # Raised to 1.80 R:R for opposite VA target
MIN_TARGET_PCT = 0.010      # Require at least 1.00% gross move to target
MAX_HOLDING_BARS = 48       # 4 hours max hold time
COOLDOWN_BARS = 8           # 40 mins cooldown
RSI_PERIOD = 14
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE", "3"))  # Min score out of 7

# --- Session Confluence Strategy Settings ---
REQUIRE_SESSION_CONFLUENCE = os.getenv("REQUIRE_SESSION_CONFLUENCE", "True").lower() in ("true", "1", "yes")
NY_SESSION_OPEN = "09:30:00"
NY_SESSION_CLOSE = "16:00:00"
NY_TIMEZONE = "America/New_York"
ASIA_SESSION_OPEN = "00:00:00"
ASIA_SESSION_CLOSE = "08:00:00"
ASIA_TIMEZONE = "UTC"
KLINE_FETCH_LIMIT = 500     # Sufficient bars to encompass completed NY and Asia sessions

# --- Risk Circuit Breaker ---
MAX_CONSECUTIVE_LOSSES = 2
CIRCUIT_BREAKER_FREEZE_BARS = 24  # Freeze coin for 2 hours on 2 losses

# --- Universe Screener (Top 200 by Volume) ---
MIN_24H_VOLUME_USD = float(os.getenv("MIN_24H_VOLUME_USD", "250000"))  # Raised to $250k volume floor
MAX_24H_VOLUME_USD = float(os.getenv("MAX_24H_VOLUME_USD", "inf"))     # No upper cap: captures top high-volume movers
MAX_PAIRS = int(os.getenv("MAX_PAIRS", "200"))                         # Scan top 200 coins

# Blacklist: Non-viable meme tokens and BTC-slaved dinosaur coins
EXCLUDED_SUBSTRINGS = [
    "UP", "DOWN", "3L", "3S", "4L", "4S", "5L", "5S", "BULL", "BEAR", "ONUSDT",
    "BASECAT", "INU", "PEPE", "SHIB", "MEME", "DOGE", "CAT",
    "XMR", "DASH", "UNI", "LTC", "BCH"
]
FIAT_BASES = ["EUR", "BRL", "TRY", "GBP", "AUD", "RUB", "UAH"]
STABLECOIN_BASES = ["USD1", "USDC", "TUSD", "FDUSD", "USDP", "BUSD", "DAI", "USDE", "PYUSD", "RLUSD"]
