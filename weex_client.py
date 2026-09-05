import time
import json
import hmac
import hashlib
import base64
import logging
from typing import Optional, Dict, Any, List
import requests

import config

logger = logging.getLogger("WEEX_CLIENT")


class WeexClient:
    """
    Python client for WEEX V3 Contract API.
    Ported and refined from Wallaby-Alpha/Dillinger-WEEX-BOT.
    Handles HMAC-SHA256 request signing, market data, and native TP/SL order execution.
    """

    def __init__(
        self,
        api_key: str = config.WEEX_API_KEY,
        api_secret: str = config.WEEX_API_SECRET,
        passphrase: str = config.WEEX_PASSPHRASE,
        base_url: str = config.WEEX_BASE_URL,
    ):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.passphrase = passphrase.strip()
        
        # Defensive cleanup: strip accidental prefixes or quotes
        clean_url = base_url.strip().strip("'\"")
        if clean_url.startswith("WEEX_BASE_URL="):
            clean_url = clean_url.replace("WEEX_BASE_URL=", "").strip().strip("'\"")
        if not clean_url.startswith("http"):
            clean_url = f"https://{clean_url}"
        self.base_url = clean_url.rstrip("/")
        self.session = requests.Session()

    def generate_signature(self, timestamp: str, method: str, path: str, body_str: str) -> str:
        """
        Generates HMAC-SHA256 signature encoded in Base64:
        message = timestamp + method + path + bodyStr
        """
        message = timestamp + method.upper() + path + (body_str or "")
        mac = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        is_public: bool = False,
        timeout: int = 12
    ) -> Dict[str, Any]:
        """
        Executes an HTTP request against WEEX V3 Contract endpoints with auth headers.
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "locale": "en-US"
        }

        body_str = json.dumps(body) if (method.upper() not in ("GET", "DELETE") and body) else ""

        if not is_public:
            timestamp = str(int(time.time() * 1000))
            signature = self.generate_signature(timestamp, method, path, body_str)
            headers.update({
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.passphrase
            })

        for attempt in range(3):
            try:
                if method.upper() == "GET":
                    resp = self.session.get(url, headers=headers, timeout=timeout)
                elif method.upper() == "POST":
                    resp = self.session.post(url, headers=headers, data=body_str, timeout=timeout)
                elif method.upper() == "DELETE":
                    resp = self.session.delete(url, headers=headers, timeout=timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue

                return resp.json()
            except Exception as e:
                logger.warning(f"WEEX request attempt {attempt + 1} failed for {method} {path}: {e}")
                time.sleep(1.0)

        return {"code": -1, "msg": "Request failed after 3 attempts"}

    def get_exchange_info(self) -> Dict[str, Dict[str, Any]]:
        """
        Fetches all trading symbol metadata including price/quantity precision and limits.
        """
        res = self.request("GET", "/capi/v3/market/exchangeInfo", is_public=True)
        raw_symbols = res.get("symbols") or res.get("data", {}).get("symbols") or []
        metadata = {}

        for s in raw_symbols:
            sym = s.get("symbol") or s.get("displaySymbol")
            if sym:
                metadata[sym] = {
                    "symbol": sym,
                    "pricePrecision": int(s.get("pricePrecision", 4)),
                    "quantityPrecision": int(s.get("quantityPrecision", 2)),
                    "contractVal": float(s.get("contractVal", 1.0)),
                    "minOrderSize": float(s.get("minOrderSize", 0.001)),
                    "maxOrderSize": float(s.get("maxOrderSize", 1000000)),
                    "minLeverage": int(s.get("minLeverage", 1)),
                    "maxLeverage": int(s.get("maxLeverage", 100))
                }
        return metadata

    def get_mark_price(self, symbol: str) -> float:
        """
        Fetches current mark price for a symbol.
        """
        res = self.request("GET", f"/capi/v3/market/premiumIndex?symbol={symbol}", is_public=True)
        item = res if not isinstance(res, list) else (res[0] if len(res) > 0 else {})
        if "data" in res:
            item = res["data"]
            if isinstance(item, list) and len(item) > 0:
                item = item[0]

        mark_price = item.get("markPrice") or item.get("indexPrice")
        if mark_price:
            return float(mark_price)
        raise ValueError(f"Could not retrieve mark price for {symbol}: {res}")

    def get_available_margin(self) -> float:
        """
        Fetches available USDT balance in the futures account.
        """
        res = self.request("GET", "/capi/v3/account/balance")
        balances = res if isinstance(res, list) else res.get("data", [])
        if isinstance(balances, dict):
            balances = balances.get("list", [])

        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance") or b.get("balance") or 0.0)
        return 0.0

    def set_leverage(self, symbol: str, leverage: int = config.DEFAULT_LEVERAGE) -> bool:
        """
        Sets isolated leverage for both long and short positions on the symbol.
        """
        payload = {
            "symbol": symbol,
            "isolatedLongLeverage": str(leverage),
            "isolatedShortLeverage": str(leverage)
        }
        res = self.request("POST", "/capi/v3/account/leverage", payload)
        code = res.get("code")
        return code in (0, "0", 200, "200") or res.get("success", False)

    def place_order_with_tpsl(
        self,
        symbol: str,
        side: str,              # "BUY" or "SELL"
        position_side: str,     # "LONG" or "SHORT"
        quantity: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Submits market entry order to WEEX with attached native Take Profit and Stop Loss triggers.
        WEEX V3 requires client order IDs to have the 'b-' prefix.
        """
        cid = client_order_id or f"b-vp-{int(time.time() * 1000)}"
        if not cid.startswith("b-"):
            cid = f"b-{cid}"

        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "positionSide": position_side.upper(),
            "quantity": str(quantity),
            "newClientOrderId": cid
        }

        if tp_price and tp_price > 0:
            payload["tpTriggerPrice"] = str(tp_price)
            payload["tpWorkingType"] = "MARK_PRICE"

        if sl_price and sl_price > 0:
            payload["slTriggerPrice"] = str(sl_price)
            payload["slWorkingType"] = "MARK_PRICE"

        res = self.request("POST", "/capi/v3/order", payload)
        data = res.get("data", res)

        is_success = res.get("code") in (0, "0", 200, "200") or data.get("success", False) or bool(data.get("orderId"))
        return {
            "success": is_success,
            "orderId": str(data.get("orderId", "")),
            "clientOrderId": cid,
            "raw": res
        }

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """
        Returns all open positions with positive size.
        """
        res = self.request("GET", "/capi/v3/account/position/allPosition")
        raw_list = res if isinstance(res, list) else res.get("data", [])
        if isinstance(raw_list, dict):
            raw_list = raw_list.get("list", [])

        active = []
        for p in raw_list:
            size = float(p.get("size") or p.get("total") or 0.0)
            if size > 0:
                active.append({
                    "symbol": p.get("symbol"),
                    "side": "SHORT" if p.get("side") == "SHORT" else "LONG",
                    "size": size,
                    "openValue": float(p.get("openValue", 0.0)),
                    "entryPrice": float(p.get("openPrice") or p.get("entryPrice") or 0.0),
                    "unrealizePnl": float(p.get("unrealizePnl", 0.0)),
                    "tpOrderId": p.get("stopProfitId") or p.get("tpOrderId"),
                    "slOrderId": p.get("stopLossId") or p.get("slOrderId")
                })
        return active
