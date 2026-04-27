# Agent Context 2026-04-27 Full

This is the current full handoff for the repo after the 2026-04-27 intraday
watcher work. A new same-workspace agent should read this first, then inspect
the named project files directly.

## Read Order

1. `AGENT_CONTEXT_2026-04-25.md`
2. `AGENT_CONTEXT_2026-04-26K.md`
3. `AGENT_CONTEXT_2026-04-27.md`
4. `backend_api.py`
5. `update_db.py`
6. `update_db_fubon.py`
7. `fubon_probe.py`
8. `fubon_intraday_watcher.py`
9. `watch_fubon_update.py`
10. `scanner_cards.html`

## Current Direction

The project has largely converged on:

- `backend_api.py`
  - FastAPI scanner
  - DMI / MACD scan logic
  - current `30m` gated flat-bar handling
- `update_db_fubon.py`
  - manual / formal Fubon updater path
- `fubon_intraday_watcher.py`
  - live intraday watcher path
- `fubon_probe.py`
  - Fubon API wrapper / symbol normalization

Legacy Yahoo watcher path is no longer the intended main automation path:

- `market_watcher.py`
- `watch_yahoo_update.py`
- `yahoo.py`

## DMI / MACD State

Do not overstate parity as "fully solved".

Current honest status:

- `60m DMI`
  - strongest validated timeframe
  - previously matched validated phone samples well
- `15m DMI`
  - much improved
  - not claimed as exact parity
- `30m DMI`
  - historically hardest timeframe
  - now handled in scan-prep by a narrow `30m` gated flat-bar rule
- `MACD`
  - largely stable
  - major behavioral regressions such as `1326.TW 15m` false recent cross were
    specifically protected against

Important principle:

- do not rewrite global DMI core math just to chase remaining parity deltas
- `60m` alignment is strong evidence that the main DMI math path is basically
  correct

## Existing Backend Changes That Matter

### 1. `30m` gated flat-bar rule

In `backend_api.py`:

- `_apply_30m_gated_flatbar_rule()`
- applied inside `_build_scan_frames()`

Scope is intentionally narrow:

- `30m` only
- scan-prep only

Do not casually generalize this to:

- updater write path
- `15m`
- `60m`
- MACD

### 2. DMI tangle simplification

Current intended tangle behavior:

- driven by spread cap
- plus start date
- start date must be `>= 2026-01-01`

The old mean/level UI requirement was intentionally removed from the main user
path.

### 3. Daily tangle timezone bug already fixed

Earlier `1d tangle = 0` issue came from:

- naive daily `_dt`
- aware Taipei start date

This was already fixed before the latest watcher work.

### 4. Render / UptimeRobot health endpoint exists

`backend_api.py` now has:

- `GET /health`

Use this for monitoring instead of hitting a random frontend route.

## Watcher Finalized Design

The current agreed watcher design is:

- use Fubon, not Yahoo
- live path is based on `15m`
- watcher writes today's intraday state
- no automatic close-time finalize pass for now
- manual / formal post-close correction can still be done via
  `update_db_fubon.py`

### What the watcher does

`fubon_intraday_watcher.py` currently:

1. polls Fubon sentinel symbols on `15m`
2. waits until enough sentinels share the newest market timestamp
3. when a new shared `15m` timestamp appears:
   - fetches native Fubon `intraday 15m` data for all watched symbols
   - filters to today's bars
   - upserts today's `15m`
   - synthesizes provisional current-day `1d`
   - synthesizes finalized `30m / 60m / 180m / 240m` from `15m`
   - calls API reload

### Important design clarifications

- watcher `1d` is provisional intraday daily, not final close-time daily
- watcher `30m/60m/180m/240m` come from `15m`
- finalized high-timeframe bars are only written when enough `15m` bars exist
- later manual/formal update is still allowed to overwrite today's provisional
  rows

## Runtime Issues Found During Watcher Bring-up

These were discovered on 2026-04-27 and already changed in code.

### A. Historical `15m` route was wrong for live watcher

Problem:

- watcher originally called Fubon `historical.candles(... timeframe=15 ...)`
- on live same-day windows this produced mass `404 Resource Not Found`

Fix:

- watcher now uses live `intraday.candles(... 15m ...)`
- then filters rows down to today's local date

Relevant file:

- `fubon_intraday_watcher.py`

### B. `.TWO` symbol normalization bug

Problem:

- `3317.TWO` became `3317O`
- caused `404 Resource Not Found` on TPEX tickers

Fix:

- `fubon_probe.py`
- `normalize_symbol()` now:
  - strips `.TWO` first
  - strips `.TW` second

