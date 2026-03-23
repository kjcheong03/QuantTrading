"""
APEX Bot — Technical Indicators
Pure functions operating on lists of floats (close prices unless noted).
No external dependencies — standard library only.
"""

import math


# ── Moving Averages ────────────────────────────────────────────────────────────

def sma(prices: list, period: int) -> float:
    """Simple Moving Average of the last `period` values."""
    if not prices:
        return 0.0
    window = prices[-period:] if len(prices) >= period else prices
    return sum(window) / len(window)


def ema(prices: list, period: int) -> float:
    """Exponential Moving Average — returns the latest EMA value."""
    if not prices:
        return 0.0
    if len(prices) == 1:
        return prices[0]
    k = 2.0 / (period + 1)
    result = prices[0]
    for p in prices[1:]:
        result = p * k + result * (1.0 - k)
    return result


def ema_series(prices: list, period: int) -> list:
    """Full EMA series (same length as input)."""
    if not prices:
        return []
    k = 2.0 / (period + 1)
    out = [prices[0]]
    for p in prices[1:]:
        out.append(p * k + out[-1] * (1.0 - k))
    return out


# ── Oscillators ────────────────────────────────────────────────────────────────

def rsi(prices: list, period: int = 14) -> float:
    """
    Relative Strength Index (0–100).
    Returns 50 (neutral) when there is insufficient data.
    """
    if len(prices) < period + 1:
        return 50.0

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    prices: list,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple:
    """
    MACD indicator.
    Returns (macd_line, signal_line, histogram) — latest values only.
    Returns (0, 0, 0) when there is insufficient data.
    """
    if len(prices) < slow + signal:
        return 0.0, 0.0, 0.0

    fast_s = ema_series(prices, fast)
    slow_s = ema_series(prices, slow)

    # MACD line starts where the slow EMA is fully seeded
    macd_s = [f - s for f, s in zip(fast_s[slow - 1:], slow_s[slow - 1:])]

    if len(macd_s) < signal:
        return macd_s[-1], 0.0, macd_s[-1]

    sig_line  = ema_series(macd_s, signal)[-1]
    macd_line = macd_s[-1]
    histogram = macd_line - sig_line
    return macd_line, sig_line, histogram


# ── Volatility ─────────────────────────────────────────────────────────────────

def rolling_std(prices: list, period: int) -> float:
    """Sample standard deviation over the last `period` values."""
    window = prices[-period:] if len(prices) >= period else prices
    n = len(window)
    if n < 2:
        return 0.0
    mean = sum(window) / n
    return math.sqrt(sum((x - mean) ** 2 for x in window) / (n - 1))


def bollinger_bands(
    prices: list,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple:
    """
    Bollinger Bands.
    Returns (upper_band, middle_band, lower_band).
    """
    if not prices:
        return 0.0, 0.0, 0.0
    window = prices[-period:] if len(prices) >= period else prices
    mid    = sum(window) / len(window)
    n      = len(window)
    std    = math.sqrt(sum((x - mid) ** 2 for x in window) / max(n - 1, 1))
    return mid + std_dev * std, mid, mid - std_dev * std


def atr_proxy(prices: list, period: int = 14) -> float:
    """
    ATR proxy computed from absolute price changes.
    Used because the Roostoo ticker does not expose OHLC candles.
    Approximates the average true range per polling interval.
    """
    if len(prices) < 2:
        return 0.0
    abs_changes = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    return ema(abs_changes, period)


# ── Trend / Directional Strength ───────────────────────────────────────────────

def adx_proxy(prices: list, period: int = 14) -> float:
    """
    Simplified ADX proxy (0–100) using signed price-change directional movement.
    Values above ~25 indicate a trending regime; below ~20 indicate ranging.
    Without H/L candle data, this is computed from close-to-close changes.
    """
    if len(prices) < period + 1:
        return 0.0

    deltas  = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    pos_dm  = [max(d, 0.0) for d in deltas]
    neg_dm  = [abs(min(d, 0.0)) for d in deltas]

    avg_pos = ema(pos_dm, period)
    avg_neg = ema(neg_dm, period)
    total   = avg_pos + avg_neg

    if total == 0.0:
        return 0.0
    return 100.0 * abs(avg_pos - avg_neg) / total


def momentum_return(prices: list, period: int = 10) -> float:
    """
    Simple rate-of-change over `period` bars.
    Positive → uptrend, negative → downtrend.
    """
    if len(prices) < period + 1:
        return 0.0
    base = prices[-(period + 1)]
    if base == 0.0:
        return 0.0
    return (prices[-1] - base) / base


def bb_percent_b(price: float, upper: float, lower: float) -> float:
    """
    Bollinger %B — position of price relative to the bands.
    0 = at lower band, 1 = at upper band, >1 = above upper, <0 = below lower.
    """
    span = upper - lower
    if span == 0.0:
        return 0.5
    return (price - lower) / span
