# Operations Runbook

Last updated: `2026-04-21`

## Purpose

This file is the short practical runbook for daily use.

Use this file for:
- what to run before market
- what each terminal does
- how to manually patch data
- how to verify DB freshness

Do not use this file as full history.
Long-form history stays in `HANDOFF_V2.md`.

## Current Data Strategy

- Stock universe:
  - `TWSE OpenAPI`
  - `TPEX OpenAPI`

- Intraday K:
  - main source = Yahoo direct chart API
  - native-first:
    - `15m` direct
    - `30m` direct
    - `60m` direct
  - `180m / 240m` from `60m` resample
  - watcher now uses Yahoo `last non-null bar` as the intraday trigger

- Daily K:
  - main source = Yahoo direct `1d` path in `update_db.py`
  - fallback = `yfinance`
  - if this becomes unstable again, next candidate is `FinMind`

- Purple:
  - depends on DB freshness
  - if DB is stale, purple is not trustworthy

## Source Of Truth

Trust order:

1. `check_update_status.py`
2. direct DB contents
3. API after `/reload`
4. watcher log
5. watcher state

Important:

- `yahoo.py` is useful for intraday readiness checks.
- `watch_yahoo_update.py` is useful after close if you want to observe when Yahoo reaches `FULL`.
- `update_db.py` now auto-calls API reload after it finishes unless you pass `--reload-url ""`.
- For daily K, final truth is still DB plus `check_update_status.py`.

## Terminal Setup

## Terminal 1: API

Run:

```bash
uvicorn backend_api:app --host 0.0.0.0 --port 8000 --reload
```

Shortcut:

```bat
start_api.cmd
```

Purpose:
- serves frontend data
- keeps DB data in memory

Important:
- watcher auto-calls `/reload` after verified writes
- `update_db.py` now also auto-calls `/reload` after finish
- only use manual `/reload` if you want an extra refresh

## Terminal 2: ngrok

Only needed if outside users need access.

Run your usual ngrok command.

Shortcut:

```bat
start_ngrok.cmd
```

Purpose:
- expose local API to public URL

## Terminal 3: Watcher

Run:

```bash
python market_watcher.py
```

Shortcut:

```bat
start_watcher.cmd
```

Purpose:
- during market hours, poll Yahoo `15m` last non-null bar
- when enough sentinels have the same bar, pull native `15m / 30m / 60m`
- only `180m / 240m` are resampled from `60m`
- write DB
- verify DB actually updated
- call API reload URL after verified writes
- after market, wait for Yahoo final labels to become `FULL`, then run EOD refresh

Current caution:
- watcher now follows the more accurate Yahoo `last_bar / final label` logic
- still trust `check_update_status.py` over state if anything looks odd

## Daily Workflow

## Before Market

Open:

1. API
2. ngrok if needed
3. watcher

Fastest option:

```bat
start_all.cmd
```

Current `start_all.cmd` layout:

- top-left: API
- top-right: ngrok
- bottom-left: `purple` shell
- bottom-right: watcher

Optional quick check:

```bash
python check_update_status.py --date 2026-04-21
```

## During Market

Watcher should be the main automatic updater.

If you want to manually check whether Yahoo intraday bars are really available:

```bash
python yahoo.py --date 2026-04-21
```

How to interpret:

- `latest_ts` alone is not enough
- `last_bar` is the usable K bar
- `bar_today=True` means today's data exists
- `watch_yahoo_update.py` `FULL` means that interval reached Yahoo's final label

If needed, verify DB directly:

```bash
python check_update_status.py --date 2026-04-21
```

## After Market

Recommended final patch:

```bash
python update_db.py --tf all --daily-days 1 --intraday-days 1 --purple
```

Then verify:

```bash
python check_update_status.py --date 2026-04-21
```

## Manual Commands

## Check Yahoo Intraday Readiness

```bash
python yahoo.py --date 2026-04-21
```

Use this mainly for intraday bars.

## Watch Yahoo Until FULL

```bash
python watch_yahoo_update.py --date 2026-04-21 --poll-seconds 30 --timeout-minutes 180
```

Use this when you want to know when Yahoo has fully completed:
- `15m final label = 13:15`
- `30m final label = 13:00`
- `60m final label = 13:00`
- `1d final label = 09:00`

## Check DB / State / Log Status

```bash
python check_update_status.py --date 2026-04-21
```

## Patch Intraday Only

```bash
python update_db.py --tf intraday --intraday-days 1
```

This now auto-reloads API at the end.

## Patch Daily Only

```bash
python update_db.py --tf 1d --daily-days 1
```

This now auto-reloads API at the end.

## Patch Everything

```bash
python update_db.py --tf all --daily-days 1 --intraday-days 1
```

This now auto-reloads API at the end.

## Rebuild Purple

```bash
python update_db.py --tf all --daily-days 1 --intraday-days 1 --purple
```

This now auto-reloads API at the end.

## Reload API

Browser:

```text
http://127.0.0.1:8000/reload
```

Or PowerShell:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/reload
```

Shortcut:

```bat
reload_api.cmd
```

Usually not needed after `update_db.py` now.

## Extra Working Shell

If you only want a project shell with:
- project folder already selected
- `conda activate purple` already done

use:

```bat
start_shell.cmd
```

## What Counts As Success

For intraday:

- `check_update_status.py` should show:
  - `intraday_core_ok=True`
  - latest `15m / 30m / 60m` on target date

For daily:

- `check_update_status.py` should show:
  - `daily_ok=True`
  - `daily_candles.latest = target date`

For full success:

- `intraday_core_ok=True`
- `daily_ok=True`
- API reloaded
- purple rerun if needed

## What To Ignore For Now

- stale watcher state by itself
- old `last_intraday_bar_key` by itself
- `yahoo.py` daily check as final daily truth

Always confirm with:

```bash
python check_update_status.py --date YYYY-MM-DD
```

## If Something Looks Wrong

If intraday looks stale:

1. run `python yahoo.py --date YYYY-MM-DD`
2. if intraday `bar_today=True`, run manual intraday update
3. run `check_update_status.py`

If daily looks stale:

1. run `python update_db.py --tf 1d --daily-days 1`
2. run `python check_update_status.py --date YYYY-MM-DD`
3. if still stale, inspect Yahoo daily path / switch daily fallback plan

If website still shows old data after DB update:

1. call `/reload`
2. refresh frontend page
3. if still stale, restart API

## Related Files

- `PROJECT_STATUS.md`
- `HANDOFF_V2.md`
- `market_watcher.py`
- `update_db.py`
- `check_update_status.py`
- `yahoo.py`
