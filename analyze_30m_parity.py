import argparse
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta


LOCAL_OFFSET = timedelta(hours=8)
SUPPORTED_TFS = {"15m", "30m", "60m", "180m", "240m"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare intraday DMI / MACD parity against phone values, with extra 30m experiments."
    )
    parser.add_argument("--db", default="stock_data.db")
    parser.add_argument(
        "--targets",
        default="",
        help="Legacy 30m-only items like 1717.TW:25.63:24.66,1785.TWO:31.6:34.43",
    )
    parser.add_argument(
        "--targets-full",
        default="",
        help=(
            "Items like "
            "1717.TW:15m:22.86:20.75:-0.64:-0.72,1717.TW:30m:25.63:24.66:-1.06:-0.91"
        ),
    )
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--length", type=int, default=14)
    parser.add_argument("--macd-fast", type=int, default=12)
    parser.add_argument("--macd-slow", type=int, default=26)
    parser.add_argument("--macd-signal", type=int, default=9)
    parser.add_argument(
        "--flat-volume-threshold",
        type=int,
        default=500,
        help="Also test removing flat OHLC bars with volume <= this threshold.",
    )
    parser.add_argument(
        "--gate-window-bars",
        type=int,
        default=240,
        help="Use the last N bars to compute the candidate 30m gating features.",
    )
    parser.add_argument(
        "--sweep-bars",
        default="",
        help="Comma-separated last-N bars sweep, e.g. 60,100,150,200,300",
    )
    return parser.parse_args()


def parse_targets(raw: str):
    targets = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        ticker, plus_text, minus_text = chunk.split(":")
        targets.append((ticker.strip(), float(plus_text), float(minus_text)))
    return targets


def parse_full_targets(raw: str):
    targets = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        ticker, timeframe, plus_text, minus_text, dif_text, macd_text = chunk.split(":")
        targets.append(
            {
                "ticker": ticker.strip(),
                "timeframe": timeframe.strip(),
                "phone_plus": float(plus_text),
                "phone_minus": float(minus_text),
                "phone_dif": float(dif_text),
                "phone_macd": float(macd_text),
            }
        )
    return targets


def parse_sweep_bars(raw: str):
    bars = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value = int(chunk)
        if value > 0:
            bars.append(value)
    return bars


def load_intraday(db_path: str, ticker: str, timeframe: str, days: int):
    cutoff_local = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(days, 1))
    cutoff_utc = cutoff_local - LOCAL_OFFSET
    conn = sqlite3.connect(db_path)
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
    bars = []
    for row in rows:
        dt = datetime.fromisoformat(str(row[0])) + LOCAL_OFFSET
        if dt.second != 0:
            continue
        if timeframe == "15m" and dt.minute % 15 != 0:
            continue
        if timeframe == "30m" and dt.minute % 30 != 0:
            continue
        if timeframe in {"60m", "180m", "240m"} and dt.minute != 0:
            continue
        bars.append((dt, float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5] or 0)))
    return bars


def trim_close_auction_tail(bars):
    if len(bars) <= 1:
        return list(bars)
    last = bars[-1]
    if (
        last[0].hour == 13
        and last[0].minute == 30
        and last[1] == last[2] == last[3] == last[4]
    ):
        return list(bars[:-1])
    return list(bars)


def remove_daily_open_0900(bars):
    return [row for row in bars if not (row[0].hour == 9 and row[0].minute == 0)]


def remove_flat_bars(bars, max_volume=None):
    kept = []
    for row in bars:
        is_flat = row[1] == row[2] == row[3] == row[4]
        if not is_flat:
            kept.append(row)
            continue
        if max_volume is None or row[5] <= max_volume:
            continue
        kept.append(row)
    return kept


def count_flat_bars(bars, max_volume=None):
    total = 0
    for row in bars:
        is_flat = row[1] == row[2] == row[3] == row[4]
        if not is_flat:
            continue
        if max_volume is not None and row[5] > max_volume:
            continue
        total += 1
    return total


