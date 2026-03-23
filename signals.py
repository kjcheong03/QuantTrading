"""
APEX Bot — Ensemble Signal Engine

Combines six independent signals into a single directional vote.
Each signal contributes ±1 or 0; a configurable threshold determines trade action.

Signals (level-based — persistent while condition holds):
  1. EMA alignment     (9/21)    — bullish while fast > slow
  2. MACD histogram    (12/26/9) — bullish while histogram > 0
  3. RSI               (14)      — oversold (<35) or overbought (>65)
  4. Bollinger %B      (20, 2σ)  — price position within volatility envelope
  5. Time-series momentum (10-period return) — absolute trend confirmation
  6. Fear & Greed Index (alternative.me) — contrarian sentiment overlay

Regime detection (ADX proxy):
  • TRENDING (ADX > threshold): macro trend present → momentum signals weighted higher
  • RANGING  (ADX ≤ threshold): sideways market → mean-reversion signals weighted higher
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

import config
import indicators as ind


# ── Fear & Greed Index cache ───────────────────────────────────────────────────

_fg_cache: dict = {"value": None, "fetched_at": 0.0}

def _fetch_fear_greed() -> Optional[int]:
    """
    Fetch the Crypto Fear & Greed Index from alternative.me.
    Returns an integer 0–100 (0 = Extreme Fear, 100 = Extreme Greed).
    Caches the result for FEAR_GREED_CACHE_TTL seconds (index is daily).
    Returns None on any network failure so the signal degrades gracefully.
    """
    now = time.time()
    if (_fg_cache["value"] is not None and
            now - _fg_cache["fetched_at"] < config.FEAR_GREED_CACHE_TTL):
        return _fg_cache["value"]
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=5,
        )
        value = int(resp.json()["data"][0]["value"])
        _fg_cache["value"]      = value
        _fg_cache["fetched_at"] = now
        logging.getLogger(__name__).info("Fear & Greed Index: %d", value)
        return value
    except Exception as exc:
        logging.getLogger(__name__).warning("Fear & Greed fetch failed: %s", exc)
        return _fg_cache["value"]  # return stale value if available

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """Full diagnostic snapshot for one pair at one point in time."""
    pair:        str
    action:      str            # 'BUY' | 'SELL' | 'HOLD'
    vote:        int            # raw ensemble vote  (−6 to +6)
    regime:      str            # 'TRENDING' | 'RANGING'
    rsi:         float
    macd_hist:   float
    bb_pct_b:    float          # Bollinger %B
    momentum:    float          # 10-period return
    adx:         float          # ADX proxy value
    ema_fast:    float
    ema_slow:    float
    fear_greed:  Optional[int]  # 0–100 (None if unavailable)


class SignalEngine:
    """
    Computes the ensemble trading signal for a single pair
    given its price history.
    """

    def evaluate(self, pair: str, prices: list) -> SignalResult:
        """
        Returns a SignalResult.  action = 'HOLD' when history is too short
        or the vote does not meet the threshold.
        """
        null = SignalResult(
            pair=pair, action="HOLD", vote=0, regime="UNKNOWN",
            rsi=50.0, macd_hist=0.0, bb_pct_b=0.5,
            momentum=0.0, adx=0.0, ema_fast=0.0, ema_slow=0.0,
            fear_greed=None,
        )
        if len(prices) < config.MIN_HISTORY_LEN:
            return null

        price = prices[-1]

        # ── Compute all indicators ─────────────────────────────────────────────
        rsi_val                  = ind.rsi(prices, config.RSI_PERIOD)
        macd_line, sig_l, m_hist = ind.macd(prices, config.MACD_FAST,
                                            config.MACD_SLOW, config.MACD_SIGNAL)
        bb_up, _, bb_lo          = ind.bollinger_bands(prices, config.BB_PERIOD,
                                                       config.BB_STD_DEV)
        pct_b                    = ind.bb_percent_b(price, bb_up, bb_lo)
        mom                      = ind.momentum_return(prices, config.MOMENTUM_PERIOD)
        adx_val                  = ind.adx_proxy(prices, config.ADX_PERIOD)
        fast_now                 = ind.ema(prices, config.EMA_FAST_PERIOD)
        slow_now                 = ind.ema(prices, config.EMA_SLOW_PERIOD)

        # ── Regime classification ──────────────────────────────────────────────
        regime    = "TRENDING" if adx_val > config.ADX_TREND_THRESH else "RANGING"
        threshold = (config.SIGNAL_THRESHOLD_TREND if regime == "TRENDING"
                     else config.SIGNAL_THRESHOLD_RANGE)

        # ── Signal votes ───────────────────────────────────────────────────────
        # Uses LEVEL-based signals (persistent while condition holds) so the
        # bot actively evaluates entries every cycle, not just at crossover events.
        vote = 0

        # 1. EMA alignment — bullish while fast EMA is above slow EMA
        if fast_now > slow_now:
            vote += 1
        elif fast_now < slow_now:
            vote -= 1

        # 2. MACD histogram direction — bullish while histogram is positive
        if m_hist > 0.0:
            vote += 1
        elif m_hist < 0.0:
            vote -= 1

        # 3. RSI — oversold/overbought zones
        if rsi_val < config.RSI_OVERSOLD:
            vote += 1
        elif rsi_val > config.RSI_OVERBOUGHT:
            vote -= 1

        # 4. Bollinger Band %B — buy near lower band, sell near upper band
        if pct_b < 0.35:     # below lower third of band
            vote += 1
        elif pct_b > 0.65:   # above upper third of band
            vote -= 1

        # 5. Time-series momentum
        if mom > config.MOMENTUM_THRESH:
            vote += 1
        elif mom < -config.MOMENTUM_THRESH:
            vote -= 1

        # 6. Fear & Greed Index (contrarian — buy fear, sell greed)
        fg = _fetch_fear_greed()
        if fg is not None:
            if fg <= config.FEAR_GREED_FEAR_THRESH:    # extreme fear → contrarian buy
                vote += 1
            elif fg >= config.FEAR_GREED_GREED_THRESH: # extreme greed → contrarian sell
                vote -= 1

        # ── Regime-based gate ──────────────────────────────────────────────────
        # In a trending regime, require momentum signal to agree (avoids
        # entering mean-reversion trades against a strong trend).
        # In a ranging regime, require RSI or BB signal to agree (avoids
        # chasing breakouts in choppy conditions).
        if regime == "TRENDING" and vote > 0 and mom < 0:
            vote -= 1   # penalise buying against negative momentum in trend
        if regime == "TRENDING" and vote < 0 and mom > 0:
            vote += 1   # penalise selling against positive momentum in trend
        if regime == "RANGING" and abs(pct_b - 0.5) < 0.2 and abs(rsi_val - 50) < 15:
            vote = 0    # price near middle of bands with neutral RSI → no edge

        # ── Determine action ───────────────────────────────────────────────────
        if vote >= threshold:
            action = "BUY"
        elif vote <= -threshold:
            action = "SELL"
        else:
            action = "HOLD"

        result = SignalResult(
            pair=pair, action=action, vote=vote, regime=regime,
            rsi=rsi_val, macd_hist=m_hist, bb_pct_b=pct_b,
            momentum=mom, adx=adx_val,
            ema_fast=fast_now, ema_slow=slow_now,
            fear_greed=fg,
        )
        logger.debug(
            "%-10s | %-4s | vote=%+d | regime=%-8s | rsi=%5.1f | "
            "bb%%=%.2f | mom=%+.4f | adx=%4.1f | fg=%s",
            pair, action, vote, regime, rsi_val, pct_b, mom, adx_val,
            fg if fg is not None else "n/a",
        )
        return result
