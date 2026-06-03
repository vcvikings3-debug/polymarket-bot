"""On-chain intelligence: funding rates, exchange flows, DEX data, whale activity, technicals."""

import sys
import os
import time
import requests
import numpy as np
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# In-memory cache: key → (data, timestamp)
_cache: dict = {}
_CACHE_TTL = 300.0  # 5 minutes


def _cache_get(key: str):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _cache_set(key: str, data):
    _cache[key] = (data, time.time())


# Coin name → CoinGecko id mapping
_COINGECKO_IDS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "bnb": "binancecoin", "binancecoin": "binancecoin",
    "xrp": "ripple", "ripple": "ripple",
    "cardano": "cardano", "ada": "cardano",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "polygon": "matic-network", "matic": "matic-network",
    "chainlink": "chainlink", "link": "chainlink",
    "uniswap": "uniswap", "uni": "uniswap",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "shiba": "shiba-inu", "shib": "shiba-inu",
    "polkadot": "polkadot", "dot": "polkadot",
    "near": "near", "atom": "cosmos",
    "litecoin": "litecoin", "ltc": "litecoin",
}

# Coin name → Binance futures symbol
_BINANCE_SYMBOLS = {
    "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
    "ethereum": "ETHUSDT", "eth": "ETHUSDT",
    "solana": "SOLUSDT", "sol": "SOLUSDT",
    "bnb": "BNBUSDT", "binancecoin": "BNBUSDT",
    "xrp": "XRPUSDT", "ripple": "XRPUSDT",
    "cardano": "ADAUSDT", "ada": "ADAUSDT",
    "avalanche": "AVAXUSDT", "avax": "AVAXUSDT",
    "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
    "chainlink": "LINKUSDT", "link": "LINKUSDT",
    "polkadot": "DOTUSDT", "dot": "DOTUSDT",
    "near": "NEARUSDT", "atom": "ATOMUSDT",
    "litecoin": "LTCUSDT", "ltc": "LTCUSDT",
}

_CRYPTO_KEYWORDS = list(_COINGECKO_IDS.keys())


def _detect_coin(market_question: str) -> str:
    """Best-guess which coin a market question is about. Returns CoinGecko id."""
    text = market_question.lower()
    for kw, cg_id in _COINGECKO_IDS.items():
        if kw in text:
            return cg_id
    return "bitcoin"  # default to BTC if ambiguous


def _ema(data: list, period: int) -> list:
    if not data:
        return []
    k = 2.0 / (period + 1)
    ema = [data[0]]
    for val in data[1:]:
        ema.append(val * k + ema[-1] * (1 - k))
    return ema


