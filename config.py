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

# --- 5-Minute Volume Profile Strategy Parameters ---
TIMEFRAME = "5m"
LOOKBACK_BARS = 72          # 6 hours lookback (72 * 5m)
NUM_BINS = 30
VAL_PCT = 0.70
ATR_PERIOD = 14
ATR_MULT_STOP = 1.6
MIN_RR = 1.4
MIN_TARGET_PCT = 0.010      # Require at least 1.00% gross move to POC
MAX_HOLDING_BARS = 48       # 4 hours max hold time
COOLDOWN_BARS = 8           # 40 mins cooldown
RSI_PERIOD = 14
RSI_LONG_MAX = 46.0         # Must be recovering from oversold
RSI_SHORT_MIN = 54.0        # Must be exhausting from overbought

# --- Risk Circuit Breaker ---
MAX_CONSECUTIVE_LOSSES = 2
CIRCUIT_BREAKER_FREEZE_BARS = 24  # Freeze coin for 2 hours on 2 losses

# --- Universe Screener ---
MIN_24H_VOLUME_USD = 400_000
MAX_24H_VOLUME_USD = 6_000_000
MAX_PAIRS = 20

# Blacklist: Non-viable meme tokens and BTC-slaved dinosaur coins
EXCLUDED_SUBSTRINGS = [
    "UP", "DOWN", "3L", "3S", "4L", "4S", "5L", "5S", "BULL", "BEAR", "ONUSDT",
    "BASECAT", "INU", "PEPE", "SHIB", "MEME", "DOGE", "CAT",
    "XMR", "DASH", "UNI", "LTC", "BCH"
]
FIAT_BASES = ["EUR", "BRL", "TRY", "GBP", "AUD", "RUB", "UAH"]
STABLECOIN_BASES = ["USD1", "USDC", "TUSD", "FDUSD", "USDP", "BUSD", "DAI", "USDE", "PYUSD", "RLUSD"]
