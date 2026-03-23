"""
Roostoo API client.
Handles authentication (HMAC-SHA256) and all REST endpoints.
"""

import hashlib
import hmac
import logging
import time

import requests

logger = logging.getLogger(__name__)


class RoostooClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.base_url   = base_url.rstrip("/")

    # ── Auth helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _sign(self, params: dict) -> str:
        """Sort params alphabetically, build query string, return HMAC-SHA256 hex."""
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, params: dict) -> dict:
        return {
            "RST-API-KEY":    self.api_key,
            "MSG-SIGNATURE":  self._sign(params),
        }

    # ── Public endpoints (no auth) ────────────────────────────────────────────

    def get_server_time(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/v3/serverTime", timeout=10)
            return r.json()
        except Exception as e:
            logger.error("get_server_time: %s", e)
            return {}

    def get_exchange_info(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/v3/exchangeInfo", timeout=10)
            return r.json()
        except Exception as e:
            logger.error("get_exchange_info: %s", e)
            return {}

    # ── Timestamp-authenticated endpoint ─────────────────────────────────────

    def get_ticker(self, pair: str = None) -> dict:
        params = {"timestamp": self._now_ms()}
        if pair:
            params["pair"] = pair
        try:
            r = requests.get(f"{self.base_url}/v3/ticker", params=params, timeout=10)
            return r.json()
        except Exception as e:
            logger.error("get_ticker: %s", e)
            return {}

    # ── Signed GET endpoints ──────────────────────────────────────────────────

    def get_balance(self) -> dict:
        params = {"timestamp": self._now_ms()}
        try:
            r = requests.get(
                f"{self.base_url}/v3/balance",
                params=params,
                headers=self._auth_headers(params),
                timeout=10,
            )
            return r.json()
        except Exception as e:
            logger.error("get_balance: %s", e)
            return {}

    def get_pending_count(self) -> dict:
        params = {"timestamp": self._now_ms()}
        try:
            r = requests.get(
                f"{self.base_url}/v3/pending_count",
                params=params,
                headers=self._auth_headers(params),
                timeout=10,
            )
            return r.json()
        except Exception as e:
            logger.error("get_pending_count: %s", e)
            return {}

    # ── Signed POST endpoints ─────────────────────────────────────────────────

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: float = None,
    ) -> dict:
        params = {
            "timestamp": self._now_ms(),
            "pair":      pair,
            "side":      side,
            "quantity":  quantity,
            "type":      order_type,
        }
        if order_type == "LIMIT" and price is not None:
            params["price"] = price

        headers = self._auth_headers(params)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            r = requests.post(
                f"{self.base_url}/v3/place_order",
                data=params,
                headers=headers,
                timeout=10,
            )
            resp = r.json()
            logger.info("place_order %s %s %s %s → success=%s",
                        order_type, side, quantity, pair, resp.get("Success"))
            return resp
        except Exception as e:
            logger.error("place_order: %s", e)
            return {}

    def cancel_order(self, order_id: int = None, pair: str = None) -> dict:
        params = {"timestamp": self._now_ms()}
        if order_id:
            params["order_id"] = order_id
        if pair:
            params["pair"] = pair

        headers = self._auth_headers(params)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            r = requests.post(
                f"{self.base_url}/v3/cancel_order",
                data=params,
                headers=headers,
                timeout=10,
            )
            return r.json()
        except Exception as e:
            logger.error("cancel_order: %s", e)
            return {}

    def query_order(
        self,
        order_id: int = None,
        pair: str = None,
        pending_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        params: dict = {"timestamp": self._now_ms()}
        if order_id:
            params["order_id"] = order_id
        else:
            if pair:
                params["pair"] = pair
            if pending_only:
                params["pending_only"] = "true"
            params["offset"] = offset
            params["limit"]  = limit

        headers = self._auth_headers(params)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            r = requests.post(
                f"{self.base_url}/v3/query_order",
                data=params,
                headers=headers,
                timeout=10,
            )
            return r.json()
        except Exception as e:
            logger.error("query_order: %s", e)
            return {}
