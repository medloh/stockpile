#!/usr/bin/env python3
"""Schwab OAuth helper — run on initial setup and every 7 days after.

Saves a Schwab API token to disk. Run this before using Schwab as a data
source (CLI --data-source schwab, the web UI, or the trading dashboard),
and re-run it whenever the refresh token expires.

Two modes:

    uv run options-scanner/schwab_auth.py
        Browser flow. Opens a browser locally and captures the OAuth
        redirect automatically. Use this on your own machine.

    uv run options-scanner/schwab_auth.py --manual
        Headless flow for a remote/cloud host with no browser. Prints the
        login URL; you open it in any browser, log in, and paste the
        redirected URL back. The paste is read with getpass (hidden), so
        the one-time code never shows on screen or in a screen share.

Schwab issues a 7-day refresh token at OAuth time. Refreshing the access
token does NOT extend it, so its lifetime is capped at 7 days from this
script's last successful run. After that the next quote/chain call fails
and the tools surface a "Could not fetch ..." error — re-running this
script fixes it.

Security: the token file and config.toml both hold secrets, so this
script chmods them to 0600 (owner-only). That matters on shared
remote/cloud hosts; on Windows it's effectively a no-op (ACL-based).
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path


def _harden(path: Path) -> None:
    """Restrict a secrets file to owner read/write (0600).

    Effective on POSIX (the remote/cloud hosts the --manual flow targets);
    a near no-op on Windows, which is ACL-based. Best-effort — never fatal.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _manual_flow(app_key: str, app_secret: str, callback_url: str,
                 token_path: Path):
    """Headless OAuth: print the login URL, read the redirect URL with no echo.

    Mirrors schwab-py's client_from_manual_flow but reads the redirected URL
    with getpass instead of input(), so the one-time authorization code it
    carries isn't displayed. State/CSRF protection is preserved by reusing
    the same auth_context for the URL and the exchange.
    """
    import schwab.auth as sa

    auth_context = sa.get_auth_context(app_key, callback_url)

    print("\nManual Schwab login (no local browser needed):\n")
    print("  1. Open this URL in any browser and log in:\n")
    print("     " + auth_context.authorization_url + "\n")
    print("  2. Approve access. Your browser will redirect to your callback")
    print("     URL — it may show a connection error, which is expected.")
    print("  3. Copy the ENTIRE redirected URL from the address bar and paste")
    print("     it below. The paste is hidden on purpose: it contains a")
    print("     one-time code, so it won't show on screen or in a recording.\n")

    def _token_write(token, *args, **kwargs):
        with open(token_path, "w") as f:
            json.dump(token, f)

    received_url = getpass.getpass("Redirect URL (hidden)> ").strip()
    # Confirm the paste registered without revealing the one-time code it
    # carries: print only the length and whether it begins with the expected
    # callback URL — never any of the URL's characters.
    starts_ok = received_url.startswith(callback_url)
    print(f"  Got {len(received_url)} characters "
          f"(starts with your callback URL: {'yes' if starts_ok else 'NO'}).")
    if not starts_ok:
        print("  Heads up: that doesn't start with your callback URL. If the "
              "exchange fails, re-run and paste the full redirected URL.")
    return sa.client_from_received_url(
        app_key, app_secret, auth_context, received_url, _token_write,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authenticate with Schwab and save an API token."
    )
    parser.add_argument(
        "--manual", action="store_true",
        help="Headless flow: paste the redirected URL instead of opening a "
             "browser. Use on a remote/cloud host with no browser.",
    )
    args = parser.parse_args()

    from options_scanner.config import load_config, get_schwab_config

    cfg = load_config()
    config_path = Path(__file__).parent / "config.toml"
    schwab_cfg = get_schwab_config(cfg)

    if not config_path.exists() and not schwab_cfg["app_key"] and not schwab_cfg["app_secret"]:
        example = config_path.parent / "config.toml.example"
        sys.exit(
            f"config.toml not found.\n"
            f"Copy {example} to {config_path} and fill in your credentials, "
            "or set the STOCKPILE_SCHWAB_* environment variables."
        )

    if not schwab_cfg["app_key"] or schwab_cfg["app_key"].startswith("your-"):
        sys.exit("Set app_key in options-scanner/config.toml first or export STOCKPILE_SCHWAB_APP_KEY.")
    if not schwab_cfg["app_secret"] or schwab_cfg["app_secret"].startswith("your-"):
        sys.exit("Set app_secret in options-scanner/config.toml first or export STOCKPILE_SCHWAB_APP_SECRET.")

    # config.toml holds your app_secret — tighten its perms before going
    # further (relevant on shared remote/cloud hosts).
    if config_path.exists():
        _harden(config_path)

    token_path = Path(schwab_cfg["token_file"]).expanduser()
    if token_path.exists():
        token_path.unlink()
        print("Removed existing token — starting fresh login.")

    print(f"Token will be saved to: {token_path}")

    try:
        if args.manual:
            _manual_flow(
                schwab_cfg["app_key"], schwab_cfg["app_secret"],
                schwab_cfg["callback_url"], token_path,
            )
        else:
            from stocks_shared.schwab_live import get_client
            print("Opening browser for Schwab OAuth...")
            get_client(
                schwab_cfg["app_key"],
                schwab_cfg["app_secret"],
                schwab_cfg["callback_url"],
                schwab_cfg["token_file"],
            )
        # The token now exists — restrict its perms. schwab-py rewrites the
        # file in place on later refreshes (open 'w' preserves the mode), so
        # this single chmod keeps it 0600 going forward.
        _harden(token_path)
        print("Authentication successful! Token saved (0600).")
        print("You can now use Schwab as a data source.")
    except ValueError as exc:
        sys.exit(str(exc))
    except Exception as exc:  # noqa: BLE001 — surface manual-flow / library errors
        sys.exit(f"Schwab authentication failed: {exc}")


if __name__ == "__main__":
    main()
