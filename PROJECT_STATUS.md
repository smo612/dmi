# Project Status

Last updated: `2026-04-20`

## Current State

- Core frontend/API structure is still usable.
- Intraday DB data has been recovered for `2026-04-20`.
- Daily K data is still stale at `2026-04-17`.
- Watcher state is not fully trustworthy right now; use DB checks first.

## Data Source Status

- `intraday_candles`
  - Current practical source: Yahoo Finance
  - Current status: usable again for `15m / 30m / 60m / 180m / 240m`
  - Risk: Yahoo may expose `timestamp` before OHLCV becomes non-null

- `daily_candles`
  - Current source: Yahoo direct chart API first, `yfinance` fallback second
  - Current status: fallback code has been added on `2026-04-20`
  - Candidate backup source if Yahoo daily becomes unstable again: FinMind free daily API

- `purple_signals`
  - Depends on DB freshness
  - If `daily_candles` is stale, purple results may also remain stale or misleading

## What Is Confirmed

- `check_update_status.py` is the current source of truth.
- `watcher_state` can show `last_eod_date=today` even when `daily_candles` was not updated.
- `yahoo.py` should be judged by `last_bar`, not just `latest_ts`.
- Manual `update_db.py` does not automatically refresh the API memory cache.

## Recommended Current Strategy

1. Keep `intraday` on Yahoo for now.
2. Use the new direct Yahoo daily fallback before considering a source switch.
3. Stop trusting watcher success flags unless DB verification also passes.
4. Treat `daily` as a separate problem from `intraday`.
5. Consider FinMind for daily only if Yahoo daily still proves unstable.
6. Consider `TWSE` intraday self-built bars only if Yahoo intraday becomes unstable again.

## Daily Operations

- Check current status:
  - `python check_update_status.py --date 2026-04-20`

- Patch today intraday:
  - `python update_db.py --tf intraday --intraday-days 1`

- Patch today daily:
  - `python update_db.py --tf 1d --daily-days 1`

- Patch everything:
  - `python update_db.py --tf all --daily-days 1 --intraday-days 1`

- Rebuild purple after data refresh:
  - `python update_db.py --tf all --daily-days 1 --intraday-days 1 --purple`

- Refresh API memory after DB update:
  - open `http://127.0.0.1:8000/reload`
  - or restart API

## Immediate Next Tasks

- Verify the new `daily_candles` fallback in real update runs
  - `download_daily_batch()` now tries direct Yahoo `1d` first
  - If that still fails in practice, then switch daily to FinMind

- Tighten watcher success criteria
  - Only mark EOD success after DB daily rows really update to today
  - Only mark intraday success after DB latest intraday rows really reach today

- Update sentinel logic
  - Use `last non-null bar` logic like `yahoo.py`
  - Do not rely on `timestamp` alone

## Reference Files

- Main long-form handoff: [HANDOFF_V2.md](/c:/Users/jing5/Documents/2330dmi/HANDOFF_V2.md)
- Quick DB/state checker: [check_update_status.py](/c:/Users/jing5/Documents/2330dmi/check_update_status.py)
- Yahoo bar verifier: [yahoo.py](/c:/Users/jing5/Documents/2330dmi/yahoo.py)
- Watcher: [market_watcher.py](/c:/Users/jing5/Documents/2330dmi/market_watcher.py)
- Manual updater: [update_db.py](/c:/Users/jing5/Documents/2330dmi/update_db.py)
