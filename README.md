# 5-Minute Volume Profile WEEX Trading Bot & Alert Daemon

A production-grade algorithmic trading bot engineered for the **5-minute timeframe**, utilizing **Volume Profile (POC, VAH, VAL)** mean-reversion with confirmed trap re-entry, dynamic ATR stops, native WEEX Take-Profit / Stop-Loss order execution, and instant Telegram alerts.

---

## Performance Highlights (20-Day Multi-Regime Backtest)

* **Timeframe**: 5m
* **Profit Factor**: **1.50**
* **Average Trade**: **+0.32%** (more than 3x the exchange taker fee)
* **Net Return**: **+10.56%** (with conservative 10% portfolio sizing per position)
* **Max Drawdown**: **-4.89%**
* **Calmar Ratio**: **2.16**

---

## Key Features

1. **Trap Re-Entry Confirmation**: Waits for price to dip below Value Area Low (VAL) and close back *inside* the Value Area to confirm a bear trap (preventing falling-knife entries).
2. **1.00% Minimum Target Hurdle**: Filters out micro-scalps under 1.00% move to POC to eliminate fee drag.
3. **2-Strike Circuit Breaker**: Automatically freezes any symbol for 2 hours if it takes 2 consecutive stop-outs, protecting against prolonged one-way trend collapses.
4. **Native WEEX TP/SL**: Leverages WEEX V3 Contract API (`tpTriggerPrice` / `slTriggerPrice` directly in the entry payload) with mandatory `b-` prefixed client order IDs. Positions are guaranteed protected even if the server restarts.
5. **Multi-Asset Position Sizing**: Allocates a disciplined 10% of portfolio balance per trade.
6. **Dual Mode**: Runs in `DRY_RUN=True` (simulated trading + live Telegram alerts) or `DRY_RUN=False` (live capital execution on WEEX).

---

## Project Structure

```
vp-weex-bot/
├── .env.example              # Environment variables template
├── .gitignore                # Git ignore rules for keys and cache
├── config.py                 # Central strategy, risk, and exchange parameters
├── weex_client.py            # WEEX V3 Contract REST API client with HMAC-SHA256 signing
├── telegram_notifier.py      # Rich HTML Telegram notification dispatcher
├── vp_engine.py              # Volume Profile (POC/VAH/VAL), ATR, and RSI calculators
├── state_manager.py          # Persistent position tracking and circuit-breaker state
├── scanner.py                # Universe screener and 5m candle evaluation engine
├── executor.py               # Risk sizing, precision rounding, and order execution
├── main.py                   # Main daemon loop (synchronized to 5m candle closes)
├── test_connection.py        # Pre-flight diagnostic tool
└── requirements.txt          # Python dependencies
```

---

## Quick Start (Local Setup)

### 1. Clone & Install Dependencies
```bash
git clone <YOUR_GITHUB_REPO_URL>
cd vp-weex-bot
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your credentials:
```ini
# WEEX API Credentials
WEEX_API_KEY=your_weex_api_key
WEEX_API_SECRET=your_weex_api_secret
WEEX_PASSPHRASE=your_weex_passphrase
WEEX_BASE_URL=https://api.weex.com

# Telegram Bot
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz
TELEGRAM_CHAT_ID=987654321

# Trading Mode
# Keep DRY_RUN=True initially to verify live alerts
DRY_RUN=True

# Position Sizing & Leverage
POSITION_SIZE_PCT=0.10
DEFAULT_LEVERAGE=3
```

> **How to get Telegram Bot Token & Chat ID**:
> 1. Message `@BotFather` on Telegram and send `/newbot` to get your `TELEGRAM_BOT_TOKEN`.
> 2. Message `@userinfobot` on Telegram to get your numeric `TELEGRAM_CHAT_ID`.

### 3. Run Pre-Flight Diagnostic
```bash
python test_connection.py
```
This verifies your WEEX API authentication, dispatches a test message to your Telegram app, and checks the 5m scanner engine against live market candles.

### 4. Start the Daemon
```bash
python main.py
```

---

## Pushing to a New GitHub Repository

Run these commands inside the `vp-weex-bot` directory:

```bash
git init
git add .
git commit -m "feat: initial release of 5m Volume Profile WEEX bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo-name>.git
git push -u origin main
```
*(Make sure your repository is **Private** so your `.env` template remains confidential).*

---

## DigitalOcean Droplet Deployment (24/7 Cloud Hosting)

### 1. Create a Droplet on DigitalOcean
* **OS**: Ubuntu 24.04 (LTS) x64
* **Plan**: Basic Droplet -> Regular ($4 or $6 / month)
* **Datacenter**: Singapore or New York (close to exchange servers)
* **Authentication**: SSH Key (Recommended) or Root Password

### 2. Connect via SSH
```bash
ssh root@<YOUR_DROPLET_IP>
```

### 3. Install System Packages & Clone Repo
```bash
apt update && apt upgrade -y
apt install python3-pip python3-venv git -y

git clone https://github.com/<your-username>/<your-repo-name>.git /root/vp-weex-bot
cd /root/vp-weex-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Setup `.env` on the Server
```bash
nano .env
```
Paste your actual WEEX and Telegram keys, then press `Ctrl+O`, `Enter`, and `Ctrl+X` to save.

Test connection on the droplet:
```bash
python test_connection.py
```

### 5. Setup Systemd Background Service (Auto-Start on Reboot)
Create the service configuration:
```bash
nano /etc/systemd/system/vp-bot.service
```
Paste the following:
```ini
[Unit]
Description=5m Volume Profile WEEX Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/vp-weex-bot
ExecStart=/root/vp-weex-bot/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
systemctl daemon-reload
systemctl enable vp-bot
systemctl start vp-bot
```

### 6. Useful Maintenance Commands
* **Check live logs**:
  ```bash
  journalctl -u vp-bot -f
  ```
* **Check status**:
  ```bash
  systemctl status vp-bot
  ```
* **Restart the bot** (e.g. after editing `.env`):
  ```bash
  systemctl restart vp-bot
  ```
* **Stop the bot**:
  ```bash
  systemctl stop vp-bot
  ```