def flat_run_stats(bars):
    runs = []
    current_run = []
    for row in bars:
        is_flat = row[1] == row[2] == row[3] == row[4]
        if is_flat:
            current_run.append(row)
            continue
        if current_run:
            runs.append(current_run)
            current_run = []
    if current_run:
        runs.append(current_run)
    return {
        "runs": len(runs),
        "max_run": max((len(run) for run in runs), default=0),
        "runs_ge_3": sum(1 for run in runs if len(run) >= 3),
    }


def build_30m_feature_stats(bars, flat_volume_threshold: int):
    stats = {
        "bars": len(bars),
        "flat": count_flat_bars(bars),
        f"flat_le_{flat_volume_threshold}": count_flat_bars(bars, max_volume=flat_volume_threshold),
    }
    if bars:
        stats["flat_share"] = stats["flat"] / len(bars)
        stats[f"flat_le_{flat_volume_threshold}_share"] = stats[f"flat_le_{flat_volume_threshold}"] / len(bars)
    else:
        stats["flat_share"] = 0.0
        stats[f"flat_le_{flat_volume_threshold}_share"] = 0.0
    stats.update(flat_run_stats(bars))
    return stats


def choose_30m_gated_variant(bars, flat_volume_threshold: int, gate_window_bars: int):
    gate_bars = take_last_bars(bars, gate_window_bars)
    stats = build_30m_feature_stats(gate_bars, flat_volume_threshold)
    lowflat_share = stats[f"flat_le_{flat_volume_threshold}_share"]
    flat_share = stats["flat_share"]
    max_run = stats["max_run"]

    if lowflat_share >= 0.20:
        mode_name = "baseline" if max_run >= 5 else f"no_flat<={flat_volume_threshold}"
    elif flat_share >= 0.18:
        mode_name = f"no_flat<={flat_volume_threshold}"
    else:
        mode_name = "no_flat"

    mode_bars = {
        "baseline": bars,
        "no_flat": remove_flat_bars(bars),
        f"no_flat<={flat_volume_threshold}": remove_flat_bars(bars, max_volume=flat_volume_threshold),
    }[mode_name]
    return mode_name, mode_bars, stats


