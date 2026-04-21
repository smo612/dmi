import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_DB = "stock_data.db"
DEFAULT_STATE = "market_watch_state.json"
DEFAULT_LOG = "market_watcher.log"
TIMEFRAMES = ["5m", "15m", "30m", "60m", "180m", "240m"]


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_last_log_line(path: Path) -> str:
    if not path.exists():
        return "(log file not found)"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"(failed to read log: {e})"
    for line in reversed(lines):
        if line.strip():
            return line
    return "(log is empty)"


def _latest_intraday(conn: sqlite3.Connection, timeframe: str):
    cur = conn.cursor()
    cur.execute(
        "select max(Datetime), count(*) from intraday_candles where Timeframe=?",
        (timeframe,),
    )
    return cur.fetchone()


def _latest_daily(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("select max(Date), count(*) from daily_candles")
    return cur.fetchone()


def _purple_status(conn: sqlite3.Connection):
    cur = conn.cursor()
    try:
        cur.execute("select count(*), max(TriggerTime), max(ScanAt) from purple_signals")
        return cur.fetchone()
    except sqlite3.Error:
        return None


def _date_part(text):
    if not text:
        return None
    return str(text)[:10]


def main():
    parser = argparse.ArgumentParser(description="檢查 DB / watcher state 是否已更新到今天")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB 路徑")
    parser.add_argument("--state", default=DEFAULT_STATE, help="watcher state 檔路徑")
    parser.add_argument("--log", default=DEFAULT_LOG, help="watcher log 路徑")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="目標交易日，格式 YYYY-MM-DD，預設今天",
    )
    args = parser.parse_args()

    target_date = args.date
    db_path = Path(args.db)
    state_path = Path(args.state)
    log_path = Path(args.log)

    print(f"target_date: {target_date}")
    print(f"db: {db_path}")
    print(f"state: {state_path}")
    print(f"log: {log_path}")
    print()

    state = _read_state(state_path)
    print("[watcher_state]")
    if not state:
        print("state file missing or unreadable")
    else:
        for key in [
            "last_intraday_bar_key",
            "last_intraday_signature",
            "last_intraday_run_ts",
            "last_stale_force_run_ts",
            "last_stale_force_done_ts",
            "last_eod_date",
        ]:
            print(f"{key}: {state.get(key)}")
    print()

    if not db_path.exists():
        print("[db]")
        print("DB file not found")
        print()
        print("[log_tail]")
        print(_read_last_log_line(log_path))
        return

    conn = sqlite3.connect(str(db_path))
    try:
        print("[intraday]")
        intraday_ok = True
        latest_intraday = {}
        for tf in TIMEFRAMES:
            latest_dt, count_rows = _latest_intraday(conn, tf)
            latest_intraday[tf] = latest_dt
            is_today = _date_part(latest_dt) == target_date
            if tf in {"15m", "30m", "60m"} and not is_today:
                intraday_ok = False
            print(
                f"{tf}: latest={latest_dt} count={count_rows} updated_today={is_today}"
            )
        print()

        print("[daily]")
        daily_date, daily_count = _latest_daily(conn)
        daily_ok = _date_part(daily_date) == target_date
        print(f"latest={daily_date} count={daily_count} updated_today={daily_ok}")
        print()

        print("[purple]")
        purple = _purple_status(conn)
        if purple is None:
            print("purple_signals table missing or unreadable")
        else:
            total, max_trigger_time, max_scan_at = purple
            print(f"count={total} latest_trigger={max_trigger_time} latest_scan_at={max_scan_at}")
        print()
    finally:
        conn.close()

    print("[log_tail]")
    print(_read_last_log_line(log_path))
    print()

    bar_key = state.get("last_intraday_bar_key")
    bar_key_today = isinstance(bar_key, str) and bar_key.startswith(target_date.replace("-", ""))
    eod_today = state.get("last_eod_date") == target_date

    print("[summary]")
    print(f"intraday_core_ok={intraday_ok}")
    print(f"daily_ok={daily_ok}")
    print(f"state_bar_key_today={bar_key_today}")
    print(f"state_eod_today={eod_today}")

    if intraday_ok and daily_ok and bar_key_today and eod_today:
        print("overall=OK")
    elif intraday_ok or daily_ok:
        print("overall=PARTIAL")
    else:
        print("overall=STALE")


if __name__ == "__main__":
    main()
