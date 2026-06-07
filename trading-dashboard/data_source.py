import time as _time
import threading, random
from pathlib import Path
import yfinance as yf
import requests
from requests.adapters import HTTPAdapter, Retry

def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=1.5, status_forcelist=[429,500,502,503,504], allowed_methods=["GET","POST"], raise_on_status=True)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent":"Mozilla/5.0","Accept":"text/html,application/xhtml+xml,*/*;q=0.8","Accept-Language":"en-US,en;q=0.9"})
    return session

_SESSION = _build_session()
_YF_INTERVAL = {"1m":"1m","3m":"2m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"1h","1d":"1d","1w":"1wk","1M":"1mo"}
_YF_PERIOD = {"1m":"7d","2m":"60d","5m":"60d","15m":"60d","30m":"60d","1h":"730d","1d":"5y","1wk":"10y","1mo":"max"}
_HL_INTERVAL = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d","1w":"1w"}
_HL_MINS = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440,"1w":10080}
_SOURCE_ALIASES = {}

_RATE_LOCK = threading.Lock()
_LAST_CALL = 0.0
_MIN_INTERVAL = 1.2  # seconds between calls

def _rate_limit():
    global _LAST_CALL
    with _RATE_LOCK:
        now = _time.time()
        wait = _MIN_INTERVAL - (now - _LAST_CALL)
        if wait > 0:
            _time.sleep(wait + random.uniform(0.1, 0.4))  # jitter
        _LAST_CALL = _time.time()

def _to_unix(ts) -> int:
    return int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)

def fetch_yfinance(symbol: str, interval: str, limit: int = 200) -> list:
    _rate_limit()
    yf_iv = _YF_INTERVAL.get(interval, "1d")
    period = _YF_PERIOD.get(yf_iv, "1y")
    # yfinance >= 0.2.52 manages its own curl_cffi session; passing a plain
    # requests.Session raises "Yahoo API requires curl_cffi session". Let YF
    # handle it. The shared _SESSION is still used for Hyperliquid below.
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=yf_iv, auto_adjust=True, timeout=15)
    if df is None or df.empty:
        raise ValueError(f"yfinance: no data for '{symbol}'")
    df = df.dropna(subset=["Open","High","Low","Close"]).tail(limit)
    return [{"time":_to_unix(ts),"open":round(float(r['Open']),4),"high":round(float(r['High']),4),"low":round(float(r['Low']),4),"close":round(float(r['Close']),4),"volume":int(r.get('Volume',0) or 0)} for ts,r in df.iterrows()]

def fetch_hyperliquid(symbol: str, interval: str, limit: int = 200) -> list:
    _rate_limit()
    iv = _HL_INTERVAL.get(interval, "1h")
    mins = _HL_MINS.get(iv, 60)
    coin = symbol.upper().replace("-PERP","").replace("/USDT","").replace("USDT","").strip()
    start_ms = int((_time.time() - mins*60*(limit+5))*1000)
    resp = _SESSION.post("https://api.hyperliquid.xyz/info", json={"type":"candleSnapshot","req":{"coin":coin,"interval":iv,"startTime":start_ms}}, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Hyperliquid: unexpected response for '{coin}'")
    return [{"time":int(c['t'])//1000,"open":float(c['o']),"high":float(c['h']),"low":float(c['l']),"close":float(c['c']),"volume":float(c['v'])} for c in data[-limit:]]

# Schwab credentials are shared with the options-scanner — one setup per repo.
_SCHWAB_CONFIG_PATH = Path(__file__).resolve().parents[1] / "options-scanner" / "config.toml"
# Parsed Schwab config, read once: (app_key, app_secret, callback_url,
# token_file). The client itself is cached by stocks_shared.schwab_live.
# get_client, which rebuilds it when the token file changes — so re-running
# schwab_auth.py is picked up without restarting the dashboard.
_schwab_cfg: tuple | None = None

def _get_schwab_client():
    """Return the shared Schwab client, built from the scanner's config.

    The config is parsed once; the client is cached (and refreshed after a
    re-auth) by stocks_shared.schwab_live.get_client, so we call through to
    it every time rather than holding our own client reference.
    """
    global _schwab_cfg
    if _schwab_cfg is None:
        from options_scanner.config import load_config, get_schwab_config

        if not _SCHWAB_CONFIG_PATH.exists():
            cfg = load_config()
            schwab = get_schwab_config(cfg)
            if not schwab["app_key"] or not schwab["app_secret"]:
                raise ValueError(
                    f"Schwab config not found at {_SCHWAB_CONFIG_PATH}. "
                    "First-time setup: options-scanner/SCHWAB_DATA_SOURCE.md"
                )
        else:
            cfg = load_config()
            schwab = get_schwab_config(cfg)

        app_key, app_secret = schwab["app_key"], schwab["app_secret"]
        if (not app_key or app_key.startswith("your-")
                or not app_secret or app_secret.startswith("your-")):
            raise ValueError(
                "Schwab app_key/app_secret not set in options-scanner/config.toml "
                "or via STOCKPILE_SCHWAB_APP_KEY/STOCKPILE_SCHWAB_APP_SECRET. "
                "First-time setup: options-scanner/SCHWAB_DATA_SOURCE.md"
            )
        _schwab_cfg = (
            app_key, app_secret,
            schwab["callback_url"],
            schwab["token_file"],
        )
    from stocks_shared.schwab_live import get_client
    return get_client(*_schwab_cfg)

def fetch_schwab(symbol: str, interval: str, limit: int = 200) -> list:
    # Authenticated brokerage API — no shared-session rate limiting needed.
    from stocks_shared.schwab_live import fetch_price_history_schwab
    return fetch_price_history_schwab(_get_schwab_client(), symbol, interval, limit)

def fetch_schwab_live_price(symbol: str) -> float | None:
    """Real-time mark for the live ticker number (fresher than daily close)."""
    from stocks_shared.schwab_live import fetch_live_price_schwab
    return fetch_live_price_schwab(_get_schwab_client(), symbol)

_DATASOURCE_REGISTRY = { 'yfinance': fetch_yfinance, 'hyperliquid': fetch_hyperliquid, 'schwab': fetch_schwab }

def fetch_ohlcv(source: str, symbol: str, interval: str, limit: int = 200) -> list:
    source = _SOURCE_ALIASES.get(source, source)
    fn = _DATASOURCE_REGISTRY.get(source)
    if fn is None:
        raise ValueError(f"Unknown source '{source}'. Available: {list(_DATASOURCE_REGISTRY.keys())}")
    return fn(symbol, interval, limit)