def aggregate_30m_from_15m(bars15, offset_minutes: int):
    buckets = defaultdict(list)
    for row in bars15:
        dt = row[0]
        minute_total = dt.hour * 60 + dt.minute
        bucket_total = ((minute_total - offset_minutes) // 30) * 30 + offset_minutes
        bucket_hour = bucket_total // 60
        bucket_minute = bucket_total % 60
        bucket_dt = dt.replace(hour=bucket_hour, minute=bucket_minute, second=0, microsecond=0)
        buckets[bucket_dt].append(row)

    bars30 = []
    for bucket_dt in sorted(buckets):
        group = buckets[bucket_dt]
        bars30.append(
            (
                bucket_dt,
                group[0][1],
                max(item[2] for item in group),
                min(item[3] for item in group),
                group[-1][4],
                sum(item[5] for item in group),
            )
        )
    return bars30


def build_30m_variants_from_15m(bars15, flat_volume_threshold: int):
    variants = {
        "derived30_off0": trim_close_auction_tail(aggregate_30m_from_15m(bars15, 0)),
        "derived30_off15": trim_close_auction_tail(aggregate_30m_from_15m(bars15, 15)),
        "derived30_15m_no_flat": trim_close_auction_tail(aggregate_30m_from_15m(remove_flat_bars(bars15), 0)),
        f"derived30_15m_no_flat<={flat_volume_threshold}": trim_close_auction_tail(
            aggregate_30m_from_15m(remove_flat_bars(bars15, max_volume=flat_volume_threshold), 0)
        ),
    }
    return variants


def take_last_bars(bars, count: int):
    if count <= 0 or len(bars) <= count:
        return list(bars)
    return list(bars[-count:])


def calc_wilder_dmi(bars, length: int, session_reset: bool = False):
    n = len(bars)
    if n < length + 2:
        return None

    plus_dm = [math.nan] * n
    minus_dm = [math.nan] * n
    tr = [math.nan] * n

    for i in range(1, n):
        same_day = bars[i][0].date() == bars[i - 1][0].date()
        prev_high = bars[i - 1][2]
        prev_low = bars[i - 1][3]
        prev_close = bars[i - 1][4]

        if session_reset and not same_day:
            plus_dm[i] = 0.0
            minus_dm[i] = 0.0
            tr[i] = bars[i][2] - bars[i][3]
            continue

        up_move = bars[i][2] - prev_high
        down_move = prev_low - bars[i][3]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr1 = bars[i][2] - bars[i][3]
        tr2 = abs(bars[i][2] - prev_close)
        tr3 = abs(bars[i][3] - prev_close)
        tr[i] = max(tr1, tr2, tr3)

    atr = [math.nan] * n
    plus_smoothed = [math.nan] * n
    minus_smoothed = [math.nan] * n
    atr[length] = sum(tr[1:length + 1])
    plus_smoothed[length] = sum(plus_dm[1:length + 1])
    minus_smoothed[length] = sum(minus_dm[1:length + 1])

    for i in range(length + 1, n):
        atr[i] = atr[i - 1] - (atr[i - 1] / length) + tr[i]
        plus_smoothed[i] = plus_smoothed[i - 1] - (plus_smoothed[i - 1] / length) + plus_dm[i]
        minus_smoothed[i] = minus_smoothed[i - 1] - (minus_smoothed[i - 1] / length) + minus_dm[i]

    last = max(i for i in range(n) if not math.isnan(atr[i]))
    return (
        plus_smoothed[last] / atr[last] * 100.0,
        minus_smoothed[last] / atr[last] * 100.0,
        bars[last][0],
    )


def ema(values, length: int):
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


def calc_macd(bars, fast: int, slow: int, signal: int):
    closes = [row[4] for row in bars]
    if len(closes) < slow:
        return None
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    dif = [math.nan] * len(closes)
    for i in range(len(closes)):
        if math.isnan(fast_ema[i]) or math.isnan(slow_ema[i]):
            continue
        dif[i] = fast_ema[i] - slow_ema[i]

    valid_dif = [value for value in dif if not math.isnan(value)]
    signal_values = ema(valid_dif, signal)
    signal_line = [math.nan] * len(closes)
    signal_idx = 0
    for i, value in enumerate(dif):
        if math.isnan(value):
            continue
        signal_line[i] = signal_values[signal_idx]
        signal_idx += 1

    for i in range(len(closes) - 1, -1, -1):
        if not math.isnan(dif[i]) and not math.isnan(signal_line[i]):
            return dif[i], signal_line[i], bars[i][0]
    return None


def dmi_score(value, phone_plus: float, phone_minus: float):
    if value is None:
        return None
    return abs(value[0] - phone_plus) + abs(value[1] - phone_minus)


def macd_score(value, phone_dif: float, phone_macd: float):
    if value is None:
        return None
    return abs(value[0] - phone_dif) + abs(value[1] - phone_macd)


def print_dmi_mode(name: str, value, phone_plus: float, phone_minus: float):
    if value is None:
        print(f"  {name:<22} DMI n/a")
        return
    score = dmi_score(value, phone_plus, phone_minus)
    print(
        f"  {name:<22} +DI {value[0]:6.2f} -DI {value[1]:6.2f} "
        f"score={score:5.2f} dt={value[2]}"
    )


def print_macd_mode(name: str, value, phone_dif: float, phone_macd: float):
    if value is None:
        print(f"  {name:<22} MACD n/a")
        return
    score = macd_score(value, phone_dif, phone_macd)
    print(
        f"  {name:<22} DIF {value[0]:7.2f} MACD9 {value[1]:7.2f} "
        f"score={score:5.2f} dt={value[2]}"
    )


def run_legacy_30m_mode(args):
    targets = parse_targets(args.targets)
    for ticker, phone_plus, phone_minus in targets:
        bars15 = trim_close_auction_tail(load_intraday(args.db, ticker, "15m", args.days))
        native30_raw = load_intraday(args.db, ticker, "30m", args.days)
        native30_trim = trim_close_auction_tail(native30_raw)
        derived_variants = build_30m_variants_from_15m(bars15, args.flat_volume_threshold)
        native30_drop_open = remove_daily_open_0900(native30_trim)
        native30_no_flat = remove_flat_bars(native30_trim)
        native30_no_flat_lowvol = remove_flat_bars(native30_trim, max_volume=args.flat_volume_threshold)
        gated_name, gated_bars, gated_stats = choose_30m_gated_variant(
            native30_trim,
            args.flat_volume_threshold,
            args.gate_window_bars,
        )

        print(f"\n## {ticker} phone +DI {phone_plus:.2f} / -DI {phone_minus:.2f}")
        print(
            "  "
            f"stats bars={len(native30_trim)} "
            f"flat={count_flat_bars(native30_trim)} "
            f"flat<={args.flat_volume_threshold}={count_flat_bars(native30_trim, args.flat_volume_threshold)}"
        )
        print(
            "  "
            f"gate_window={min(args.gate_window_bars, len(native30_trim))} "
            f"flat_share={gated_stats['flat_share']:.3f} "
            f"flat<={args.flat_volume_threshold}_share={gated_stats[f'flat_le_{args.flat_volume_threshold}_share']:.3f} "
            f"max_run={gated_stats['max_run']} "
            f"candidate={gated_name}"
        )
        print_dmi_mode("native30_trim", calc_wilder_dmi(native30_trim, args.length), phone_plus, phone_minus)
        print_dmi_mode("native30_keep", calc_wilder_dmi(native30_raw, args.length), phone_plus, phone_minus)
        for name, variant_bars in derived_variants.items():
            print_dmi_mode(name, calc_wilder_dmi(variant_bars, args.length), phone_plus, phone_minus)
        print_dmi_mode("native30_drop0900", calc_wilder_dmi(native30_drop_open, args.length), phone_plus, phone_minus)
        print_dmi_mode("native30_no_flat", calc_wilder_dmi(native30_no_flat, args.length), phone_plus, phone_minus)
        print_dmi_mode(
            f"native30_no_flat<={args.flat_volume_threshold}",
            calc_wilder_dmi(native30_no_flat_lowvol, args.length),
            phone_plus,
            phone_minus,
        )
        print_dmi_mode(
            f"native30_gate[{gated_name}]",
            calc_wilder_dmi(gated_bars, args.length),
            phone_plus,
            phone_minus,
        )
        print_dmi_mode(
            "native30_session_reset",
            calc_wilder_dmi(native30_trim, args.length, session_reset=True),
            phone_plus,
            phone_minus,
        )


def run_full_parity_mode(args):
    targets = parse_full_targets(args.targets_full)
    sweep_bars = parse_sweep_bars(args.sweep_bars)

    grouped = defaultdict(list)
    for target in targets:
        grouped[target["ticker"]].append(target)

    for ticker in sorted(grouped):
        print(f"\n## {ticker}")
        bars15_cache = trim_close_auction_tail(load_intraday(args.db, ticker, "15m", args.days))
        derived30_variants = build_30m_variants_from_15m(bars15_cache, args.flat_volume_threshold)
        for target in sorted(grouped[ticker], key=lambda item: item["timeframe"]):
            timeframe = target["timeframe"]
            bars_raw = load_intraday(args.db, ticker, timeframe, args.days)
            bars = trim_close_auction_tail(bars_raw)

            print(
                f"\n### {timeframe} phone "
                f"+DI {target['phone_plus']:.2f} / -DI {target['phone_minus']:.2f} "
                f"DIF {target['phone_dif']:.2f} / MACD9 {target['phone_macd']:.2f}"
            )
            print(
                f"  stats bars={len(bars)} "
                f"flat={count_flat_bars(bars)} "
                f"flat<={args.flat_volume_threshold}={count_flat_bars(bars, args.flat_volume_threshold)}"
            )

            print_dmi_mode("native_trim", calc_wilder_dmi(bars, args.length), target["phone_plus"], target["phone_minus"])
            print_macd_mode(
                "native_trim",
                calc_macd(bars, args.macd_fast, args.macd_slow, args.macd_signal),
                target["phone_dif"],
                target["phone_macd"],
            )

            if sweep_bars:
                print("  sweep:")
                for count in sweep_bars:
                    bars_n = take_last_bars(bars, count)
                    dmi_value = calc_wilder_dmi(bars_n, args.length)
                    macd_value = calc_macd(bars_n, args.macd_fast, args.macd_slow, args.macd_signal)
                    dmi_text = "n/a"
                    if dmi_value is not None:
                        dmi_text = (
                            f"+DI {dmi_value[0]:.2f} / -DI {dmi_value[1]:.2f} "
                            f"(score={dmi_score(dmi_value, target['phone_plus'], target['phone_minus']):.2f})"
                        )
                    macd_text = "n/a"
                    if macd_value is not None:
                        macd_text = (
                            f"DIF {macd_value[0]:.2f} / MACD9 {macd_value[1]:.2f} "
                            f"(score={macd_score(macd_value, target['phone_dif'], target['phone_macd']):.2f})"
                        )
                    print(f"    last-{count:<3} DMI {dmi_text}; MACD {macd_text}")

            if timeframe == "30m":
                gated_name, gated_bars, gated_stats = choose_30m_gated_variant(
                    bars,
                    args.flat_volume_threshold,
                    args.gate_window_bars,
                )
                print(
                    "  "
                    f"gate_window={min(args.gate_window_bars, len(bars))} "
                    f"flat_share={gated_stats['flat_share']:.3f} "
                    f"flat<={args.flat_volume_threshold}_share={gated_stats[f'flat_le_{args.flat_volume_threshold}_share']:.3f} "
                    f"max_run={gated_stats['max_run']} "
                    f"candidate={gated_name}"
                )
                print("  30m variants:")
                variant_modes = [
                    ("drop0900", remove_daily_open_0900(bars), False),
                    ("no_flat", remove_flat_bars(bars), False),
                    (f"no_flat<={args.flat_volume_threshold}", remove_flat_bars(bars, args.flat_volume_threshold), False),
                    ("session_reset", bars, True),
                ]
                for name, variant_bars, session_reset in variant_modes:
                    print_dmi_mode(
                        name,
                        calc_wilder_dmi(variant_bars, args.length, session_reset=session_reset),
                        target["phone_plus"],
                        target["phone_minus"],
                    )
                    print_macd_mode(
                        name,
                        calc_macd(variant_bars, args.macd_fast, args.macd_slow, args.macd_signal),
                        target["phone_dif"],
                        target["phone_macd"],
                    )
                print_dmi_mode(
                    f"gate[{gated_name}]",
                    calc_wilder_dmi(gated_bars, args.length),
                    target["phone_plus"],
                    target["phone_minus"],
                )
                print_macd_mode(
                    f"gate[{gated_name}]",
                    calc_macd(gated_bars, args.macd_fast, args.macd_slow, args.macd_signal),
                    target["phone_dif"],
                    target["phone_macd"],
                )
                print("  30m derived-from-15m variants:")
                for name, variant_bars in derived30_variants.items():
                    print_dmi_mode(
                        name,
                        calc_wilder_dmi(variant_bars, args.length),
                        target["phone_plus"],
                        target["phone_minus"],
                    )
                    print_macd_mode(
                        name,
                        calc_macd(variant_bars, args.macd_fast, args.macd_slow, args.macd_signal),
                        target["phone_dif"],
                        target["phone_macd"],
                    )


def main():
    args = parse_args()
    if args.targets_full:
        run_full_parity_mode(args)
        return
    if args.targets:
        run_legacy_30m_mode(args)
        return
    raise SystemExit("Provide either --targets or --targets-full")


if __name__ == "__main__":
    main()
