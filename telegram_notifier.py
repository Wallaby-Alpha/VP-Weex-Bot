import logging
import requests
import config

logger = logging.getLogger("TELEGRAM_NOTIFIER")


class TelegramNotifier:
    """
    Dispatches rich HTML trade alerts, circuit breaker warnings, and system status to Telegram.
    """

    def __init__(self, bot_token: str = config.TELEGRAM_BOT_TOKEN, chat_id: str = config.TELEGRAM_CHAT_ID):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_message(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.info(f"[TELEGRAM DISABLED] {text}")
            return False

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            resp = requests.post(self.base_url, json=payload, timeout=8)
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"Telegram error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to dispatch Telegram message: {e}")
            return False

    def notify_trade_signal(self, trade: dict):
        """
        Formats and dispatches trade signal details.
        """
        side = trade["side"].upper()
        icon = "🟢" if side == "LONG" else "🔴"
        action = "LONG ENTRY" if side == "LONG" else "SHORT ENTRY"
        dry_str = " <b>[DRY RUN - SIMULATED]</b>" if trade.get("dry_run", False) else " <b>[LIVE WEEX ORDER]</b>"

        msg = (
            f"{icon} <b>{action} TRIGGERED</b>{dry_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Pair:</b> <code>#{trade['symbol']}</code>\n"
            f"<b>Timeframe:</b> <code>{trade.get('timeframe', '5m')}</code>\n"
            f"<b>Entry Price:</b> <code>${trade['entry_price']:.4f}</code>\n"
            f"<b>Take Profit (POC):</b> <code>${trade['take_profit']:.4f}</code> (+{trade['reward_pct']:.2f}%)\n"
            f"<b>Stop Loss (1.6x ATR):</b> <code>${trade['stop_loss']:.4f}</code> (-{trade['risk_pct']:.2f}%)\n"
            f"<b>Risk / Reward:</b> <code>{trade['rr']:.2f}</code>\n"
            f"<b>Order Size:</b> <code>{trade['quantity']} contracts</code> (~10% Portfolio)\n"
            f"<b>RSI(14):</b> <code>{trade.get('rsi', 0):.1f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Protection: Native WEEX Stop-Loss & Take-Profit Attached</i>"
        )
        self.send_message(msg)

    def notify_circuit_breaker(self, symbol: str, freeze_hours: int = 2):
        msg = (
            f"⚠️ <b>CIRCUIT BREAKER ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pair: <code>#{symbol}</code>\n"
            f"Reason: 2 consecutive stop-outs detected (trend protection).\n"
            f"Status: Frozen for <b>{freeze_hours} hours</b> to prevent bleed."
        )
        self.send_message(msg)

    def notify_startup(self, active_pairs: list, dry_run: bool, balance: float):
        mode = "🟡 DRY-RUN (Alerts Only)" if dry_run else "🟢 LIVE TRADING (WEEX Contract Active)"
        msg = (
            f"🤖 <b>VP SCANNER DAEMON ONLINE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Available USDT:</b> <code>${balance:,.2f}</code>\n"
            f"<b>Active Universe:</b> <code>{len(active_pairs)} Pairs</code>\n"
            f"<b>Timeframe:</b> <code>{config.TIMEFRAME}</code>\n"
            f"<b>Target Hurdle:</b> <code>>={config.MIN_TARGET_PCT * 100:.2f}% move to POC</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Scanning live bars every 5 minutes...</i>"
        )
        self.send_message(msg)

    def notify_error(self, err_text: str):
        msg = f"🚨 <b>BOT ERROR ALERT</b>\n<code>{err_text}</code>"
        self.send_message(msg)