def _calculate_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    arr = np.array(prices, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _bollinger_position(prices: list, period: int = 20) -> str:
    if len(prices) < period:
        return "MIDDLE"
    recent = prices[-period:]
    mean = sum(recent) / period
    std = (sum((p - mean) ** 2 for p in recent) / period) ** 0.5
    if std == 0:
        return "MIDDLE"
    upper = mean + 2 * std
    lower = mean - 2 * std
    current = prices[-1]
    pos = (current - lower) / (upper - lower)
    if pos > 0.8:
        return "ABOVE"
    elif pos < 0.2:
        return "BELOW"
    return "MIDDLE"


def get_funding_rates(symbols: list = None) -> dict:
    """Fetch perpetual futures funding rates from Binance public API."""
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    cache_key = f"funding_{','.join(sorted(symbols))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {}
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        all_rates = resp.json()
        rate_map = {r["symbol"]: float(r["fundingRate"]) for r in all_rates
                    if "symbol" in r and "fundingRate" in r}
        for sym in symbols:
            rate = rate_map.get(sym, 0.0)
            if rate > 0.001:
                label = "EXTREME_BULLISH_POSITIONING"
            elif rate > 0.0001:
                label = "BULLISH_POSITIONING"
            elif rate < -0.001:
                label = "EXTREME_BEARISH_POSITIONING"
            elif rate < -0.0001:
                label = "BEARISH_POSITIONING"
            else:
                label = "NEUTRAL"
            result[sym] = {"rate": rate, "label": label}
        logger.debug("onchain: funding rates fetched for {}", symbols)
    except Exception as e:
        logger.warning("onchain: funding rate fetch failed — {}", e)
        for sym in symbols:
            result[sym] = {"rate": 0.0, "label": "NEUTRAL"}
    _cache_set(cache_key, result)
    return result


def get_exchange_flows(coin: str = "bitcoin") -> dict:
    """Approximate exchange net flow from CoinGecko market data."""
    cg_id = _COINGECKO_IDS.get(coin.lower(), "bitcoin")
    cache_key = f"flows_{cg_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"direction": "NEUTRAL", "magnitude": 0.5, "note": "CoinGecko approximation"}
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "true", "community_data": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        market_data = data.get("market_data", {})
        change_7d = market_data.get("price_change_percentage_7d", 0) or 0
        change_24h = market_data.get("price_change_percentage_24h", 0) or 0
        # Volume trend as proxy: high volume with price up = distribution risk; high vol + down = capitulation
        volume_24h = market_data.get("total_volume", {}).get("usd", 0) or 0
        market_cap = market_data.get("market_cap", {}).get("usd", 1) or 1
        vol_ratio = volume_24h / market_cap
        if change_24h < -3 and vol_ratio > 0.05:
            direction = "INFLOW"  # selling pressure
            magnitude = min(abs(change_24h) / 10, 1.0)
        elif change_7d > 10 and vol_ratio > 0.08:
            direction = "INFLOW"  # distribution phase
            magnitude = 0.6
        elif change_24h > 2 and vol_ratio < 0.03:
            direction = "OUTFLOW"  # accumulation
            magnitude = 0.6
        elif change_7d < -5:
            direction = "OUTFLOW"  # accumulation
            magnitude = 0.5
        else:
            direction = "NEUTRAL"
            magnitude = 0.5
        result = {"direction": direction, "magnitude": round(magnitude, 2),
                  "price_change_24h": change_24h, "price_change_7d": change_7d,
                  "note": "CoinGecko price/volume approximation"}
        logger.debug("onchain: exchange flows for {} — {}", cg_id, direction)
    except Exception as e:
        logger.warning("onchain: exchange flows fetch failed for {} — {}", coin, e)
    _cache_set(cache_key, result)
    return result


def get_dex_volume_trend(protocol: str = None) -> dict:
    """Query DeFiLlama for TVL changes and overall DeFi health."""
    cache_key = f"defi_{protocol or 'total'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"direction": "NEUTRAL", "change_7d_pct": 0.0, "summary": "DeFiLlama data"}
    try:
        if protocol:
            resp = requests.get(
                f"https://api.llama.fi/protocol/{protocol}",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            tvl_history = data.get("tvl", [])
            if len(tvl_history) >= 14:
                recent = tvl_history[-1].get("totalLiquidityUSD", 0)
                week_ago = tvl_history[-8].get("totalLiquidityUSD", 1)
                change_pct = ((recent - week_ago) / week_ago) * 100 if week_ago else 0
            else:
                change_pct = 0.0
        else:
            resp = requests.get("https://api.llama.fi/charts", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if len(data) >= 14:
                recent = data[-1].get("totalLiquidityUSD", 0)
                week_ago = data[-8].get("totalLiquidityUSD", 1)
                change_pct = ((recent - week_ago) / week_ago) * 100 if week_ago else 0
            else:
                change_pct = 0.0

        if change_pct > 10:
            direction = "GROWING"
        elif change_pct > 3:
            direction = "SLIGHTLY_GROWING"
        elif change_pct < -10:
            direction = "DECLINING"
        elif change_pct < -3:
            direction = "SLIGHTLY_DECLINING"
        else:
            direction = "STABLE"

        result = {
            "direction": direction,
            "change_7d_pct": round(change_pct, 2),
            "summary": f"DeFi TVL {direction.lower().replace('_', ' ')}: {change_pct:+.1f}% 7d",
        }
        logger.debug("onchain: DeFiLlama TVL 7d change {:.1f}%", change_pct)
    except Exception as e:
        logger.warning("onchain: DeFiLlama fetch failed — {}", e)
    _cache_set(cache_key, result)
    return result


def get_mempool_signal() -> dict:
    """Query mempool.space for Bitcoin mempool congestion."""
    cache_key = "mempool"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"congestion": "UNKNOWN", "fee_rate_sat_vbyte": 0, "pending_tx": 0}
    try:
        resp = requests.get("https://mempool.space/api/mempool", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pending = data.get("count", 0)
        # Get recommended fee
        fee_resp = requests.get("https://mempool.space/api/v1/fees/recommended", timeout=10)
        fee_resp.raise_for_status()
        fees = fee_resp.json()
        hourly_fee = fees.get("hourFee", 5)

        if hourly_fee > 100:
            congestion = "EXTREME"
        elif hourly_fee > 30:
            congestion = "HIGH"
        elif hourly_fee > 10:
            congestion = "MEDIUM"
        else:
            congestion = "LOW"

        result = {
            "congestion": congestion,
            "fee_rate_sat_vbyte": hourly_fee,
            "pending_tx": pending,
            "note": f"{congestion} mempool congestion — {hourly_fee} sat/vbyte",
        }
        logger.debug("onchain: mempool {} @ {} sat/vbyte", congestion, hourly_fee)
    except Exception as e:
        logger.warning("onchain: mempool.space fetch failed — {}", e)
    _cache_set(cache_key, result)
    return result


def get_whale_activity(coin: str = "bitcoin", threshold_usd: float = 1_000_000) -> dict:
    """Approximate whale activity from CoinGecko large-timeframe volume analysis."""
    cg_id = _COINGECKO_IDS.get(coin.lower(), "bitcoin")
    cache_key = f"whale_{cg_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {
        "net_flow_direction": "NEUTRAL",
        "estimated_volume_usd": 0,
        "note": "Approximate from public market data",
    }
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency": "usd", "days": "7", "interval": "daily"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        prices = [p[1] for p in data.get("prices", [])]
        volumes = [v[1] for v in data.get("total_volumes", [])]
        if len(prices) >= 3 and len(volumes) >= 3:
            price_change = (prices[-1] - prices[0]) / prices[0] if prices[0] else 0
            vol_trend = (volumes[-1] - volumes[0]) / volumes[0] if volumes[0] else 0
            # Accumulation: price falling + volume decreasing or price rising + volume increasing
            # Distribution: price rising fast + volume decreasing
            if price_change < -0.05 and vol_trend < -0.1:
                direction = "ACCUMULATING"
            elif price_change > 0.05 and vol_trend > 0.1:
                direction = "ACCUMULATING"
            elif price_change > 0.1 and vol_trend < -0.2:
                direction = "DISTRIBUTING"
            elif price_change < -0.1:
                direction = "DISTRIBUTING"
            else:
                direction = "NEUTRAL"
            estimated_vol = volumes[-1] if volumes else 0
            result = {
                "net_flow_direction": direction,
                "estimated_volume_usd": int(estimated_vol),
                "price_change_7d": round(price_change * 100, 1),
                "volume_trend_7d": round(vol_trend * 100, 1),
                "note": f"7d analysis: price {price_change:+.1%}, volume {vol_trend:+.1%}",
            }
        logger.debug("onchain: whale activity for {} — {}", cg_id, result.get("net_flow_direction"))
    except Exception as e:
        logger.warning("onchain: whale activity fetch failed for {} — {}", coin, e)
    _cache_set(cache_key, result)
    return result


def get_market_data(coin_id: str, days: int = 7) -> dict:
    """Fetch OHLCV from CoinGecko and calculate RSI, MACD, Bollinger position, volume trend."""
    cg_id = _COINGECKO_IDS.get(coin_id.lower(), coin_id)
    cache_key = f"market_data_{cg_id}_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"rsi": 50.0, "macd_signal": "NEUTRAL", "bb_position": "MIDDLE",
              "volume_trend": "STABLE", "is_available": False}
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency": "usd", "days": str(days), "interval": "daily"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        prices = [p[1] for p in data.get("prices", [])]
        volumes = [v[1] for v in data.get("total_volumes", [])]
        if len(prices) < 5:
            _cache_set(cache_key, result)
            return result

        rsi = _calculate_rsi(prices)
        bb = _bollinger_position(prices)

        # MACD approximation with available data
        if len(prices) >= 12:
            ema12 = _ema(prices, min(12, len(prices) // 2))
            ema26 = _ema(prices, min(26, len(prices) - 1))
            macd_line = [e12 - e26 for e12, e26 in zip(ema12[-9:], ema26[-9:])]
            if len(macd_line) >= 2:
                macd_signal = "BULLISH" if macd_line[-1] > macd_line[-2] else "BEARISH"
            else:
                macd_signal = "NEUTRAL"
        else:
            macd_signal = "NEUTRAL"

        # Volume trend
        if len(volumes) >= 3:
            recent_vol = sum(volumes[-3:]) / 3
            older_vol = sum(volumes[:3]) / 3
            if older_vol > 0:
                vol_change = (recent_vol - older_vol) / older_vol
                volume_trend = "INCREASING" if vol_change > 0.1 else ("DECREASING" if vol_change < -0.1 else "STABLE")
            else:
                volume_trend = "STABLE"
        else:
            volume_trend = "STABLE"

        result = {
            "rsi": rsi,
            "macd_signal": macd_signal,
            "bb_position": bb,
            "volume_trend": volume_trend,
            "current_price": prices[-1] if prices else 0,
            "is_available": True,
        }
        logger.debug("onchain: technicals for {} — RSI={} MACD={} BB={}", cg_id, rsi, macd_signal, bb)
    except Exception as e:
        logger.warning("onchain: market data fetch failed for {} — {}", coin_id, e)
    _cache_set(cache_key, result)
    return result


def calculate_fear_greed_proxy() -> dict:
    """Approximate fear/greed index using free data sources."""
    cache_key = "fear_greed"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"score": 50, "label": "NEUTRAL"}
    try:
        btc_data = get_market_data("bitcoin", days=30)
        btc_flows = get_exchange_flows("bitcoin")
        prices_resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": "30", "interval": "daily"},
            timeout=10,
        )
        prices_resp.raise_for_status()
        prices_data = prices_resp.json()
        prices = [p[1] for p in prices_data.get("prices", [])]
        volumes = [v[1] for v in prices_data.get("total_volumes", [])]

        score = 50.0

        # Price momentum (30%)
        if len(prices) >= 14:
            momentum = (prices[-1] - prices[-14]) / prices[-14] if prices[-14] else 0
            momentum_contribution = min(max(momentum * 100, -30), 30) * 0.3
            score += momentum_contribution

        # Volume trend (25%)
        if len(volumes) >= 7:
            recent_vol = sum(volumes[-3:]) / 3
            older_vol = sum(volumes[-7:-4]) / 3 if len(volumes) >= 7 else recent_vol
            if older_vol > 0:
                vol_change = (recent_vol - older_vol) / older_vol
                score += min(max(vol_change * 50, -15), 15) * 0.25

        # RSI-based sentiment (15%)
        rsi = btc_data.get("rsi", 50.0)
        score += (rsi - 50) * 0.15

        # Exchange flow signal (15%)
        if btc_flows.get("direction") == "OUTFLOW":
            score += 10 * 0.15
        elif btc_flows.get("direction") == "INFLOW":
            score -= 10 * 0.15

        # Volatility (15%) — high volatility = fear
        if len(prices) >= 7:
            recent_prices = prices[-7:]
            avg_p = sum(recent_prices) / len(recent_prices)
            volatility = (sum((p - avg_p) ** 2 for p in recent_prices) / len(recent_prices)) ** 0.5
            vol_pct = volatility / avg_p if avg_p else 0
            score -= min(vol_pct * 100, 15) * 0.15

        score = round(max(0, min(100, score)))

        if score >= 80:
            label = "EXTREME_GREED"
        elif score >= 60:
            label = "GREED"
        elif score >= 40:
            label = "NEUTRAL"
        elif score >= 20:
            label = "FEAR"
        else:
            label = "EXTREME_FEAR"

        result = {"score": score, "label": label}
        logger.debug("onchain: fear/greed proxy = {} ({})", score, label)
    except Exception as e:
        logger.warning("onchain: fear/greed proxy failed — {}", e)
    _cache_set(cache_key, result)
    return result


def build_onchain_context(market: dict) -> dict:
    """Master function — returns OnchainContext dict for the given market."""
    try:
        question = market.get("question", "")
        coin = _detect_coin(question)
        coin_simple = next((k for k, v in _COINGECKO_IDS.items() if v == coin and len(k) > 3), coin)
        binance_sym = _BINANCE_SYMBOLS.get(coin_simple, "BTCUSDT")

        funding_data = get_funding_rates([binance_sym])
        funding = funding_data.get(binance_sym, {"rate": 0.0, "label": "NEUTRAL"})

        flows = get_exchange_flows(coin_simple)
        dex = get_dex_volume_trend()
        mempool = get_mempool_signal()
        whale = get_whale_activity(coin_simple)
        technicals = get_market_data(coin, days=14)
        fear_greed = calculate_fear_greed_proxy()

        funding_label = funding.get("label", "NEUTRAL")
        flow_direction = flows.get("direction", "NEUTRAL")
        whale_direction = whale.get("net_flow_direction", "NEUTRAL")
        rsi = technicals.get("rsi", 50.0)
        macd = technicals.get("macd_signal", "NEUTRAL")
        bb = technicals.get("bb_position", "MIDDLE")
        vol_trend = technicals.get("volume_trend", "STABLE")
        fg_score = fear_greed.get("score", 50)
        fg_label = fear_greed.get("label", "NEUTRAL")

        summary = (
            f"Coin: {coin}. "
            f"Funding: {funding_label} ({funding.get('rate', 0):.5f}). "
            f"Exchange flows: {flow_direction}. "
            f"Whale activity: {whale_direction}. "
            f"RSI: {rsi}, MACD: {macd}, BB: {bb}. "
            f"DeFi TVL: {dex.get('direction', 'STABLE')} ({dex.get('change_7d_pct', 0):+.1f}% 7d). "
            f"Fear/Greed: {fg_score}/100 ({fg_label}). "
            f"Mempool: {mempool.get('congestion', 'UNKNOWN')}."
        )

        return {
            "coin": coin,
            "funding_rate": funding,
            "exchange_flows": flows,
            "dex_health": dex,
            "whale_activity": whale,
            "technicals": technicals,
            "fear_greed": fear_greed,
            "mempool": mempool,
            "summary": summary,
            "is_available": True,
        }
    except Exception as e:
        logger.error("onchain_analyzer.build_onchain_context failed: {}", e)
        return {
            "coin": "bitcoin",
            "funding_rate": {"rate": 0.0, "label": "NEUTRAL"},
            "exchange_flows": {"direction": "NEUTRAL", "magnitude": 0.5},
            "dex_health": {"direction": "NEUTRAL", "change_7d_pct": 0.0},
            "whale_activity": {"net_flow_direction": "NEUTRAL"},
            "technicals": {"rsi": 50.0, "macd_signal": "NEUTRAL", "bb_position": "MIDDLE", "volume_trend": "STABLE"},
            "fear_greed": {"score": 50, "label": "NEUTRAL"},
            "mempool": {"congestion": "UNKNOWN"},
            "summary": "On-chain data unavailable.",
            "is_available": False,
        }
