# Agent Context 2026-04-27

This handoff is intended to be sufficient for a same-workspace agent to resume
without re-discovering the project. Read this first, then inspect the named
project files directly.

## Read Order

1. `AGENT_CONTEXT_2026-04-25.md`
2. `AGENT_CONTEXT_2026-04-26K.md`
3. `backend_api.py`
4. `update_db_fubon.py`
5. `fubon_intraday_watcher.py`
6. `watch_fubon_update.py`
7. `market_watcher.py`
8. `debug_intraday_indicators.py`
9. `analyze_30m_parity.py`

`market_watcher.py` is now legacy Yahoo-path watcher context only. Do not use it
as the authoritative intraday automation path for the parity-sensitive build.

## Current Project Direction

The repo has now largely converged on the Fubon path for intraday data.

Current intended split:

- `backend_api.py`
  - FastAPI scanner and parity-sensitive scan logic
- `update_db_fubon.py`
  - manual / batch Fubon updater
- `fubon_intraday_watcher.py`
  - intraday auto-watch path discussed with the user on 2026-04-27
- `watch_fubon_update.py`
  - diagnostic observer for Fubon bar appearance / mutation timing
- `market_watcher.py`
  - old Yahoo-based watcher, not the preferred production direction now

## DMI / MACD Status

This is the latest honest status and should not be overstated:

- `update_db_fubon.py` is the intended reliable update path
- `MACD` is largely aligned and behaviorally stable
- `1326.TW 15m` false recent-cross regression was specifically guarded against
- `60m DMI` is the strongest aligned timeframe on validated samples
- `15m DMI` is much improved but not claimed as exact phone parity
- `30m DMI` was historically the hardest timeframe; the current API path uses a
  narrow `30m` flat-bar gate instead of a global DMI rewrite

Do not describe the state as "everything fully aligned". The more precise
statement is:

- main update path is usable
- MACD is mostly in place
- `60m DMI` is high-confidence
- `15m DMI` is close but not perfect
- `30m DMI` is controlled by a narrow scan-prep rule, not by a formula rewrite

## Final DMI / MACD Decisions Already Landed

### 1. No DMI core rewrite

Do not rewrite `calc_dmi_full_components()` / Wilder core math just to chase
remaining parity deltas.

Reason:

- `60m` exact/near-exact validated alignment is strong evidence the core DMI
  math, seeding, and smoothing path are fundamentally correct

### 2. `30m` parity handled at scan-prep layer

`backend_api.py` contains a `30m`-only gated flat-bar rule in
`_apply_30m_gated_flatbar_rule()` and applies it inside `_build_scan_frames()`.

This is intentionally scoped to:

- `30m`
- scan-prep only

It should not be generalized to:

- `1d`
- `15m`
- `60m`
- updater write path
- MACD math

### 3. `DMI tangle` rule simplified

`DMI tangle` is no longer using the old mean/level filter as a required logic
gate. The intended current behavior is:

- condition is based on four-line spread cap
- plus a start date
- start date must be `>= 2026-01-01`

Frontend was also simplified accordingly.

### 4. Daily tangle zero-hit regression was fixed

`AGENT_CONTEXT_2026-04-26K.md` documents the bug:

- daily `_dt` was timezone-naive
- tangle start date was timezone-aware
- comparison inside `strategy_dmi_tangle()` broke and each ticker got swallowed
  by scan-loop `try/except`

Fix already landed in `backend_api.py`.

### 5. Health endpoint exists for Render/UptimeRobot

`backend_api.py` now exposes:

- `GET /health`

Use that for uptime ping instead of fragile homepage probing.

## Important New Finding For Watcher Design

On 2026-04-27, we explicitly tested whether `15m`-derived higher timeframes
would drift from current native Fubon DB values.

Validated sample tickers:

- `1717.TW`
- `1785.TWO`
- `3518.TW`
- `1326.TW`

Result:

- native `30m DMI` == `15m -> 30m off0` derived DMI
- native `60m DMI` == `15m -> 60m off0` derived DMI
- `off15` bucket alignment did **not** match; bucket convention matters

Practical conclusion:

- for the new intraday watcher, `15m` can be the only native fetched timeframe
- `30m / 60m / 180m / 240m` can be synthesized from `15m`
- provisional intraday `1d` can also be synthesized from the same `15m`
  stream
- but higher-timeframe bars should only be written once they are finalized

This conclusion is specifically about:

- current repo
- current Fubon DB shape
- current resample convention (`off0`)

Do not overstate it as a universal proof that every future parity issue is
solved by resampling.

## Agreed Watcher Design

The user explicitly chose this design:

- do not involve purple scan automation for now
- do not rely on Yahoo watcher path
- do not auto-run end-of-day finalize yet
- accept provisional intraday daily synthesis, with later manual/formal update
  allowed to overwrite it

### Design Summary

Intraday automation should work like this:

1. Watcher observes the market using Fubon `15m` sentinel bars
2. When a new `15m` market token is detected:
   - fetch native `15m` candles
   - synthesize current-day provisional `1d` from the same `15m`
   - update DB `15m`
   - synthesize `30m / 60m / 180m / 240m` from `15m`
   - only keep finalized higher-timeframe buckets
   - reload API
3. Do **not** automatically run a post-close finalize/update pass yet
4. Preserve intraday-written data so later manual inspection remains possible
5. Post-close official/manual `update_db_fubon.py` remains a separate choice

Reasoning:

