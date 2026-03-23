"""
APEX Bot — Main Orchestrator

Full trading cycle:
  1. Fetch real-time ticker from Roostoo.
  2. Update per-pair price history.
  3. Record portfolio value; update performance metrics.
  4. Circuit-breaker check — halt new entries if drawdown is too large.
  5. Exit logic — evaluate stop-loss / take-profit / signal reversal
     for every open position.
  6. Cross-sectional momentum filter — rank all pairs by 20-period
     return; only allow new entries for top performers (positive momentum).
  7. Ensemble signal evaluation — 5-factor voting system per pair.
  8. Entry logic — volatility-targeted, Kelly-calibrated position sizing.
  9. Audit logging — every trade appended to CSV for competition review.
"""

import csv
import logging
import os
import time
from collections import defaultdict, deque

import requests

import config
from client    import RoostooClient
from indicators import momentum_return
from portfolio  import Portfolio, Position, TradeRecord
from risk       import RiskManager
from signals    import SignalEngine

logger = logging.getLogger(__name__)


# ── Audit CSV logger ───────────────────────────────────────────────────────────

class AuditLogger:
    """Append-only CSV writers for trades and portfolio snapshots."""

    TRADE_HEADERS = [
        "timestamp_utc", "pair", "side", "quantity",
        "entry_price", "exit_price", "pnl_pct", "reason", "api_success",
    ]
    PERF_HEADERS = [
        "timestamp_utc", "portfolio_value", "total_return_pct",
        "sortino", "sharpe", "calmar", "max_drawdown_pct", "open_positions",
    ]

    def __init__(self):
        self._init_csv(config.TRADE_LOG_FILE, self.TRADE_HEADERS)
        self._init_csv(config.PERF_LOG_FILE,  self.PERF_HEADERS)

    @staticmethod
    def _init_csv(path: str, headers: list):
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(headers)

    @staticmethod
    def _ts() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def log_trade(
        self,
        pair: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        reason: str,
        api_success: bool,
    ):
        with open(config.TRADE_LOG_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                self._ts(), pair, side, quantity,
                round(entry_price, 6), round(exit_price, 6),
                round(pnl_pct, 4), reason, api_success,
            ])

    def log_performance(self, portfolio_value: float, metrics: dict):
        with open(config.PERF_LOG_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                self._ts(),
                round(portfolio_value, 2),
                metrics.get("total_return_pct", 0),
                metrics.get("sortino",          0),
                metrics.get("sharpe",           0),
                metrics.get("calmar",           0),
                metrics.get("max_drawdown_pct", 0),
                metrics.get("open_positions",   0),
            ])


# ── Main Bot ───────────────────────────────────────────────────────────────────

