"""Load options-scanner configuration.

This module prefers values found in a local `config.toml`. Environment
variables may be used to override values from the file or to provide Schwab
credentials when the TOML file is absent. Environment variables are
expected to be of the form `STOCKPILE_*` (examples below).

Recognized environment variables:
  STOCKPILE_SCHWAB_APP_KEY
  STOCKPILE_SCHWAB_APP_SECRET
  STOCKPILE_SCHWAB_CALLBACK_URL
  STOCKPILE_SCHWAB_TOKEN_FILE
"""

import os
import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).parents[1] / "config.toml"


def load_config() -> dict:
    """Return config dict.

    If `config.toml` exists it is loaded first. Any recognized environment
    variables will then be applied on top of the TOML-derived values so
    environment values take precedence. If the TOML file is missing Schwab
    credentials from environment variables will still be applied.
    """
    cfg: dict = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)

    # Schwab overrides
    schwab_keys = {
        "app_key": os.environ.get("STOCKPILE_SCHWAB_APP_KEY"),
        "app_secret": os.environ.get("STOCKPILE_SCHWAB_APP_SECRET"),
        "callback_url": os.environ.get("STOCKPILE_SCHWAB_CALLBACK_URL"),
        "token_file": os.environ.get("STOCKPILE_SCHWAB_TOKEN_FILE"),
    }
    if any(v is not None for v in schwab_keys.values()):
        s = cfg.setdefault("schwab", {})
        for k, v in schwab_keys.items():
            if v is not None:
                s[k] = v

    return cfg


def get_provider(cfg: dict) -> str:
    """Return 'yahoo', 'schwab', or 'moomoo' from config, defaulting to 'yahoo'."""
    return cfg.get("data_source", {}).get("provider", "yahoo")


def get_schwab_config(cfg: dict) -> dict:
    """Return the [schwab] section with defaults filled in."""
    s = cfg.get("schwab", {})
    return {
        "app_key":      s.get("app_key", ""),
        "app_secret":   s.get("app_secret", ""),
        "callback_url": s.get("callback_url", "https://127.0.0.1:8182/"),
        "token_file":   s.get("token_file", "~/.config/schwab-token.json"),
    }


def get_moomoo_config(cfg: dict) -> dict:
    """Return the [moomoo] section with defaults filled in.

    Keys:
        host: IP address of the OpenD gateway (default '127.0.0.1').
        port: TCP port of the OpenD gateway (default 11111).
    """
    m = cfg.get("moomoo", {})
    return {
        "host": m.get("host", "127.0.0.1"),
        "port": int(m.get("port", 11111)),
    }