- this gives the user intraday freshness
- keeps parity-sensitive automation on the Fubon path
- allows `1d` to move intraday from the same source, while still treating it as
  overwriteable provisional data
- avoids immediately overwriting intraday observations with an automatic
  end-of-day finalize
- leaves room to study how intraday provisional behavior should evolve later

## Implemented Watcher

New file:

- `fubon_intraday_watcher.py`

Purpose:

- same-workspace Fubon intraday auto watcher
- sentinel-triggered
- fetches only native intraday `15m`
- builds a provisional current-day `1d` from the same `15m`
- synthesizes higher timeframes after finalization condition is met
- reloads API after each successful cycle

### Key Behavior

- sentinel symbols are a liquid TW mix copied from legacy watcher context
- watcher polls Fubon `15m` intraday candles for sentinels
- if enough sentinels share the latest market bar
  - default ready ratio: `0.60`
- and the resulting token differs from the last triggered token
  - watcher runs one intraday refresh cycle

Cycle details:

- write today's `15m` directly from Fubon intraday `15m`
- aggregate today's `15m` rows into a provisional `1d`
  - overwrite only today's daily row
- synthesize:
  - `30m`
  - `60m`
  - `180m`
  - `240m`
- higher-timeframe bar is only kept if the required last contributing `15m` bar
  is already present for that ticker

In practice that means:

- `1d` can move intraday and is intentionally provisional
- a `30m` bucket is not written until the underlying `15m` structure has reached
  the bucket's final contributing bar
- same idea for `60m / 180m / 240m`

Important runtime note discovered on 2026-04-27:

- using `historical.candles(... timeframe=15 ...)` for the live watcher caused
  mass `404 Resource Not Found` responses in the `from=non-trading-day -> today`
  window
- therefore the watcher was corrected to use live `intraday.candles(... 15m ...)`
  for today's bars instead of the historical route
- previous-day history for indicator continuity is expected to already exist in
  the DB from prior manual/formal updates; the watcher only needs to upsert
  today's bars

Second runtime note discovered the same day:

- the first watcher token design included sentinel bar signatures
  (`close/volume/...`) in the trigger token
- that made the watcher rerun for the same still-forming `15m` bar whenever the
  bar mutated intrabar
- effect: repeated full-market cycles, heavy reload pressure, API timeout risk

This was corrected so the token is now:

- latest shared sentinel `15m` timestamp only

Meaning:

- one full watcher cycle per newly observed `15m` bar timestamp
- no retrigger just because the same open bar's signature changed

### Files Related To This Watch Path

- `fubon_intraday_watcher.py`
  - actual watcher implementation
- `watch_fubon_update.py`
  - diagnostic Fubon observer, useful for timing experiments
- `start_watcher.cmd`
  - repointed to the new Fubon watcher so existing launcher habits do not
    accidentally start the old Yahoo path
- `start_fubon_intraday_watcher.cmd`
  - convenience launcher for the new watcher

### Legacy File That Should Not Be Used As The Main Path

- `market_watcher.py`

Reason:

- it is Yahoo-based
- it does not represent the current parity-sensitive data route

## Commands

### Start API

```powershell
python -m uvicorn backend_api:app --host 0.0.0.0 --port 8000 --reload
```

or use:

```powershell
start_api.cmd
```

### Start New Fubon Intraday Watcher

```powershell
python fubon_intraday_watcher.py --intraday-days 1 --poll-seconds 30 --poll-offhours-seconds 300 --reload-url http://127.0.0.1:8000/reload
```

or use:

```powershell
start_fubon_intraday_watcher.cmd
```

Existing launcher compatibility:

```powershell
start_watcher.cmd
```

`start_all.cmd` still calls `start_watcher.cmd`, so it now inherits the new
Fubon watcher path.

### One-Shot Watcher Poll For Debugging

```powershell
python fubon_intraday_watcher.py --once --intraday-days 1 --reload-url http://127.0.0.1:8000/reload
```

### Manual Reliable Update

This remains the manual, authoritative updater path:

```powershell
python update_db_fubon.py --tf intraday --intraday-days 1 --reload-url http://127.0.0.1:8000/reload
```

Optional full/manual follow-up remains available later, but the watcher itself
does not auto-trigger it.

If a formal close-time correction is needed later, use the manual Fubon updater
to overwrite the provisional day.

## What Was Verified In This Pass

Verified:

```powershell
python -m py_compile fubon_intraday_watcher.py
```

This passed.

`backend_api.py` had already separately passed:

```powershell
python -m py_compile backend_api.py
```

## Guardrails

When continuing from here, protect these:

- do not regress `1326.TW 15m` into a false recent DMI cross
- do not break validated `60m` alignment
- do not undo the current `/scan` steady-state speed gains
- do not mix Yahoo watcher writes into the parity-sensitive Fubon path
- do not auto-finalize post-close data inside the watcher without explicit user
  intent
- remember that watcher-written `1d` is provisional intraday daily, not the
  final official close-time row

## If A Future Agent Needs To Continue

Most likely next work items, in order:

1. runtime validation of `fubon_intraday_watcher.py` on a live session
2. observe whether finalized `30m/60m` synthesis timing feels right intraday
3. decide later whether a manual close-time finalize should become automated
4. only then revisit whether native `30m/60m` fetches are needed at all

If there is confusion about watcher behavior, inspect these in order:

1. `fubon_intraday_watcher.py`
2. `update_db_fubon.py`
3. `watch_fubon_update.py`
4. `backend_api.py`

This MD plus the listed files should be enough for a new agent to re-enter the
project state without starting from zero.