class TradingBot:

    def __init__(self):
        self.client        = RoostooClient(config.API_KEY, config.API_SECRET, config.BASE_URL)
        self.portfolio     = Portfolio()
        self.risk          = RiskManager()
        self.signal_engine = SignalEngine()
        self.audit         = AuditLogger()

        # Rolling price history per pair (close prices, oldest → newest)
        self.price_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=config.MAX_PRICE_HISTORY)
        )

        self.active_pairs:  list[str] = []
        self.exchange_info: dict      = {}

    # ── Initialisation ─────────────────────────────────────────────────────────

    def initialise(self):
        logger.info("=" * 60)
        logger.info("APEX Bot starting up")
        logger.info("=" * 60)
        self._load_exchange_info()
        self._warmup_from_binance()
        self._reconcile_positions()
        logger.info("Active pairs  : %s", self.active_pairs)
        logger.info("Open positions: %s", list(self.portfolio.positions.keys()))

    def _load_exchange_info(self):
        info = self.client.get_exchange_info()
        # Exchange info uses TradePairs key (not Data)
        self.exchange_info = info.get("TradePairs", {}) if isinstance(info, dict) else {}

        available: set[str] = set(self.exchange_info.keys())

        # Supplement with ticker if exchange_info is empty
        if not available:
            ticker = self.client.get_ticker()
            available = set((ticker or {}).get("Data", {}).keys())

        self.active_pairs = [p for p in config.PREFERRED_PAIRS if p in available]
        if not self.active_pairs:
            self.active_pairs = config.PREFERRED_PAIRS
            logger.warning("Exchange info empty — using full preferred pair list.")

        logger.info("Discovered pairs: %s", sorted(available))

    def _warmup_from_binance(self):
        """
        Seed price history from Binance public kline data so that all
        indicators are fully warmed up before the first live cycle.
        """
        for pair in self.active_pairs:
            symbol = config.BINANCE_SYMBOL_MAP.get(pair)
            if not symbol:
                logger.warning("No Binance symbol mapped for %s — skipping warmup.", pair)
                continue
            try:
                resp = requests.get(
                    "https://api.binance.com/api/v3/klines",
                    params={
                        "symbol":   symbol,
                        "interval": config.BINANCE_INTERVAL,
                        "limit":    config.WARMUP_CANDLES,
                    },
                    timeout=15,
                )
                candles = resp.json()
                if isinstance(candles, list) and candles:
                    for c in candles:
                        self.price_history[pair].append(float(c[4]))  # close price
                    logger.info("Warmed up %-10s with %d candles.", pair, len(candles))
                else:
                    logger.warning("Unexpected Binance response for %s.", pair)
            except Exception as exc:
                logger.warning("Binance warmup failed for %s: %s", pair, exc)

    def _reconcile_positions(self):
        """
        On (re)start, detect any crypto already held in the account and
        register it as an open position at the current market price.
        This ensures the bot manages pre-existing balances correctly.
        """
        balance = self.client.get_balance()
        if not balance or not balance.get("Success"):
            return
        ticker = self.client.get_ticker()
        ticker_data = (ticker or {}).get("Data", {})

        for coin, amounts in balance.get("SpotWallet", {}).items():
            if coin == "USD":
                continue
            qty = float(amounts.get("Free", 0)) + float(amounts.get("Lock", 0))
            if qty <= 0:
                continue
            pair = f"{coin}/USD"
            if pair not in self.active_pairs or pair in self.portfolio.positions:
                continue
            price = float(ticker_data.get(pair, {}).get("LastPrice", 0))
            if price <= 0:
                continue
            prices = list(self.price_history[pair])
            sl, tp = self.risk.compute_stops(price, prices)
            self.portfolio.positions[pair] = Position(
                pair=pair, quantity=qty, entry_price=price,
                stop_loss=sl, take_profit=tp,
            )
            logger.info("Reconciled: %-10s qty=%.6f @ %.4f", pair, qty, price)

    # ── Exchange helpers ───────────────────────────────────────────────────────

    def _qty_precision(self, pair: str) -> int:
        # exchange_info is already the TradePairs dict after _load_exchange_info
        return int(self.exchange_info.get(pair, {}).get("AmountPrecision", 2))

    def _min_order_usd(self, pair: str) -> float:
        return float(self.exchange_info.get(pair, {}).get("MiniOrder", 1.0))

    # ── Account helpers ────────────────────────────────────────────────────────

    def _portfolio_value(self, ticker_data: dict) -> float:
        balance = self.client.get_balance()
        if not balance or not balance.get("Success"):
            return self.portfolio.current_value or 0.0
        total = 0.0
        for coin, amounts in balance.get("SpotWallet", {}).items():
            qty = float(amounts.get("Free", 0)) + float(amounts.get("Lock", 0))
            if qty <= 0:
                continue
            if coin == "USD":
                total += qty
            else:
                pair  = f"{coin}/USD"
                price = float(ticker_data.get(pair, {}).get("LastPrice", 0))
                if price > 0:
                    total += qty * price
        return total

    def _free_usd(self) -> float:
        balance = self.client.get_balance()
        if not balance or not balance.get("Success"):
            return 0.0
        return float(balance.get("SpotWallet", {}).get("USD", {}).get("Free", 0))

    # ── Cross-sectional momentum ranking ──────────────────────────────────────

    def _rank_by_momentum(self) -> list[str]:
        """
        Rank active pairs by their CS_MOMENTUM_PERIOD return (descending).
        Returns only pairs with positive momentum — these are the only candidates
        for new long entries. This is the cross-sectional momentum filter.
        """
        scored: list[tuple[float, str]] = []
        for pair in self.active_pairs:
            prices = list(self.price_history[pair])
            mom    = momentum_return(prices, config.CS_MOMENTUM_PERIOD)
            scored.append((mom, pair))
        scored.sort(reverse=True)
        return [pair for mom, pair in scored if mom > config.CS_MIN_MOMENTUM]

    # ── Trade execution ────────────────────────────────────────────────────────

    def _open_position(self, pair: str, price: float, prices: list,
                       portfolio_value: float) -> bool:
        """Place a market BUY order and register the new position."""
        if pair in self.portfolio.positions:
            return False

        kelly   = self.portfolio.kelly_fraction()
        free    = self._free_usd()
        pos_usd = self.risk.position_size_usd(price, prices, portfolio_value, free, kelly)

        if pos_usd < self._min_order_usd(pair):
            logger.info("Skipping BUY %-10s — size %.0f < min order.", pair, pos_usd)
            return False

        prec = self._qty_precision(pair)
        qty  = round(pos_usd / price, prec)
        if qty <= 0:
            return False

        resp    = self.client.place_order(pair, "BUY", qty, order_type="MARKET")
        success = bool(resp and resp.get("Success"))
        detail  = (resp or {}).get("OrderDetail", {})
        filled  = float(detail.get("FilledAverPrice", 0)) or price

        self.audit.log_trade(pair, "BUY", qty, filled, 0.0, 0.0, "SIGNAL", success)
        self.portfolio.trade_log.append(
            TradeRecord(pair=pair, side="BUY", quantity=qty, price=filled)
        )

        if not success:
            logger.warning("BUY failed %-10s: %s", pair, resp)
            return False

        filled_qty = float(detail.get("FilledQuantity", qty)) or qty
        sl, tp     = self.risk.compute_stops(filled, prices)

        self.portfolio.positions[pair] = Position(
            pair=pair, quantity=filled_qty, entry_price=filled,
            stop_loss=sl, take_profit=tp,
        )
        logger.info(
            "OPENED  %-10s | qty=%.6f | entry=%.4f | SL=%.4f | TP=%.4f",
            pair, filled_qty, filled, sl, tp,
        )
        return True

    def _close_position(self, pair: str, current_price: float, reason: str) -> bool:
        """Place a market SELL order and remove the position."""
        pos = self.portfolio.positions.get(pair)
        if not pos:
            return False

        prec = self._qty_precision(pair)
        qty  = round(pos.quantity, prec)

        resp    = self.client.place_order(pair, "SELL", qty, order_type="MARKET")
        success = bool(resp and resp.get("Success"))
        detail  = (resp or {}).get("OrderDetail", {})
        exit_p  = float(detail.get("FilledAverPrice", 0)) or current_price
        pnl_pct = pos.pnl_pct(exit_p)

        self.audit.log_trade(pair, "SELL", qty, pos.entry_price, exit_p, pnl_pct, reason, success)
        self.portfolio.trade_log.append(
            TradeRecord(pair=pair, side="SELL", quantity=qty, price=exit_p,
                        pnl_pct=pnl_pct, reason=reason)
        )

        if not success:
            logger.warning("SELL failed %-10s: %s", pair, resp)
            return False

        logger.info(
            "CLOSED  %-10s | reason=%-12s | entry=%.4f | exit=%.4f | PnL=%+.2f%%",
            pair, reason, pos.entry_price, exit_p, pnl_pct,
        )
        del self.portfolio.positions[pair]
        return True

    # ── Main cycle ─────────────────────────────────────────────────────────────

    def run_cycle(self):
        # 1. Fetch all tickers ─────────────────────────────────────────────────
        ticker_resp = self.client.get_ticker()
        if not ticker_resp or not ticker_resp.get("Success"):
            logger.warning("Ticker fetch failed — skipping cycle.")
            return
        ticker_data = ticker_resp.get("Data", {})

        # 2. Portfolio value & performance ─────────────────────────────────────
        port_value = self._portfolio_value(ticker_data)
        self.portfolio.record_value(port_value)
        metrics    = self.portfolio.summary()
        self.audit.log_performance(port_value, metrics)

        logger.info(
            "── Cycle ─ Value=$%,.0f ─ Ret=%+.2f%% ─ "
            "Sortino=%.3f ─ Sharpe=%.3f ─ Calmar=%.3f ─ DD=%.2f%%",
            port_value,
            metrics["total_return_pct"],
            metrics["sortino"],
            metrics["sharpe"],
            metrics["calmar"],
            metrics["current_dd_pct"],
        )

        # 3. Update price history ───────────────────────────────────────────────
        for pair in self.active_pairs:
            price = float(ticker_data.get(pair, {}).get("LastPrice", 0))
            if price > 0:
                self.price_history[pair].append(price)

        # 4. Circuit breaker ────────────────────────────────────────────────────
        if self.risk.update_circuit_breaker(self.portfolio.current_drawdown):
            logger.warning("Trading halted by circuit breaker — exits still active.")

        # 5. Exit logic (runs even when circuit breaker is active) ─────────────
        for pair in list(self.portfolio.positions.keys()):
            price = float(ticker_data.get(pair, {}).get("LastPrice", 0))
            if price <= 0:
                continue
            pos = self.portfolio.positions[pair]

            if price <= pos.stop_loss:
                self._close_position(pair, price, "STOP_LOSS")
                continue
            if price >= pos.take_profit:
                self._close_position(pair, price, "TAKE_PROFIT")
                continue

            # Signal-based exit: close if ensemble votes to sell
            sig = self.signal_engine.evaluate(pair, list(self.price_history[pair]))
            if sig.action == "SELL":
                self._close_position(pair, price, "SIGNAL_SELL")

        # 6. Skip new entries if circuit breaker is active ─────────────────────
        if self.risk.is_halted:
            return

        # 7. Cross-sectional momentum filter ────────────────────────────────────
        momentum_ranked = self._rank_by_momentum()
        if not momentum_ranked:
            logger.info("No pairs pass cross-sectional momentum filter — no entries.")
            return

        # 8. Entry logic ────────────────────────────────────────────────────────
        exposure_pct = (self.portfolio.total_exposure_usd() / port_value
                        if port_value > 0 else 1.0)

        for pair in momentum_ranked:
            if pair in self.portfolio.positions:
                continue   # already holding
            if exposure_pct >= config.MAX_TOTAL_EXPOSURE_PCT:
                logger.info("Max exposure %.0f%% reached — no more entries.",
                            config.MAX_TOTAL_EXPOSURE_PCT * 100)
                break

            prices = list(self.price_history[pair])
            sig    = self.signal_engine.evaluate(pair, prices)

            if sig.action != "BUY":
                continue

            price = float(ticker_data.get(pair, {}).get("LastPrice", 0))
            if price <= 0:
                continue

            opened = self._open_position(pair, price, prices, port_value)
            if opened and pair in self.portfolio.positions:
                new_pos_usd   = self.portfolio.positions[pair].quantity * price
                exposure_pct += new_pos_usd / port_value

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self):
        self.initialise()
        logger.info("Starting live trading loop (interval=%ds).", config.LOOP_INTERVAL_SECONDS)
        while True:
            try:
                self.run_cycle()
            except Exception as exc:
                logger.error("Unhandled error in run_cycle: %s", exc, exc_info=True)
            time.sleep(config.LOOP_INTERVAL_SECONDS)
