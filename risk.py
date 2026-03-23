"""
APEX Bot — Risk Management

Responsibilities:
  • Compute ATR-based, volatility-adaptive stop-loss and take-profit levels.
  • Size positions using Kelly criterion + ATR volatility targeting
    (larger positions when volatility is low, smaller when it is high).
  • Enforce a portfolio-level circuit breaker that halts new entries
    when drawdown exceeds a configurable threshold.
"""

import logging

import config
import indicators as ind

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Stateful risk controller.
    Holds the circuit-breaker flag and applies consistent sizing rules.
    """

    def __init__(self):
        self._halted      = False
        self._halt_reason = ""

    # ── Circuit breaker ────────────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        return self._halted

    def update_circuit_breaker(self, current_drawdown: float) -> bool:
        """
        Evaluate drawdown and toggle the trading halt flag.
        Returns True if trading is currently halted (no new entries allowed).
        """
        if not self._halted and current_drawdown >= config.MAX_DRAWDOWN_HALT:
            self._halted      = True
            self._halt_reason = (
                f"Drawdown {current_drawdown:.2%} reached halt threshold "
                f"{config.MAX_DRAWDOWN_HALT:.2%}"
            )
            logger.warning("🚨 CIRCUIT BREAKER TRIGGERED — %s", self._halt_reason)

        elif self._halted and current_drawdown < config.MAX_DRAWDOWN_RESUME:
            self._halted = False
            logger.info(
                "Circuit breaker lifted — drawdown recovered to %.2f%%",
                current_drawdown * 100,
            )

        return self._halted

    # ── Stop / Take-profit ─────────────────────────────────────────────────────

    def compute_stops(
        self,
        entry_price: float,
        prices: list,
    ) -> tuple:
        """
        Compute ATR-based stop-loss and take-profit.

        Stop  = entry − ATR_STOP_MULTIPLIER  × ATR
        Target = entry + ATR_TARGET_MULTIPLIER × ATR

        Both are clamped to percentage floors/caps to avoid excessively
        wide or impossibly tight levels on any individual asset.

        Returns:
            (stop_loss, take_profit) as absolute prices.
        """
        atr = ind.atr_proxy(prices, config.ATR_PERIOD)

        # Fallback: use a fixed percentage of price if ATR cannot be computed
        if atr == 0.0 or entry_price == 0.0:
            atr = entry_price * 0.02

        raw_sl = entry_price - config.ATR_STOP_MULTIPLIER   * atr
        raw_tp = entry_price + config.ATR_TARGET_MULTIPLIER * atr

        # Clamp stop-loss: must be between FLOOR% and CAP% below entry
        sl_floor = entry_price * (1.0 - config.STOP_LOSS_PCT_CAP)
        sl_cap   = entry_price * (1.0 - config.STOP_LOSS_PCT_FLOOR)
        stop_loss = max(sl_floor, min(raw_sl, sl_cap))

        # Clamp take-profit: must be at least FLOOR% above entry
        tp_floor  = entry_price * (1.0 + config.TAKE_PROFIT_PCT_FLOOR)
        take_profit = max(raw_tp, tp_floor)

        logger.debug(
            "Stops for entry=%.4f | ATR=%.4f | SL=%.4f (%.2f%%) | TP=%.4f (%.2f%%)",
            entry_price, atr, stop_loss,
            (entry_price - stop_loss) / entry_price * 100,
            take_profit,
            (take_profit - entry_price) / entry_price * 100,
        )
        return stop_loss, take_profit

    # ── Position sizing ────────────────────────────────────────────────────────

    def position_size_usd(
        self,
        entry_price:    float,
        prices:         list,
        portfolio_value: float,
        free_usd:       float,
        kelly_fraction: float,
    ) -> float:
        """
        Volatility-targeted position sizing.

        Step 1 — Determine risk fraction:
            risk_pct = min(kelly_fraction, MAX_RISK_PER_TRADE_PCT)

        Step 2 — Convert to a dollar risk amount:
            risk_usd = portfolio_value × risk_pct

        Step 3 — Back-solve for position size given the ATR-based stop distance:
            stop_distance = ATR_STOP_MULTIPLIER × ATR
            position_usd  = risk_usd × (price / stop_distance)
            (smaller ATR → larger position; larger ATR → smaller position)

        Step 4 — Apply hard caps:
            • Never exceed MAX_POSITION_SIZE_PCT of portfolio
            • Never exceed 98% of available USD

        Returns the USD amount to invest (not quantity).
        Returns 0.0 if the computed size falls below minimum thresholds.
        """
        if entry_price <= 0.0 or portfolio_value <= 0.0:
            return 0.0

        atr = ind.atr_proxy(prices, config.ATR_PERIOD)
        if atr == 0.0:
            atr = entry_price * 0.02   # fallback 2% volatility estimate

        risk_pct      = min(kelly_fraction, config.MAX_RISK_PER_TRADE_PCT)
        risk_usd      = portfolio_value * risk_pct
        stop_distance = config.ATR_STOP_MULTIPLIER * atr

        # Volatility-targeted size
        position_usd  = risk_usd * (entry_price / stop_distance)

        # Hard caps
        max_by_pct    = portfolio_value * config.MAX_POSITION_SIZE_PCT
        max_by_cash   = free_usd * 0.98
        position_usd  = min(position_usd, max_by_pct, max_by_cash)

        logger.debug(
            "Sizing: kelly=%.3f risk_usd=%.0f stop_dist=%.4f → pos_usd=%.0f",
            kelly_fraction, risk_usd, stop_distance, position_usd,
        )
        return position_usd
