import argparse
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta


LOCAL_OFFSET = timedelta(hours=8)
INTRADAY_TFS = {"15m", "30m", "60m", "180m", "240m"}


@dataclass
class Candle:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def parse_args():
    parser = argparse.ArgumentParser(
        description="Debug one ticker/timeframe with raw bars, filtered bars, DMI, and MACD."
    )
    parser.add_argument("--db", default="stock_data.db")
    parser.add_argument("--ticker", required=True, help="Ticker, e.g. 1326.TW")
    parser.add_argument("--timeframe", required=True, choices=["1d", "15m", "30m", "60m", "180m", "240m"])
    parser.add_argument("--tail", type=int, default=20, help="How many tail bars to print")
    parser.add_argument("--days", type=int, default=60, help="Only load recent N calendar days for intraday")
    parser.add_argument("--dmi-length", type=int, default=14)
    parser.add_argument("--macd-fast", type=int, default=12)
    parser.add_argument("--macd-slow", type=int, default=26)
    parser.add_argument("--macd-signal", type=int, default=9)
    parser.add_argument("--cross-window", type=int, default=6, help="Report whether the latest cross is inside the last N valid bars")
    parser.add_argument("--lookback-bars", type=int, default=0, help="If > 0, only analyze the last N bars after each cleanup stage")
    return parser.parse_args()


