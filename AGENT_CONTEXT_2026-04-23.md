# Agent Context 2026-04-23

This is the current handoff document for the repo state as of `2026-04-23`.
Use this before older docs like `PROJECT_STATUS.md` or `OPERATIONS_RUNBOOK.md`.

## Project Purpose

This repo is a Taiwan stock scanner / dashboard with:

- backend API: `backend_api.py`
- main frontend: `scanner_cards.html`
- legacy Yahoo updater: `update_db.py`
- legacy watcher: `market_watcher.py`
- Fubon helper scripts:
  - `fubon_probe.py`
  - `fubon_yahoo.py`
  - `watch_fubon_update.py`
- Fubon updater:
  - `update_db_fubon.py`

Main DB:

- `stock_data.db`

Main tables:

- `stocks`
- `daily_candles`
- `intraday_candles`
- `purple_signals`

## High-Level History

Yahoo intraday data broke around `2026-04-20`.
We opened the Fubon path and migrated recent intraday rebuilding to Fubon.

Confirmed:

- Fubon login works
- Fubon historical candles work
- Fubon latest intraday candles work
- Fubon can return complete historical bars for the broken Yahoo dates we tested

## Current Reality

The repo is now past the original Yahoo/Fubon mixed-time bug, but it is not fully "phone-app matched" yet.

What is already much better now:

- fake evening trigger times like `21:15` are fixed
- intraday frontend is re-enabled again
- recent intraday data was cleared and rebuilt from Fubon
- close / latest timestamps now mostly align with market reality

What is still not fundamentally solved:

- some `DMI 確認金叉` hits still look like `準備金叉`
- some `DMI 準備金叉` hits are already crossed on the phone app
- phone DMI values and frontend DMI values can differ, especially near the crossing boundary

## What Was Actually Confirmed

### 1. The raw candle source is not the main problem anymore

We directly compared multiple symbols with the phone app.

For many samples:

- close price matched
- direction of DMI broadly matched
- only the exact indicator values or cross/ready classification differed

This means the problem is no longer the old "Yahoo bars are obviously broken" class of bug.

### 2. Current 30m / 60m are still derived from 15m

`update_db_fubon.py` currently downloads:

- native `1d`
- native `15m`

Then it resamples from `15m` into:

- `30m`
- `60m`
- `180m`
- `240m`

This is inherited from `update_db.py` logic.

Important consequence:

- even with clean 15m bars, higher timeframes can still diverge from the broker app
- the final bucket handling matters a lot

### 3. Fubon's close-auction flat bar distorts indicators

Many symbols have a final flat bar at local `13:30`:

- `15m` often has a final flat `13:30` bar
- derived `30m` also ended up with a final flat `13:30` bar

Examples we explicitly inspected in DB:

- `3518.TW` `15m`
- `6015.TWO` `15m`
- `1717.TW` `30m`
- `2915.TW` `30m`
- `1785.TWO` `30m`
- `1718.TW` `30m`

This final flat close-auction bar is useful for displaying quotes, but it can flip DMI / MACD classification for edge-case symbols.

### 4. DMI value mismatch is still partly formula / timeframe construction

We validated phone vs frontend on sample symbols.

Examples:

- `3518 15m`
  - frontend: `43.88 / 37.38`
  - phone: `43.05 / 36.94`
  - same direction, small numerical drift
- `1785 30m`
  - frontend: `37.88 / 37.93`
  - phone: `38.00 / 36.48`
  - same price, but classification flips

This strongly suggests:

- not only raw K bars matter
- DMI smoothing / seed / timeframe construction also matters
- near-cross symbols are especially sensitive

### 5. MACD is much closer than DMI

We also compared MACD samples.

Examples:

- `2486 15m`
  - frontend: `macd 1.9075 / signal 1.6537`
  - phone: `DIF 1.79 / MACD9 1.84`
- `1721 30m`
  - frontend: `1.0154 / 0.9069`
  - phone: `1.05 / 1.00`

This means:

- raw prices are broadly usable
- DMI is the more fragile indicator right now

## Changes Already Made

### Backend / API

`backend_api.py` has already been changed to:

- normalize mixed intraday timestamp styles when loading DB
- avoid fake evening trigger times
- keep live provisional bars out of scans during market hours
- split DMI into:
  - `確認金叉`
  - `準備金叉`
  - `全糾結`
- use better display snapshots for post-close cards
- distinguish `日量 / 成交值` vs `K量 / K值`