### C. Watcher retriggered repeatedly on the same open `15m` bar

Problem:

- trigger token originally included bar signature
- close / volume changes inside the same still-forming `15m` bar retriggered a
  full-market cycle
- this was too heavy and helped create reload pressure

Fix:

- watcher trigger token is now only the newest shared sentinel timestamp

Effect:

- one watcher cycle per newly observed `15m` timestamp
- no retrigger just because the same open bar mutates

### D. Reload timeout too short

Problem:

- watcher could write DB successfully
- but `/reload` timed out at `30s`
- API would not necessarily reflect the new DB state immediately

Fix:

- `update_db.py`
- `notify_api_reload()` timeout changed from plain `30s` to:
  - connect timeout `5s`
  - read timeout `180s`

### E. Request gap tuned faster but still conservative

Change:

- watcher default `--request-gap-seconds` changed from `0.25` to `0.15`
- launcher scripts were updated to use `0.15`

Relevant files:

- `fubon_intraday_watcher.py`
- `start_watcher.cmd`
- `start_fubon_intraday_watcher.cmd`

### F. Liquidity gate vs provisional daily

Problem:

- watcher writes provisional current-day `1d`
- scan liquidity filters (`min_volume`, `min_turnover`) were using the latest
  daily row
- during live session this can wrongly treat today's still-forming daily candle
  as the liquidity gate source
- effect: front-end scans could collapse toward `0` hits

Current fix direction already landed in code:

- during live intraday session, effective liquidity maps should prefer the
  previous completed daily row instead of today's provisional row

Relevant file:

- `backend_api.py`

Important note:

- this area changed late in the session and should be runtime-verified after
  restart

### G. `Date` vs `_dt` startup crash

Problem:

- `load_all_data()` preloads daily rows as `_dt`
- late-session liquidity helper mistakenly assumed a `Date` column
- API startup crashed with `KeyError: 'Date'`

Fix:

- liquidity helper now supports:
  - `_dt`
  - fallback to `Date`

Relevant file:

- `backend_api.py`

This was syntax-checked, but still needs live startup verification after restart.

## Operational State At Time Of This Handoff

By the end of this pass:

- watcher could successfully write DB rows again
- watcher was no longer stuck on the old Fubon historical path
- watcher no longer had the `.TWO` symbol bug
- watcher no longer retriggered from same-bar signature churn

However, there was still active live-session pressure around:

- API restart / reload behavior
- verifying the latest `backend_api.py` liquidity-gate fixes in runtime

So the most honest current state is:

- watcher path is much closer to usable
- backend startup and scan hit behavior after the latest liquidity-gate fix
  must still be confirmed by actually restarting API and then watcher

## Restart Sequence

If the next agent is resuming during live usage, safest order is:

1. stop current API window
2. stop current watcher window
3. start API
4. confirm API startup succeeds
5. start watcher
6. then verify scan hits are no longer collapsing to zero

Commands:

```powershell
start_api.cmd
```

then:

```powershell
start_watcher.cmd
```

Direct watcher command:

```powershell
python fubon_intraday_watcher.py --intraday-days 1 --poll-seconds 30 --poll-offhours-seconds 300 --request-gap-seconds 0.15 --reload-url http://127.0.0.1:8000/reload
```

## Backup And Cleanup Done In This Pass

Created backup snapshot folder:

- `backup/project_snapshot_20260427_130319`

This is a conservative project-file snapshot intended as a quick rollback source
for repo files. Large runtime DB / cache / log folders were not fully duplicated
there.

Created cleanup bin:

- `backup/cleanup_bin_20260427_130319`

Moved there:

- `$null`
- `.tmp_scanner_prev.html`
- `WATCH.txt`
- `fubun.txt`
- `render log.txt`

Files that were left in place because they were in use / locked at the time:

- `market_watcher.lock`
- `fubon_intraday_watcher.lock`

## Guardrails

Do not casually break these:

- `1326.TW 15m` must not regress into false recent DMI cross
- validated `60m` alignment must not be casually broken
- `30m` flat-bar gate is narrow and intentional; do not expand it blindly
- watcher should stay on the Fubon path, not revert to Yahoo watcher behavior
- reload and scan responsiveness matter, but avoid "speed fixes" that destroy
  scan correctness

## Most Likely Next Checks

1. restart API and confirm startup succeeds after latest `backend_api.py` fix
2. confirm `/health` responds
3. restart watcher
4. confirm watcher writes DB and reload no longer times out as badly
5. confirm frontend scan hit counts are no longer incorrectly zero during live
   session

This file plus the listed project files should be enough for the next agent to
re-enter the current state without starting from zero.
