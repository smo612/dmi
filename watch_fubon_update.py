import argparse
import time
from datetime import datetime, timedelta

from fubon_probe import (
    TW,
    FubonProbeClient,
    bar_signature,
    extract_latest_bar,
    format_tw,
    parse_bar_time,
)


DEFAULT_SYMBOL = "2330"
DEFAULT_INTERVALS = ["15m", "30m", "60m"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poll Fubon candles and observe when each interval appears or mutates"
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="TW stock code, default 2330")
    parser.add_argument(
        "--date",
        default=datetime.now(TW).strftime("%Y-%m-%d"),
        help="Target date YYYY-MM-DD. Default: today in Asia/Taipei.",
    )
    parser.add_argument(
        "--intervals",
        default=",".join(DEFAULT_INTERVALS),
        help="Comma-separated intervals. Supported: 1m,5m,10m,15m,30m,60m",
    )
    parser.add_argument("--poll-seconds", type=int, default=60, help="Polling interval in seconds.")
    parser.add_argument(
        "--request-gap-seconds",
        type=float,
        default=2.0,
        help="Minimum sleep between Fubon requests to avoid aggressive polling.",
    )
    parser.add_argument("--retries", type=int, default=3, help="Retry count per request.")
    parser.add_argument(
        "--retry-sleep-seconds",
        type=float,
        default=3.0,
        help="Base retry sleep in seconds.",
    )
    parser.add_argument("--timeout-minutes", type=int, default=180, help="Stop after N minutes.")
    args = parser.parse_args()

    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    client = FubonProbeClient(
        request_gap_seconds=args.request_gap_seconds,
        retries=args.retries,
        retry_sleep_seconds=args.retry_sleep_seconds,
    )
    deadline = datetime.now(TW) + timedelta(minutes=args.timeout_minutes)

    state: dict[str, dict] = {
        interval: {
            "latest_ts": None,
            "latest_signature": None,
            "first_seen_at": None,
            "first_stable_at": None,
            "unchanged_polls": 0,
        }
        for interval in intervals
    }

    print(f"login_ok {client.accounts}")
    print(
        f"symbol={args.symbol} target_date={args.date} intervals={','.join(intervals)} "
        f"poll_seconds={args.poll_seconds} request_gap_seconds={args.request_gap_seconds}"
    )

    while True:
        now = datetime.now(TW)
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] poll")

        for interval in intervals:
            try:
                payload = client.fetch_intraday_candles(args.symbol, interval)
                latest = extract_latest_bar(payload)
                latest_dt = parse_bar_time(latest.get("date")) if latest else None
                sig = bar_signature(latest)
                item = state[interval]

                if latest is None:
                    print(f"{interval}: WAIT no_data")
                    continue

                latest_date = latest_dt.strftime("%Y-%m-%d") if latest_dt else ""
                bar_today = latest_date == args.date

                if item["latest_ts"] != latest_dt:
                    item["latest_ts"] = latest_dt
                    item["latest_signature"] = sig
                    item["first_seen_at"] = now
                    item["first_stable_at"] = None
                    item["unchanged_polls"] = 0
                    print(
                        f"{interval}: NEW_BAR first_seen={now.strftime('%H:%M:%S')} "
                        f"bar={format_tw(latest_dt)} bar_today={bar_today} "
                        f"close={latest.get('close')} volume={latest.get('volume')}"
                    )
                    continue

                if item["latest_signature"] != sig:
                    item["latest_signature"] = sig
                    item["first_stable_at"] = None
                    item["unchanged_polls"] = 0
                    print(
                        f"{interval}: UPDATE_BAR seen_since={item['first_seen_at'].strftime('%H:%M:%S')} "
                        f"bar={format_tw(latest_dt)} close={latest.get('close')} volume={latest.get('volume')}"
                    )
                    continue

                item["unchanged_polls"] += 1
                if item["unchanged_polls"] == 1:
                    item["first_stable_at"] = now
                    print(
                        f"{interval}: STABLE first_stable={now.strftime('%H:%M:%S')} "
                        f"bar={format_tw(latest_dt)} close={latest.get('close')} volume={latest.get('volume')}"
                    )
                else:
                    print(
                        f"{interval}: HOLD polls={item['unchanged_polls']} "
                        f"bar={format_tw(latest_dt)} close={latest.get('close')} volume={latest.get('volume')}"
                    )
            except Exception as exc:
                print(f"{interval}: ERROR -> {exc}")

        if datetime.now(TW) >= deadline:
            print("\ntimeout reached")
            break

        time.sleep(max(args.poll_seconds, 1))

    print("\nsummary")
    for interval in intervals:
        item = state[interval]
        latest_ts = item["latest_ts"]
        first_seen = item["first_seen_at"]
        stable_at = item["first_stable_at"]
        print(
            f"{interval}: latest_bar={format_tw(latest_ts)} "
            f"first_seen={first_seen.strftime('%Y-%m-%d %H:%M:%S') if first_seen else 'None'} "
            f"first_stable={stable_at.strftime('%Y-%m-%d %H:%M:%S') if stable_at else 'None'} "
            f"unchanged_polls={item['unchanged_polls']}"
        )


if __name__ == "__main__":
    main()

