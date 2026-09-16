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
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.10"))  # Max 10% balance allocation collar
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "3"))        # 3x isolated leverage
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.01"))  # 1.0% equity risk per trade (risk parity)
MIN_DOLLAR_RISK = float(os.getenv("MIN_DOLLAR_RISK", "0.50"))        # $0.50 risk floor for small accounts
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
MIN_TARGET_PCT = 0.007      # Require at least 0.70% gross move to target
MIN_STOP_PCT = float(os.getenv("MIN_STOP_PCT", "0.0075")) # Require at least 0.75% minimum SL distance from entry
MAX_STOP_PCT = float(os.getenv("MAX_STOP_PCT", "0.0250")) # 2.50% maximum SL distance ceiling to avoid bloated stops
MAX_HOLDING_BARS = 48       # 4 hours max hold time
COOLDOWN_BARS = 8           # 40 mins cooldown
RSI_PERIOD = 14
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE", "3"))  # Min score out of 7
MAX_CONFLUENCE_SCORE = int(os.getenv("MAX_CONFLUENCE_SCORE", "4"))  # Cap at 4 (scores >= 5 are runaway breakouts with 0% mean-reversion win rate)
REQUIRE_BTC_REL_STRENGTH = os.getenv("REQUIRE_BTC_REL_STRENGTH", "True").lower() in ("true", "1", "yes")

# --- Profit Protection & Trailing Stop Settings ---
BREAKEVEN_TRIGGER_PCT = float(os.getenv("BREAKEVEN_TRIGGER_PCT", "0.009"))       # +0.90% unrealized gain moves SL to entry
BREAKEVEN_BUFFER_PCT = float(os.getenv("BREAKEVEN_BUFFER_PCT", "0.001"))        # +0.10% buffer to cover trading fees
TRAILING_PROFIT_TRIGGER_PCT = float(os.getenv("TRAILING_PROFIT_TRIGGER_PCT", "0.025")) # +2.5% unrealized gain starts trailing
TRAILING_PROFIT_RETENTION = float(os.getenv("TRAILING_PROFIT_RETENTION", "0.50"))    # Retains 50% of peak unrealized profit

# --- Session Confluence Strategy Settings ---
REQUIRE_SESSION_CONFLUENCE = os.getenv("REQUIRE_SESSION_CONFLUENCE", "True").lower() in ("true", "1", "yes")
NY_SESSION_OPEN = "09:30:00"
NY_SESSION_CLOSE = "16:00:00"
NY_TIMEZONE = "America/New_York"
ASIA_SESSION_OPEN = "00:00:00"
ASIA_SESSION_CLOSE = "08:00:00"
ASIA_TIMEZONE = "UTC"
KLINE_FETCH_LIMIT = 1000     # 1000 bars (~83 hours) guarantees reaching Friday's completed NY session on weekends

# --- Risk Circuit Breaker & Post-Loss Cooldown ---
POST_LOSS_COOLDOWN_HOURS = float(os.getenv("POST_LOSS_COOLDOWN_HOURS", "3.0"))  # 3-hour freeze on any symbol hitting SL
MAX_CONSECUTIVE_LOSSES = 2
CIRCUIT_BREAKER_FREEZE_BARS = 24  # Freeze coin for 2 hours on 2 losses

# --- Universe Screener (Top 200 by Volume) ---
MIN_24H_VOLUME_USD = float(os.getenv("MIN_24H_VOLUME_USD", "100000"))  # Lowered to $100k volume floor
MAX_24H_VOLUME_USD = float(os.getenv("MAX_24H_VOLUME_USD", "inf"))     # No upper cap: captures top high-volume movers
MAX_PAIRS = int(os.getenv("MAX_PAIRS", "200"))                         # Scan top 200 coins

# Blacklist: Leveraged tokens and BTC-slaved dinosaur coins (meme coins kept — they produce the widest VAs)
EXCLUDED_SUBSTRINGS = [
    "UP", "DOWN", "3L", "3S", "4L", "4S", "5L", "5S", "BULL", "BEAR", "ONUSDT",
    "XMR", "DASH", "LTC", "BCH"
]
FIAT_BASES = ["EUR", "BRL", "TRY", "GBP", "AUD", "RUB", "UAH"]
STABLECOIN_BASES = ["USD1", "USDC", "TUSD", "FDUSD", "USDP", "BUSD", "DAI", "USDE", "PYUSD", "RLUSD"]
