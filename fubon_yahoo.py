import argparse
from datetime import datetime

from fubon_probe import (
    TW,
    FubonProbeClient,
    extract_candle_rows,
    extract_latest_bar,
    format_tw,
    parse_bar_time,
)


DEFAULT_SYMBOL = "2330"
DEFAULT_INTERVALS = ["15m", "30m", "60m"]


def check_interval(client: FubonProbeClient, symbol: str, interval: str, target_date: str) -> None:
    payload = client.fetch_intraday_candles(symbol, interval)
    rows = extract_candle_rows(payload)
    latest = extract_latest_bar(payload)

    if not latest:
        print(f"{interval}: no data")
        return

    latest_dt = parse_bar_time(latest.get("date"))
    latest_today = latest_dt.strftime("%Y-%m-%d") == target_date if latest_dt else False

    print(
        f"{interval}: rows={len(rows)} "
        f"latest_bar={format_tw(latest_dt)} "
        f"bar_today={latest_today} "
        f"open={latest.get('open')} high={latest.get('high')} low={latest.get('low')} "
        f"close={latest.get('close')} volume={latest.get('volume')} avg={latest.get('average')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Fubon latest intraday bars by interval")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="TW stock code, e.g. 2330")
    parser.add_argument(
        "--date",
        default=datetime.now(TW).strftime("%Y-%m-%d"),
        help="Target date in YYYY-MM-DD format. Default: today in Asia/Taipei.",
    )
    parser.add_argument(
        "--intervals",
        default=",".join(DEFAULT_INTERVALS),
        help="Comma-separated intervals. Supported: 1m,5m,10m,15m,30m,60m",
    )
    parser.add_argument(
        "--request-gap-seconds",
        type=float,
        default=2.0,
        help="Minimum sleep between Fubon requests to avoid hitting rate limits.",
    )
    parser.add_argument("--retries", type=int, default=3, help="Retry count per request.")
    parser.add_argument(
        "--retry-sleep-seconds",
        type=float,
        default=3.0,
        help="Base retry sleep in seconds.",
    )
    args = parser.parse_args()

    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    client = FubonProbeClient(
        request_gap_seconds=args.request_gap_seconds,
        retries=args.retries,
        retry_sleep_seconds=args.retry_sleep_seconds,
    )

    print(f"login_ok {client.accounts}")
    print(
        f"symbol={args.symbol} target_date={args.date} intervals={','.join(intervals)} "
        f"request_gap_seconds={args.request_gap_seconds}"
    )

    for interval in intervals:
        try:
            check_interval(client, args.symbol, interval, args.date)
        except Exception as exc:
            print(f"{interval}: ERROR -> {exc}")


if __name__ == "__main__":
    main()

