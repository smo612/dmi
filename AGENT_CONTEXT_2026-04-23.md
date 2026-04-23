# Agent Context 2026-04-23

This file is the current authoritative handoff for the repo state as of `2026-04-23`.

Old docs such as `PROJECT_STATUS.md` and `OPERATIONS_RUNBOOK.md` are now partially stale.
Use this file first.

## Project Purpose

This project is a Taiwan stock scanner with:

- backend API: `backend_api.py`
- main frontend: `scanner_cards.html`
- Yahoo updater: `update_db.py`
- market watcher: `market_watcher.py`
- Fubon probe utilities:
  - `fubon_probe.py`
  - `fubon_yahoo.py`
  - `watch_fubon_update.py`
- new Fubon updater:
  - `update_db_fubon.py`

DB file:

- `stock_data.db`

Main tables:

- `stocks`
- `daily_candles`
- `intraday_candles`
- `purple_signals`

## What Happened

Yahoo intraday data around `2026-04-20` was broken.
The user opened Fubon market-data access and we started building a Fubon path.

We verified:

- Fubon login works
- Fubon real-time / intraday candles work
- Fubon historical candles can return full bars for `2026-04-20`
- Fubon supports both TWSE and OTC examples we tested

## Important Reality Right Now

The repo is in a transition state.

Current intraday DB is not clean yet.
It contains a mix of:

- old Yahoo-derived rows
- partially refreshed Fubon-derived rows
- some placeholder / odd-tail rows from previous logic

This mixed state is the main reason intraday scan results looked wrong.

Examples of symptoms we saw:

- trigger times showing as `21:15`
- some 15m DMI hits looking like pre-cross instead of confirmed cross
- long timeframes looking inconsistent

## Root Causes Confirmed

### 1. Mixed intraday source data

`update_db_fubon.py` was run, but intraday refresh did not complete for all stocks.
So recent intraday rows are partially Yahoo and partially Fubon.

### 2. Mixed timestamp shape in DB

Old Yahoo path stored intraday timestamps in UTC-naive style such as:

- `2026-04-23 05:15:00`

Fubon path briefly stored Taipei-naive style such as:

- `2026-04-23 13:15:00`

Backend originally treated all intraday DB times as UTC, which turned some Fubon rows into fake evening times like:

- `2026-04-23 21:15`

### 3. Scan logic was excluding the latest intraday bar even after market close

This was intended to avoid using unfinished live bars during market hours.
But after close, the final bar should be included.

## What Has Already Been Changed

### Backend/API

`backend_api.py` has been modified to:

- normalize mixed intraday datetime styles when loading DB
- support live-only exclusion of the newest intraday bar for scans
- stop showing fake `21:15` trigger times
- ignore the active unfinished intraday tail during live scans

Important:

- this improves reading / scanning
- it does not magically clean the underlying mixed DB

### Fubon updater

`update_db_fubon.py` has been modified so new Fubon intraday rows are stored in UTC-naive style, matching the old DB convention.

That means future Fubon writes should no longer create a second timestamp shape.

### Frontend

`scanner_cards.html` has been re-locked to `1d only` for now.

Intraday buttons `15m / 30m / 60m / 180m / 240m` are intentionally disabled again because current intraday DB is not trustworthy yet.

## Current Safe Usage

Safe:

- daily scan
- daily purple scan
- general repo work
- Fubon probe scripts

Not safe right now:

- trusting intraday DMI scan results
- trusting intraday MACD scan results
- re-enabling intraday in frontend before DB cleanup / rebuild

## Immediate Recommended Next Step

Do not keep debugging scan conditions on top of the mixed intraday DB.

The next meaningful step is:

1. backup DB
2. delete recent intraday rows for a limited date range
3. rebuild recent intraday entirely from Fubon
4. reload API
5. verify with direct DB checks and `/scan`
6. only then re-enable frontend intraday

Suggested rebuild window:

- `2026-04-20` through `2026-04-23`

## Suggested Future Cleanup Plan

### Phase 1: data cleanup

- backup `stock_data.db`
- remove recent rows from `intraday_candles`
- rerun `update_db_fubon.py --tf intraday`

### Phase 2: verify

Check:

- latest 15m should align to `13:30`
- latest 30m should align to `13:30`
- latest 60m should align to `13:00`
- no fake evening trigger times
- scan hits should match chart reality better

### Phase 3: UI recovery

- re-enable intraday buttons only after DB is clean

## Commands Added During This Work

Probe current Fubon latest bars:

```powershell
python fubon_yahoo.py --symbol 2330 --intervals 15m,30m,60m
```

Watch Fubon update timing:

```powershell
python watch_fubon_update.py --symbol 2330 --intervals 15m,30m,60m --poll-seconds 30
```

Run Fubon updater for intraday only:

```powershell
python update_db_fubon.py --tf intraday --intraday-days 3 --request-gap-seconds 1
```

Run Fubon updater for daily and intraday:

```powershell
python update_db_fubon.py --tf all --daily-days 3 --intraday-days 3
```

## Important Git Notes

Sensitive / local files should not be committed.

`.gitignore` has been updated to ignore:

- `.env`
- `.tmp/`
- `.vendor/`
- local DB files
- local logs
- `fubun.txt`
- `idea.txt`
- extracted Fubon local package folders

Recommended safe add list for this work:

- `.gitignore`
- `backend_api.py`
- `scanner_cards.html`
- `fubon_probe.py`
- `fubon_yahoo.py`
- `watch_fubon_update.py`
- `update_db_fubon.py`

## Files Most Relevant To Continue

- `backend_api.py`
- `scanner_cards.html`
- `update_db_fubon.py`
- `fubon_probe.py`
- `market_watcher.py`
- `db.txt`
- `fubun.txt`
- `.gitignore`

## Short Status Summary

- Fubon access: working
- Fubon historical bars: confirmed usable
- new Fubon updater: created
- API scan logic: partially hardened
- intraday frontend: intentionally disabled again
- intraday DB: still mixed and not yet clean
- next real milestone: cleanly rebuild recent intraday from Fubon only
