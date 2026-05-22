#!/usr/bin/env python3
"""Schwab OAuth helper — run on initial setup and every 7 days after.

Opens a browser, logs in to Schwab, and saves the token to disk. Run
this before using --data-source schwab or selecting Schwab in the web
UI, and re-run it whenever the refresh token expires.

Schwab issues a 7-day refresh token at OAuth time. Refreshing the
access token does NOT extend the refresh token, so its lifetime is
capped at 7 days from this script's last successful run. After that
the next quote/chain call fails and the scanner surfaces a generic
"Could not fetch live price" error — re-running this script fixes it.

Usage:
    uv run options-scanner/schwab_auth.py
"""

import sys
from pathlib import Path


def main() -> None:
    from options_scanner.config import load_config, get_schwab_config, get_provider

    cfg = load_config()
    config_path = Path(__file__).parent / "config.toml"

    if not config_path.exists():
        example = config_path.parent / "config.toml.example"
        sys.exit(
            f"config.toml not found.\n"
            f"Copy {example} to {config_path} and fill in your credentials."
        )

    schwab_cfg = get_schwab_config(cfg)

    if not schwab_cfg["app_key"] or schwab_cfg["app_key"].startswith("your-"):
        sys.exit("Set app_key in options-scanner/config.toml first.")
    if not schwab_cfg["app_secret"] or schwab_cfg["app_secret"].startswith("your-"):
        sys.exit("Set app_secret in options-scanner/config.toml first.")

    from stocks_shared.schwab_live import get_client

    token_path = Path(schwab_cfg["token_file"]).expanduser()
    if token_path.exists():
        token_path.unlink()
        print(f"Removed existing token — starting fresh login.")

    print("Opening browser for Schwab OAuth...")
    print(f"Token will be saved to: {token_path}")

    try:
        get_client(
            schwab_cfg["app_key"],
            schwab_cfg["app_secret"],
            schwab_cfg["callback_url"],
            schwab_cfg["token_file"],
        )
        print("Authentication successful! Token saved.")
        print("You can now run the scanner with --data-source schwab.")
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
