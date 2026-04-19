import argparse
import json
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

YF_CACHE_DIR = ".yfinance_cache"


def configure_yfinance_cache():
    os.makedirs(YF_CACHE_DIR, exist_ok=True)
    try:
        yf.set_tz_cache_location(os.path.abspath(YF_CACHE_DIR))
    except Exception as e:
        print(f"⚠️ yfinance cache 設定失敗：{e}")


def fetch_twse_stocks() -> pd.DataFrame:
    """抓上市普通股名單。"""
    try:
        resp = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=15)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json())[["公司代號", "公司簡稱"]]
        df.columns = ["code", "name"]
        df["market"] = "TW"
        return df[df["code"].str.match(r"^\d{4}$")]
    except Exception as e:
        print(f"❌ 上市名單抓取失敗：{e}")
        return pd.DataFrame(columns=["code", "name", "market"])


def fetch_tpex_stocks() -> pd.DataFrame:
    """抓上櫃普通股名單。"""
    try:
        resp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=15)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json())[["SecuritiesCompanyCode", "CompanyName"]]
        df.columns = ["code", "name"]
        df["market"] = "TWO"
        return df[df["code"].str.match(r"^\d{4}$")]
    except Exception as e:
        print(f"❌ 上櫃名單抓取失敗：{e}")
        return pd.DataFrame(columns=["code", "name", "market"])


def get_all_stocks() -> list[dict]:
    """使用公開 API 組出全市場股票清單。"""
    stocks = pd.concat([fetch_twse_stocks(), fetch_tpex_stocks()], ignore_index=True)
    stocks["yf_ticker"] = stocks["code"] + "." + stocks["market"]
    return stocks[["code", "name", "yf_ticker"]].to_dict("records")


def check_purple_signal(df: pd.DataFrame, lookback_days: int = 7) -> list[str]:
    """
    紫圈共振掃描（沿用原 scanner.py 邏輯）。
    只回傳最近 lookback_days 天內的紫圈時間。
    """
    if len(df) < 216:
        return []

    df = df.copy()
    df["avgShort"] = df["Close"].rolling(window=36).mean()
    df["avgLong"] = df["Close"].rolling(window=216).mean()

    df["vma"] = df["Volume"].rolling(window=20).mean()
    is_volume_spike = df["Volume"] > (df["vma"] * 2.5)

    is_near_short = (df["Close"] - df["avgShort"]).abs() / df["avgShort"] < 0.002
    is_near_long = (df["Close"] - df["avgLong"]).abs() / df["avgLong"] < 0.002
    should_plot_spike = is_volume_spike & (is_near_short | is_near_long)

    df["lastSpikePrice"] = np.nan
    df.loc[should_plot_spike, "lastSpikePrice"] = df["Close"]
    df["lastSpikePrice"] = df["lastSpikePrice"].ffill()

    gold_cross = (df["avgShort"] > df["avgLong"]) & (df["avgShort"].shift(1) <= df["avgLong"].shift(1))
    is_price_above_yellow_x = df["lastSpikePrice"].notna() & (df["Close"] > df["lastSpikePrice"])
    is_trend_up = df["avgShort"] > df["avgLong"]

    df["isPurpleO"] = (gold_cross & is_price_above_yellow_x & is_trend_up).fillna(False)

    purple_times = df[df["isPurpleO"]].index
    if getattr(purple_times, "tz", None) is not None:
        purple_times = purple_times.tz_localize(None)

    cutoff = datetime.now() - timedelta(days=lookback_days)
    recent_purples = purple_times[purple_times > cutoff]

    # 沿用原本尾盤假訊號過濾
    valid_times = []
    if not df.empty:
        last_candle_time = df.index[-1]
        if getattr(last_candle_time, "tzinfo", None) is not None:
            last_candle_time = last_candle_time.tz_localize(None)

        for t in recent_purples:
            if t == last_candle_time and t.hour == 13:
                continue
            valid_times.append(t.strftime("%Y-%m-%d %H:%M"))

    return valid_times


def scan_market(
    period: str = "1y",
    interval: str = "1h",
    lookback_days: int = 7,
    sleep_sec: float = 0.05,
    limit: int | None = None,
) -> list[dict]:
    stocks = get_all_stocks()
    if limit:
        stocks = stocks[:limit]

    print(f"✅ 名單獲取成功！開始使用 yfinance 掃描全市場 {len(stocks)} 檔股票...\n")
    print("-" * 50)

    all_signals = []

    for i, stock in enumerate(stocks):
        print(
            f"⏳ 掃描進度: {i + 1} / {len(stocks)} | 正在檢查: {stock['code']} {stock['name']} ...",
            end="\r",
        )

        try:
            df = yf.Ticker(stock["yf_ticker"]).history(period=period, interval=interval, auto_adjust=True)
            if not df.empty:
                trigger_times = check_purple_signal(df, lookback_days=lookback_days)
                if trigger_times:
                    print(" " * 100, end="\r")
                    time_str = ", ".join(trigger_times)
                    print(f"🎯 發現紫圈！ {stock['code']} {stock['name']} -> {time_str}")
                    for t in trigger_times:
                        all_signals.append({
                            "代碼": stock["code"],
                            "股名": stock["name"],
                            "時間": t,
                        })
        except Exception:
            pass

        time.sleep(sleep_sec)

    print(" " * 100, end="\r")
    print("-" * 50)
    return sorted(all_signals, key=lambda x: x["時間"], reverse=True)


def main():
    parser = argparse.ArgumentParser(description="台股紫圈共振掃描器（無券商 API 版）")
    parser.add_argument("--period", default="1y", help="yfinance period，預設 1y")
    parser.add_argument("--interval", default="1h", help="yfinance interval，預設 1h")
    parser.add_argument("--lookback-days", type=int, default=7, help="只輸出最近幾天內的紫圈，預設 7")
    parser.add_argument("--sleep", type=float, default=0.05, help="每檔股票之間等待秒數，預設 0.05")
    parser.add_argument("--limit", type=int, default=None, help="只掃前 N 檔，方便測試")
    parser.add_argument("--output", default="purple_signals.json", help="輸出檔名，預設 purple_signals.json")
    args = parser.parse_args()

    configure_yfinance_cache()
    print("🔄 使用公開 API 抓取台股上市/上櫃普通股名單...")
    signals = scan_market(
        period=args.period,
        interval=args.interval,
        lookback_days=args.lookback_days,
        sleep_sec=args.sleep,
        limit=args.limit,
    )

    if signals:
        print(f"✅ 掃描完畢！過去 {args.lookback_days} 天內共找到 {len(signals)} 次紫圈共振。\n")
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(signals, f, ensure_ascii=False, indent=4)
        print(f"📁 名單已儲存至 {args.output}")
    else:
        print(f"✅ 掃描完畢！過去 {args.lookback_days} 天內全市場無紫圈。")


if __name__ == "__main__":
    main()
