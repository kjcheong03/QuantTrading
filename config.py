"""
APEX Bot — Configuration
All tuneable parameters in one place.
"""

import os

# ── API Credentials ────────────────────────────────────────────────────────────
API_KEY    = os.getenv("ROOSTOO_API_KEY",    "b6NRfDUvvDVd5zACEHxtML0SbBeZp9abFdeWvPuQXTOfEzdw1kulvyqEZVD8pOuY")
API_SECRET = os.getenv("ROOSTOO_API_SECRET", "62leE85XhYewFsiVjxAwOb1rYm2NO2loADlwj4ZnkR9uePX2Q73EfygBYKzJ5Dik")
BASE_URL   = "https://mock-api.roostoo.com"

# ── Indicator Periods ─────────────────────────────────────────────────────────
EMA_FAST_PERIOD   = 9
EMA_SLOW_PERIOD   = 21
RSI_PERIOD        = 14
RSI_OVERSOLD      = 35    # vote +1 when RSI below this
RSI_OVERBOUGHT    = 65    # vote -1 when RSI above this
MACD_FAST         = 12
MACD_SLOW         = 26
MACD_SIGNAL       = 9
BB_PERIOD         = 20
BB_STD_DEV        = 2.0
ATR_PERIOD        = 14
ADX_PERIOD        = 14
ADX_TREND_THRESH  = 25    # ADX above this → trending market
MOMENTUM_PERIOD   = 10    # lookback for time-series momentum
MOMENTUM_THRESH   = 0.005 # ±0.5% needed to count as directional momentum
MIN_HISTORY_LEN   = 40    # minimum price points required before any signal

# ── Ensemble Signal Thresholds ─────────────────────────────────────────────────
# Max possible vote = ±5 (one vote per indicator)
SIGNAL_THRESHOLD_TREND  = 3   # need 3/5 signals to agree in trending regime
SIGNAL_THRESHOLD_RANGE  = 3   # need 3/5 signals to agree in ranging regime

# ── Cross-Sectional Momentum Filtering ────────────────────────────────────────
CS_MOMENTUM_PERIOD = 20   # lookback period for cross-asset momentum ranking
CS_MIN_MOMENTUM    = 0.0  # only buy pairs with positive 20-period return

# ── ATR-Based Stop / Take-Profit ──────────────────────────────────────────────
ATR_STOP_MULTIPLIER   = 2.0   # stop_loss  = entry − 2.0 × ATR
ATR_TARGET_MULTIPLIER = 2.0   # take_profit = entry + 2.0 × ATR  (1:1 R/R ratio)
STOP_LOSS_PCT_FLOOR   = 0.015 # stop always at least 1.5% below entry
STOP_LOSS_PCT_CAP     = 0.06  # stop never more than 6% below entry
TAKE_PROFIT_PCT_FLOOR = 0.03  # take-profit always at least 3% above entry

# ── Position Sizing ───────────────────────────────────────────────────────────
# Uses Kelly criterion (half-Kelly) + ATR volatility targeting
MAX_RISK_PER_TRADE_PCT  = 0.04  # max 4% of portfolio risked on any single trade
DEFAULT_RISK_PCT        = 0.02  # fallback before Kelly is calibrated (2%)
MAX_POSITION_SIZE_PCT   = 0.20  # hard cap: max 20% of portfolio per position
MAX_TOTAL_EXPOSURE_PCT  = 0.65  # max 65% of portfolio in open crypto positions

# ── Circuit Breaker ───────────────────────────────────────────────────────────
MAX_DRAWDOWN_HALT   = 0.12  # pause all new trades if drawdown hits 12%
MAX_DRAWDOWN_RESUME = 0.08  # resume trading once drawdown recovers below 8%

# ── Fear & Greed Index (alternative.me — free, no auth) ───────────────────────
FEAR_GREED_FEAR_THRESH  = 25   # ≤ 25 → Extreme Fear  → contrarian BUY  vote (+1)
FEAR_GREED_GREED_THRESH = 75   # ≥ 75 → Extreme Greed → contrarian SELL vote (-1)
FEAR_GREED_CACHE_TTL    = 3600 # refresh at most once per hour (index is daily)

# ── Trading Universe ──────────────────────────────────────────────────────────
PREFERRED_PAIRS = [
    # Tier 1 — highest liquidity
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD",
    # Tier 2 — high volatility, strong momentum potential
    "DOGE/USD", "AVAX/USD", "LINK/USD", "NEAR/USD", "SUI/USD",
    # Tier 3 — trending narratives on Roostoo
    "PEPE/USD", "WIF/USD", "TAO/USD", "TRX/USD", "TON/USD",
]

# Roostoo pair → Binance kline symbol (for historical warmup)
BINANCE_SYMBOL_MAP = {
    "BTC/USD":  "BTCUSDT",
    "ETH/USD":  "ETHUSDT",
    "SOL/USD":  "SOLUSDT",
    "BNB/USD":  "BNBUSDT",
    "XRP/USD":  "XRPUSDT",
    "DOGE/USD": "DOGEUSDT",
    "AVAX/USD": "AVAXUSDT",
    "LINK/USD": "LINKUSDT",
    "NEAR/USD": "NEARUSDT",
    "SUI/USD":  "SUIUSDT",
    "PEPE/USD": "PEPEUSDT",
    "WIF/USD":  "WIFUSDT",
    "TAO/USD":  "TAOUSDT",
    "TRX/USD":  "TRXUSDT",
    "TON/USD":  "TONUSDT",
}

# ── Data / Timing ─────────────────────────────────────────────────────────────
LOOP_INTERVAL_SECONDS = 300    # 5-minute cycle — active but not HFT
WARMUP_CANDLES        = 60     # Binance candles fetched per pair on startup
BINANCE_INTERVAL      = "15m"  # candle timeframe used for warmup
MAX_PRICE_HISTORY     = 200    # max prices kept in memory per pair

# ── Logging & Audit ───────────────────────────────────────────────────────────
LOG_FILE       = "apex_bot.log"
TRADE_LOG_FILE = "trade_log.csv"
PERF_LOG_FILE  = "performance_log.csv"
