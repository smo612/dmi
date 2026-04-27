import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


VENDOR_PATH = Path(__file__).resolve().parent / ".vendor" / "fubon_test"
if str(VENDOR_PATH) not in sys.path:
    sys.path.insert(0, str(VENDOR_PATH))

ENV_PATH = Path(__file__).resolve().parent / ".env"
TW = timezone(timedelta(hours=8))
TIMEFRAME_ALIASES = {
    "1": "1",
    "1m": "1",
    "5": "5",
    "5m": "5",
    "10": "10",
    "10m": "10",
    "15": "15",
    "15m": "15",
    "30": "30",
    "30m": "30",
    "60": "60",
    "60m": "60",
}
HISTORICAL_TIMEFRAME_ALIASES = {
    **TIMEFRAME_ALIASES,
    "d": "D",
    "1d": "D",
    "day": "D",
    "w": "W",
    "1w": "W",
    "week": "W",
    "m": "M",
    "1mth": "M",
    "1mo": "M",
    "month": "M",
}

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


def format_tw(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "None"


def normalize_symbol(symbol: str) -> str:
    text = str(symbol).strip().upper()
    if text.endswith(".TWO"):
        return text[:-4]
    if text.endswith(".TW"):
        return text[:-3]
    return text


def normalize_timeframe(timeframe: str) -> str:
    key = str(timeframe).strip().lower()
    if key not in TIMEFRAME_ALIASES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return TIMEFRAME_ALIASES[key]


def normalize_historical_timeframe(timeframe: str) -> str:
    key = str(timeframe).strip().lower()
    if key not in HISTORICAL_TIMEFRAME_ALIASES:
        raise ValueError(f"Unsupported historical timeframe: {timeframe}")
    return HISTORICAL_TIMEFRAME_ALIASES[key]


def parse_bar_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TW)


def extract_candle_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    return rows if isinstance(rows, list) else []


def extract_latest_bar(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    rows = extract_candle_rows(payload)
    if not rows:
        return None
    return rows[-1]


def bar_signature(bar: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not bar:
        return None
    return (
        bar.get("date"),
        bar.get("open"),
        bar.get("high"),
        bar.get("low"),
        bar.get("close"),
        bar.get("volume"),
        bar.get("average"),
    )


@dataclass
class RequestGate:
    min_gap_seconds: float
    _last_call_at: float = 0.0

    def wait(self) -> None:
        if self.min_gap_seconds <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_call_at
        if elapsed < self.min_gap_seconds:
            time.sleep(self.min_gap_seconds - elapsed)
        self._last_call_at = time.monotonic()


class FubonProbeClient:
    def __init__(
        self,
        request_gap_seconds: float = 2.0,
        retries: int = 3,
        retry_sleep_seconds: float = 3.0,
    ):
        _load_dotenv(ENV_PATH)
        self.sdk = FubonSDK()
        self.accounts = _login(self.sdk)
        self.sdk.init_realtime()
        self.stock_client = self.sdk.marketdata.rest_client.stock
        self.gate = RequestGate(min_gap_seconds=max(float(request_gap_seconds), 0.0))
        self.retries = max(int(retries), 1)
        self.retry_sleep_seconds = max(float(retry_sleep_seconds), 0.5)

    def fetch_intraday_candles(self, symbol: str, timeframe: str) -> dict[str, Any]:
        norm_symbol = normalize_symbol(symbol)
        norm_timeframe = normalize_timeframe(timeframe)

        last_error = None
        for attempt in range(1, self.retries + 1):
            self.gate.wait()
            try:
                return self.stock_client.intraday.candles(
                    symbol=norm_symbol,
                    timeframe=norm_timeframe,
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_sleep_seconds * attempt)

        raise RuntimeError(
            f"Fubon intraday candles failed: symbol={norm_symbol} timeframe={norm_timeframe} error={last_error}"
        )

    def fetch_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        sort: str = "asc",
    ) -> dict[str, Any]:
        norm_symbol = normalize_symbol(symbol)
        norm_timeframe = normalize_historical_timeframe(timeframe)

        last_error = None
        for attempt in range(1, self.retries + 1):
            self.gate.wait()
            try:
                return self.stock_client.historical.candles(
                    symbol=norm_symbol,
                    timeframe=norm_timeframe,
                    **{"from": start_date, "to": end_date, "sort": sort},
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_sleep_seconds * attempt)

        raise RuntimeError(
            "Fubon historical candles failed: "
            f"symbol={norm_symbol} timeframe={norm_timeframe} "
            f"from={start_date} to={end_date} error={last_error}"
        )