def format_dt(dt: datetime, timeframe: str) -> str:
    if timeframe == "1d":
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def load_candles(db_path: str, ticker: str, timeframe: str, days: int) -> list[Candle]:
    conn = sqlite3.connect(db_path)
    if timeframe == "1d":
        rows = conn.execute(
            """
            SELECT Date, Open, High, Low, Close, Volume
            FROM daily_candles
            WHERE Ticker = ?
            ORDER BY Date ASC
            """,
            (ticker,),
        ).fetchall()
        conn.close()
        return [
            Candle(
                dt=datetime.fromisoformat(str(row[0])),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=int(row[5] or 0),
            )
            for row in rows
        ]

    cutoff_local = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(days, 1))
    cutoff_utc = cutoff_local - LOCAL_OFFSET
    rows = conn.execute(
        """
        SELECT Datetime, Open, High, Low, Close, Volume
        FROM intraday_candles
        WHERE Ticker = ? AND Timeframe = ? AND Datetime >= ?
        ORDER BY Datetime ASC
        """,
        (ticker, timeframe, cutoff_utc.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()
    conn.close()
    candles = []
    for row in rows:
        dt_local = datetime.fromisoformat(str(row[0])) + LOCAL_OFFSET
        candles.append(
            Candle(
                dt=dt_local,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=int(row[5] or 0),
            )
        )
    return candles


def is_valid_bar_time(candle: Candle, timeframe: str) -> bool:
    if timeframe == "1d":
        return True
    if candle.dt.second != 0:
        return False
    minute = candle.dt.minute
    if timeframe == "15m":
        return minute % 15 == 0
    if timeframe == "30m":
        return minute % 30 == 0
    return minute == 0


def is_zero_flat(candle: Candle) -> bool:
    return (
        candle.volume == 0
        and candle.open == candle.high == candle.low == candle.close
    )


def is_close_auction_tail(candle: Candle, timeframe: str) -> bool:
    if timeframe == "1d":
        return False
    return (
        candle.dt.hour == 13
        and candle.dt.minute == 30
        and candle.open == candle.high == candle.low == candle.close
    )


def filter_valid_times(candles: list[Candle], timeframe: str) -> tuple[list[Candle], list[Candle]]:
    valid = []
    invalid = []
    for candle in candles:
        if is_valid_bar_time(candle, timeframe):
            valid.append(candle)
        else:
            invalid.append(candle)
    return valid, invalid


def trim_placeholder_tail(candles: list[Candle]) -> tuple[list[Candle], list[Candle]]:
    trimmed = list(candles)
    removed = []
    while len(trimmed) > 1 and is_zero_flat(trimmed[-1]):
        removed.append(trimmed.pop())
    removed.reverse()
    return trimmed, removed


def trim_close_auction_tail(candles: list[Candle], timeframe: str) -> tuple[list[Candle], list[Candle]]:
    if len(candles) <= 1 or timeframe == "1d":
        return list(candles), []
    if is_close_auction_tail(candles[-1], timeframe):
        return list(candles[:-1]), [candles[-1]]
    return list(candles), []


def calc_wilder_dmi(candles: list[Candle], length: int = 14):
    n = len(candles)
    if n < length + 2:
        return None, None, None, None

    plus_dm = [math.nan] * n
    minus_dm = [math.nan] * n
    tr = [math.nan] * n

    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low

        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr1 = candles[i].high - candles[i].low
        tr2 = abs(candles[i].high - candles[i - 1].close)
        tr3 = abs(candles[i].low - candles[i - 1].close)
        tr[i] = max(tr1, tr2, tr3)

    atr = [math.nan] * n
    plus_smoothed = [math.nan] * n
    minus_smoothed = [math.nan] * n

    seed = slice(1, length + 1)
    atr[length] = sum(tr[seed])
    plus_smoothed[length] = sum(plus_dm[seed])
    minus_smoothed[length] = sum(minus_dm[seed])

    for i in range(length + 1, n):
        atr[i] = atr[i - 1] - (atr[i - 1] / length) + tr[i]
        plus_smoothed[i] = plus_smoothed[i - 1] - (plus_smoothed[i - 1] / length) + plus_dm[i]
        minus_smoothed[i] = minus_smoothed[i - 1] - (minus_smoothed[i - 1] / length) + minus_dm[i]

    plus_di = [math.nan] * n
    minus_di = [math.nan] * n
    for i in range(n):
        if not math.isnan(atr[i]) and atr[i] > 0:
            plus_di[i] = 100.0 * plus_smoothed[i] / atr[i]
            minus_di[i] = 100.0 * minus_smoothed[i] / atr[i]

    dx = [math.nan] * n
    for i in range(n):
        if math.isnan(plus_di[i]) or math.isnan(minus_di[i]):
            continue
        denom = plus_di[i] + minus_di[i]
        if denom > 0:
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom

    adx = [math.nan] * n
    first_adx_idx = (length * 2) - 1
    if n > first_adx_idx:
        seed_dx = [value for value in dx[length:first_adx_idx + 1] if not math.isnan(value)]
        if seed_dx:
            adx[first_adx_idx] = sum(seed_dx) / len(seed_dx)
            for i in range(first_adx_idx + 1, n):
                if math.isnan(dx[i]) or math.isnan(adx[i - 1]):
                    continue
                adx[i] = ((adx[i - 1] * (length - 1)) + dx[i]) / length

    adxr = [math.nan] * n
    for i in range(length, n):
        if not math.isnan(adx[i]) and not math.isnan(adx[i - length]):
            adxr[i] = (adx[i] + adx[i - length]) / 2.0

    return plus_di, minus_di, adx, adxr


def ema(values: list[float], length: int) -> list[float]:
    out = [math.nan] * len(values)
    if len(values) < length:
        return out
    alpha = 2.0 / (length + 1)
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, len(values)):
        prev = (values[i] * alpha) + (prev * (1.0 - alpha))
        out[i] = prev
    return out


def calc_macd(candles: list[Candle], fast: int, slow: int, signal: int):
    closes = [c.close for c in candles]
    if len(closes) < slow:
        return None, None, None
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd = [math.nan] * len(closes)
    for i in range(len(closes)):
        if math.isnan(fast_ema[i]) or math.isnan(slow_ema[i]):
            continue
        macd[i] = fast_ema[i] - slow_ema[i]
    valid_macd = [value for value in macd if not math.isnan(value)]
    signal_values = ema(valid_macd, signal)
    signal_line = [math.nan] * len(closes)
    signal_idx = 0
    for i, value in enumerate(macd):
        if math.isnan(value):
            continue
        signal_line[i] = signal_values[signal_idx]
        signal_idx += 1
    hist = [math.nan] * len(closes)
    for i in range(len(closes)):
        if math.isnan(macd[i]) or math.isnan(signal_line[i]):
            continue
        hist[i] = macd[i] - signal_line[i]
    return macd, signal_line, hist


def latest_valid_pair(series_a, series_b):
    for idx in range(len(series_a) - 1, -1, -1):
        a = series_a[idx]
        b = series_b[idx]
        if not math.isnan(a) and not math.isnan(b):
            return idx, a, b
    return None, None, None


def latest_cross_up(series_a, series_b):
    for idx in range(1, len(series_a)):
        a0 = series_a[idx - 1]
        b0 = series_b[idx - 1]
        a1 = series_a[idx]
        b1 = series_b[idx]
        if any(math.isnan(v) for v in (a0, b0, a1, b1)):
            continue
        if a0 <= b0 and a1 > b1:
            last = idx
    return locals().get("last")


def summarize_dmi(candles: list[Candle], length: int, cross_window: int):
    plus_di, minus_di, adx, adxr = calc_wilder_dmi(candles, length=length)
    if plus_di is None:
        return None
    idx, dp, dm = latest_valid_pair(plus_di, minus_di)
    if idx is None:
        return None
    cross_idx = latest_cross_up(plus_di, minus_di)
    cross_in_window = cross_idx is not None and cross_idx >= (idx - max(cross_window - 1, 0))
    result = {
        "latest_idx": idx,
        "latest_dt": candles[idx].dt,
        "di_plus": dp,
        "di_minus": dm,
        "adx": adx[idx] if not math.isnan(adx[idx]) else None,
        "adxr": adxr[idx] if not math.isnan(adxr[idx]) else None,
        "diff": dp - dm,
        "cross_idx": cross_idx,
        "cross_dt": candles[cross_idx].dt if cross_idx is not None else None,
        "cross_in_window": cross_in_window,
    }
    return result


def summarize_macd(candles: list[Candle], fast: int, slow: int, signal: int, cross_window: int):
    macd, signal_line, hist = calc_macd(candles, fast=fast, slow=slow, signal=signal)
    if macd is None:
        return None
    idx, macd_val, signal_val = latest_valid_pair(macd, signal_line)
    if idx is None:
        return None
    cross_idx = latest_cross_up(macd, signal_line)
    cross_in_window = cross_idx is not None and cross_idx >= (idx - max(cross_window - 1, 0))
    return {
        "latest_idx": idx,
        "latest_dt": candles[idx].dt,
        "macd": macd_val,
        "signal": signal_val,
        "hist": hist[idx] if not math.isnan(hist[idx]) else None,
        "cross_idx": cross_idx,
        "cross_dt": candles[cross_idx].dt if cross_idx is not None else None,
        "cross_in_window": cross_in_window,
    }


def print_bar_table(label: str, candles: list[Candle], timeframe: str, tail: int):
    print(f"\n== {label} tail={min(len(candles), tail)} ==")
    print("dt                  open      high      low      close     vol")
    for candle in candles[-tail:]:
        print(
            f"{format_dt(candle.dt, timeframe):19} "
            f"{candle.open:8.2f} {candle.high:8.2f} {candle.low:8.2f} {candle.close:8.2f} {candle.volume:7d}"
        )


def print_summary_block(name: str, candles: list[Candle], timeframe: str, dmi_info, macd_info):
    print(f"\n## {name}")
    if not candles:
        print("bars=0")
        return
    print(
        f"bars={len(candles)} range={format_dt(candles[0].dt, timeframe)} .. {format_dt(candles[-1].dt, timeframe)}"
    )
    if dmi_info is None:
        print("DMI: not enough bars")
    else:
        adx_text = "n/a" if dmi_info["adx"] is None else f"{dmi_info['adx']:.2f}"
        adxr_text = "n/a" if dmi_info["adxr"] is None else f"{dmi_info['adxr']:.2f}"
        cross_text = "none"
        if dmi_info["cross_dt"] is not None:
            cross_text = f"{format_dt(dmi_info['cross_dt'], timeframe)} in_window={dmi_info['cross_in_window']}"
        print(
            "DMI: "
            f"+DI {dmi_info['di_plus']:.2f} / -DI {dmi_info['di_minus']:.2f} "
            f"diff={dmi_info['diff']:.2f} ADX={adx_text} ADXR={adxr_text} "
            f"last_cross={cross_text}"
        )
    if macd_info is None:
        print("MACD: not enough bars")
    else:
        hist_text = "n/a" if macd_info["hist"] is None else f"{macd_info['hist']:.4f}"
        cross_text = "none"
        if macd_info["cross_dt"] is not None:
            cross_text = f"{format_dt(macd_info['cross_dt'], timeframe)} in_window={macd_info['cross_in_window']}"
        print(
            "MACD: "
            f"macd={macd_info['macd']:.4f} signal={macd_info['signal']:.4f} hist={hist_text} "
            f"last_cross={cross_text}"
        )


def apply_lookback(candles: list[Candle], count: int) -> list[Candle]:
    if count <= 0 or len(candles) <= count:
        return list(candles)
    return list(candles[-count:])


def main():
    args = parse_args()
    raw = load_candles(args.db, args.ticker, args.timeframe, args.days)
    valid_time, invalid_time = filter_valid_times(raw, args.timeframe)
    no_placeholder, placeholder_tail = trim_placeholder_tail(valid_time)
    scan_ready, close_auction_tail = trim_close_auction_tail(no_placeholder, args.timeframe)

    raw = apply_lookback(raw, args.lookback_bars)
    valid_time = apply_lookback(valid_time, args.lookback_bars)
    no_placeholder = apply_lookback(no_placeholder, args.lookback_bars)
    scan_ready = apply_lookback(scan_ready, args.lookback_bars)

    print(f"ticker={args.ticker} timeframe={args.timeframe}")
    print(
        f"raw={len(raw)} valid_time={len(valid_time)} invalid_time={len(invalid_time)} "
        f"placeholder_tail_removed={len(placeholder_tail)} close_auction_removed={len(close_auction_tail)} "
        f"scan_ready={len(scan_ready)}"
    )
    if args.lookback_bars > 0:
        print(f"lookback_bars={args.lookback_bars}")

    if invalid_time:
        print("\n== invalid-time bars ==")
        for candle in invalid_time[-args.tail:]:
            print(
                f"{format_dt(candle.dt, args.timeframe):19} "
                f"o={candle.open:.2f} h={candle.high:.2f} l={candle.low:.2f} c={candle.close:.2f} v={candle.volume}"
            )

    if placeholder_tail:
        print("\n== placeholder tail removed ==")
        for candle in placeholder_tail:
            print(
                f"{format_dt(candle.dt, args.timeframe):19} "
                f"o={candle.open:.2f} h={candle.high:.2f} l={candle.low:.2f} c={candle.close:.2f} v={candle.volume}"
            )

    if close_auction_tail:
        print("\n== close auction tail removed ==")
        for candle in close_auction_tail:
            print(
                f"{format_dt(candle.dt, args.timeframe):19} "
                f"o={candle.open:.2f} h={candle.high:.2f} l={candle.low:.2f} c={candle.close:.2f} v={candle.volume}"
            )

    raw_dmi = summarize_dmi(raw, args.dmi_length, args.cross_window)
    raw_macd = summarize_macd(raw, args.macd_fast, args.macd_slow, args.macd_signal, args.cross_window)
    valid_dmi = summarize_dmi(valid_time, args.dmi_length, args.cross_window)
    valid_macd = summarize_macd(valid_time, args.macd_fast, args.macd_slow, args.macd_signal, args.cross_window)
    scan_dmi = summarize_dmi(scan_ready, args.dmi_length, args.cross_window)
    scan_macd = summarize_macd(scan_ready, args.macd_fast, args.macd_slow, args.macd_signal, args.cross_window)

    print_summary_block("raw", raw, args.timeframe, raw_dmi, raw_macd)
    print_summary_block("valid_time", valid_time, args.timeframe, valid_dmi, valid_macd)
    print_summary_block("scan_ready", scan_ready, args.timeframe, scan_dmi, scan_macd)

    print_bar_table("scan_ready bars", scan_ready, args.timeframe, args.tail)


if __name__ == "__main__":
    main()