Most recent change in this handoff:

- strategy evaluation now excludes the final flat `13:30` close-auction bar for all intraday timeframes
- this exclusion is for indicator scans only
- the bar still stays in DB and can still be used for display

This latest change was made because the repeated user examples showed that edge-case DMI classification was often being skewed by the final flat auction bar.

### Frontend

`scanner_cards.html` now supports:

- DMI mode buttons:
  - `確認金叉`
  - `準備金叉`
  - `全糾結`
- card labels that switch between:
  - `日量 / 成交值`
  - `K量 / K值`

### Fubon updater

`update_db_fubon.py` exists and is usable.

It was already adjusted so Fubon intraday rows are stored in the UTC-naive DB shape compatible with the old schema.

## What Is Still Not Fully Solved

### 1. Higher timeframe construction is still not native

Current architecture still synthesizes:

- `30m`
- `60m`
- `180m`
- `240m`

from smaller bars instead of using native Fubon historical `30` / `60`.

This is the main reason we cannot yet promise "frontend numbers will match the phone app exactly".

### 2. DMI formula parity with the phone app is not guaranteed

Even when close prices match, DMI values can still drift due to:

- smoothing implementation
- initial seed / warmup depth
- whether the broker app uses native 30m bars vs our derived 30m bars

### 3. Rescanning right now is not the first fix

At this stage, simply rescanning with the same current construction logic is not the best next move.

Reason:

- the main remaining mismatch is not obvious raw-data corruption
- it is strategy input shaping and timeframe construction

## Current Best Understanding

If the user asks "do we need to rescan now?", the best answer is:

- for the latest DMI classification bug: **no, not first**
- we already have enough evidence to fix the scan-side handling first

If the user asks "can this fully match the phone app now?", the best answer is:

- **not yet**
- to get much closer, the next real architectural step is native Fubon `15m / 30m / 60m`

## Recommended Next Technical Steps

### Step 1: validate the latest scan-only fix

After the newest `backend_api.py` change, validate repeated problem names again:

- `3518`
- `6015`
- `1717`
- `2915`
- `1785`
- `1718`

Check:

- `確認金叉` no longer contains obvious not-yet-crossed symbols
- `準備金叉` no longer contains obvious already-crossed symbols

### Step 2: if the user still wants phone-level parity, upgrade timeframe sourcing

Most important future refactor:

- fetch native Fubon `15m`
- fetch native Fubon `30m`
- fetch native Fubon `60m`
- only synthesize `180m / 240m` if still needed, and only from completed native base bars

This is the most likely path to real parity.

### Step 3: only then reconsider broader rescan

If timeframe sourcing logic changes, then rescanning recent intraday becomes meaningful again.

At that point:

- rebuild recent `intraday_candles`
- reload API
- re-check samples against the phone app

## Commands We Used During This Phase

Probe Fubon latest bars:

```powershell
python fubon_yahoo.py --symbol 2330 --intervals 15m,30m,60m
```

Watch Fubon update timing:

```powershell
python watch_fubon_update.py --symbol 2330 --intervals 15m,30m,60m --poll-seconds 30
```

Run Fubon intraday rebuild:

```powershell
python update_db_fubon.py --tf intraday --intraday-days 20 --request-gap-seconds 1
```

Run Fubon daily + intraday:

```powershell
python update_db_fubon.py --tf all --daily-days 3 --intraday-days 3
```

## Git / Local Safety Notes

Do not blindly `git add .`

There are local / sensitive files in the workspace.

`.gitignore` has already been updated to ignore at least:

- `.env`
- `.env.*`
- `.tmp/`
- `.vendor/`
- local DB files and WAL files
- local logs
- `fubun.txt`
- `idea.txt`
- unpacked local Fubon package folders

## Files Most Relevant For The Next Agent

- `backend_api.py`
- `scanner_cards.html`
- `update_db_fubon.py`
- `fubon_probe.py`
- `market_watcher.py`
- `db.txt`
- `fubun.txt`
- `.gitignore`

## Short Status Summary

- Yahoo was the original failure source
- Fubon raw data path is now the main source and is broadly usable
- mixed timestamp bug was fixed
- intraday frontend is enabled again
- remaining major issue is not raw candle corruption
- remaining major issue is:
  - close-auction tail handling
  - non-native higher timeframe construction
  - DMI parity with the phone app
- latest code change in this handoff:
  - exclude the final flat `13:30` intraday bar from strategy evaluation
  - keep it in DB for display
