"""
APEX Bot — Portfolio State & Performance Analytics

Responsibilities:
  • Track open positions with entry price, stop-loss, take-profit.
  • Record portfolio value snapshots every cycle.
  • Compute Sortino, Sharpe, and Calmar ratios in real time
    (the three metrics used in competition scoring).
  • Estimate Kelly criterion fraction from historical trade outcomes.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Position ───────────────────────────────────────────────────────────────────

@dataclass
class Position:
    pair:        str
    quantity:    float
    entry_price: float
    stop_loss:   float
    take_profit: float
    entry_time:  float = field(default_factory=time.time)

    @property
    def unrealised_pnl_pct(self) -> Optional[float]:
        """Returns None until a current price is provided externally."""
        return None

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price * 100.0


# ── Trade record ───────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    pair:        str
    side:        str    # 'BUY' | 'SELL'
    quantity:    float
    price:       float
    timestamp:   float = field(default_factory=time.time)
    pnl_pct:     float = 0.0   # filled on SELL
    reason:      str   = ""


# ── Portfolio state ────────────────────────────────────────────────────────────

class Portfolio:
    """
    Central portfolio tracker.

    Keeps:
      - open positions dict
      - time-series of portfolio values (for drawdown, Calmar)
      - per-period return series (for Sharpe, Sortino)
      - closed trade log (for Kelly calibration)
    """

    def __init__(self, initial_value: float = 1_000_000.0):
        self.positions:  dict[str, Position] = {}
        self.trade_log:  list[TradeRecord]   = []

        # Portfolio value snapshots: list of (unix_timestamp, usd_value)
        self._snapshots: list[tuple[float, float]] = []

        # Per-cycle return series (used for Sharpe / Sortino)
        self._returns:   list[float] = []

        # Running peak for drawdown calculation
        self._peak: float = initial_value

    # ── Snapshot recording ─────────────────────────────────────────────────────

    def record_value(self, value: float) -> None:
        """Call once per trading cycle with the current total portfolio value."""
        now = time.time()
        if self._snapshots:
            prev = self._snapshots[-1][1]
            if prev > 0:
                ret = (value - prev) / prev
                self._returns.append(ret)
        self._snapshots.append((now, value))
        if value > self._peak:
            self._peak = value

    # ── Performance metrics ────────────────────────────────────────────────────

    @property
    def current_value(self) -> float:
        return self._snapshots[-1][1] if self._snapshots else 0.0

    @property
    def current_drawdown(self) -> float:
        """Current drawdown from all-time high (0 = no drawdown, 1 = total loss)."""
        if not self._snapshots or self._peak == 0:
            return 0.0
        return max(0.0, (self._peak - self._snapshots[-1][1]) / self._peak)

    @property
    def max_drawdown(self) -> float:
        """Maximum peak-to-trough drawdown observed so far."""
        if len(self._snapshots) < 2:
            return 0.0
        peak = 0.0
        mdd  = 0.0
        for _, v in self._snapshots:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > mdd:
                mdd = dd
        return mdd

    @property
    def total_return(self) -> float:
        """Total return since first snapshot."""
        if len(self._snapshots) < 2 or self._snapshots[0][1] == 0:
            return 0.0
        return (self._snapshots[-1][1] - self._snapshots[0][1]) / self._snapshots[0][1]

    def sortino_ratio(self, annualise_factor: float = 52_560.0) -> float:
        """
        Sortino ratio from per-cycle returns.
        annualise_factor = sqrt(periods per year).
        Default: 52560 = 365 × 24 × 6 (six 10-min periods per hour).
        Only penalises negative returns (unlike Sharpe which penalises all variance).
        """
        n = len(self._returns)
        if n < 5:
            return 0.0
        mean_r   = sum(self._returns) / n
        downside = [r for r in self._returns if r < 0.0]
        if not downside:
            return float("inf")
        downside_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
        if downside_std == 0.0:
            return float("inf")
        return (mean_r / downside_std) * math.sqrt(annualise_factor)

    def sharpe_ratio(self, annualise_factor: float = 52_560.0) -> float:
        """
        Sharpe ratio from per-cycle returns (risk-free rate assumed ≈ 0
        for a short competition window).
        """
        n = len(self._returns)
        if n < 5:
            return 0.0
        mean_r = sum(self._returns) / n
        std    = math.sqrt(
            sum((r - mean_r) ** 2 for r in self._returns) / max(n - 1, 1)
        )
        if std == 0.0:
            return float("inf")
        return (mean_r / std) * math.sqrt(annualise_factor)

    def calmar_ratio(self) -> float:
        """
        Calmar ratio = total_return / max_drawdown.
        Higher is better; measures return per unit of worst historical drawdown.
        """
        mdd = self.max_drawdown
        if mdd == 0.0:
            return float("inf")
        return self.total_return / mdd

    @staticmethod
    def _safe(value: float, cap: float = 999.0) -> float:
        """Cap infinities so CSV/logging never produces unreadable values."""
        if math.isinf(value) or math.isnan(value):
            return cap
        return round(value, 4)

    def summary(self) -> dict:
        return {
            "total_return_pct": round(self.total_return * 100, 4),
            "sortino":          self._safe(self.sortino_ratio()),
            "sharpe":           self._safe(self.sharpe_ratio()),
            "calmar":           self._safe(self.calmar_ratio()),
            "max_drawdown_pct": round(self.max_drawdown * 100, 4),
            "current_dd_pct":   round(self.current_drawdown * 100, 4),
            "open_positions":   len(self.positions),
        }

    # ── Kelly criterion ────────────────────────────────────────────────────────

    def kelly_fraction(self) -> float:
        """
        Half-Kelly position sizing fraction derived from closed trade history.
        Returns DEFAULT_RISK_PCT (from config) until enough trades are recorded.

        Formula:  f* = (b·p − q) / b
          b = average_win / average_loss   (reward-to-risk ratio)
          p = win rate
          q = 1 − p

        We use half-Kelly (f* × 0.5) to reduce variance and protect capital.
        """
        import config  # local import to avoid circular dependency

        closed = [t for t in self.trade_log if t.side == "SELL" and t.pnl_pct != 0.0]
        if len(closed) < 5:
            return config.DEFAULT_RISK_PCT

        wins   = [t.pnl_pct / 100.0 for t in closed if t.pnl_pct > 0]
        losses = [abs(t.pnl_pct) / 100.0 for t in closed if t.pnl_pct <= 0]

        if not wins or not losses:
            return config.DEFAULT_RISK_PCT

        win_rate = len(wins) / len(closed)
        avg_win  = sum(wins) / len(wins)
        avg_loss = sum(losses) / len(losses)

        if avg_loss == 0.0:
            return config.MAX_RISK_PER_TRADE_PCT

        b            = avg_win / avg_loss
        full_kelly   = (b * win_rate - (1 - win_rate)) / b
        half_kelly   = max(0.0, full_kelly * 0.5)

        logger.debug(
            "Kelly calibration: win_rate=%.1f%% b=%.2f → half_kelly=%.3f",
            win_rate * 100, b, half_kelly,
        )
        return min(half_kelly, config.MAX_RISK_PER_TRADE_PCT)

    # ── Exposure helper ────────────────────────────────────────────────────────

    def total_exposure_usd(self) -> float:
        """Approximate USD value locked in open positions (based on entry prices)."""
        return sum(p.quantity * p.entry_price for p in self.positions.values())
