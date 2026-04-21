import argparse
import os
import sys
import time
from pathlib import Path


VENDOR_PATH = Path(__file__).resolve().parent / ".vendor" / "fubon_test"
if str(VENDOR_PATH) not in sys.path:
    sys.path.insert(0, str(VENDOR_PATH))

ENV_PATH = Path(__file__).resolve().parent / ".env"

from fubon_neo.sdk import FubonSDK  # noqa: E402


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _login(sdk: FubonSDK):
    personal_id = _env("FUBON_ID")
    cert_path = _env("FUBON_CERT_PATH")
    cert_pass = _env("FUBON_CERT_PASS")
    api_key = _env("FUBON_API_KEY")
    password = _env("FUBON_PASSWORD")

    if not personal_id or not cert_path:
        raise RuntimeError("Missing FUBON_ID or FUBON_CERT_PATH")

    if api_key:
        return sdk.apikey_login(personal_id, api_key, cert_path, cert_pass or None)
    if not password:
        raise RuntimeError("Missing FUBON_PASSWORD or FUBON_API_KEY")
    return sdk.login(personal_id, password, cert_path, cert_pass or None)


def main():
    _load_dotenv(ENV_PATH)

    parser = argparse.ArgumentParser(description="Minimal Fubon Neo market-data smoke test")
    parser.add_argument("--symbol", default="2330", help="TW stock code, e.g. 2330")
    parser.add_argument("--timeframe", default="15", help="1/5/10/15/30/60")
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    sdk = FubonSDK()
    accounts = _login(sdk)
    print("login_ok", accounts)

    sdk.init_realtime()
    client = sdk.marketdata.rest_client.stock

    last_error = None
    for attempt in range(1, args.retries + 1):
        try:
            data = client.intraday.candles(symbol=args.symbol, timeframe=args.timeframe)
            print("candles_ok")
            print(data)
            return
        except Exception as exc:
            last_error = exc
            print(f"attempt_{attempt}_failed", type(exc).__name__, exc)
            if attempt < args.retries:
                time.sleep(1.5 * attempt)

    raise SystemExit(f"candles_failed: {last_error}")


if __name__ == "__main__":
    main()
