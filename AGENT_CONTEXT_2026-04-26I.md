# Agent Context 2026-04-26I

This handoff continues from:

- `AGENT_CONTEXT_2026-04-25.md`
- `AGENT_CONTEXT_2026-04-26.md`
- `AGENT_CONTEXT_2026-04-26B.md`
- `AGENT_CONTEXT_2026-04-26C.md`
- `AGENT_CONTEXT_2026-04-26D.md`
- `AGENT_CONTEXT_2026-04-26E.md`
- `AGENT_CONTEXT_2026-04-26F.md`
- `AGENT_CONTEXT_2026-04-26G.md`
- `AGENT_CONTEXT_2026-04-26H.md`

## Scope Of This Pass

User asked for two things in the same round:

1. find why short-timeframe `DMI tangle` still looked effectively broken
2. simplify the frontend `tangle` UX:
   - remove the mean/level controls
   - keep only spread upper bound
   - use a date input like purple
   - only allow dates `>= 2026-01-01`
   - default min volume to `1000` for all searches

This pass stayed narrow:

- changed `backend_api.py`
- changed `scanner_cards.html`
- did **not** touch `update_db_fubon.py`
- did **not** touch DMI core math
- did **not** touch MACD logic
- did **not** touch the `30m` gate logic already added earlier

## Root Cause Update For Tangle

After the earlier stale API block was disabled, short-timeframe `tangle` was no
longer failing at the boundary, but it could still look "empty" for two real
reasons:

1. old `tangle` logic still required both:
   - `spread <= threshold`
   - `mean in [10, 25]`
2. the frontend still exposed / sent the mean-range controls, so users were
   implicitly scanning with that stricter broker-unrelated filter

For the concrete user example `2455.TW 60m`:

- phone screenshot corresponds to bars around `2026-04-24 10:00` / `11:00`
- local debug showed:
  - `2026-04-24 10:00` spread `1.89`, mean `41.49`
  - `2026-04-24 11:00` spread `1.66`, mean `38.26`

Implications:

- under the old `mean 10~25` rule, **both bars were always excluded**
- under the default `spread <= 1.5`, **both bars were still excluded**
- so the issue was not "missing data"; it was "filters were stricter than the
  intended scan definition"

Additional debug:

- from `2026-01-01` onward, `2455.TW 60m` has `0` bars with `spread <= 1.5`
- but it has `3` bars with `spread <= 2.0`:
  - `2026-03-30 10:00`
  - `2026-04-24 10:00`
  - `2026-04-24 11:00`

So after this pass, if the user wants `2455 60m` to appear, the next knob is
`spread`, not `mean`.

## Backend Change

Changed file:

- `backend_api.py`

### What changed

`strategy_dmi_tangle()` now uses:

- `spread_max`
- `start_date`

and no longer filters on:

- `mean_min`
- `mean_max`

New request / response field:

- `dmi_tangle_start_date`

Rules:

- default is `2026-01-01`
- backend validates `dmi_tangle_start_date >= 2026-01-01`
- backend rejects future dates

### Layer classification

This is a **scan rule / strategy filter** change, not:

- data cleaning
- timeframe aggregation
- smoothing
- DMI formula

### Safety

This should not affect:

- `1d`, `15m`, `30m`, `60m` DMI math
- MACD math
- updater behavior
- `30m` gated flat-bar preprocessing

It only changes how `tangle` decides whether a bar qualifies.

## Frontend Change

Changed file:

- `scanner_cards.html`

### What changed

For `DMI -> tangle`:

- added `dmi-tangle-start-date`
- min date is `2026-01-01`
- max date is today
- default value is `2026-01-01`
- hid the old mean/lock controls
- request body now sends:
  - `dmi_tangle_start_date`
  - `dmi_tangle_spread`
- request body no longer needs the mean values for actual behavior

For all searches:

- `min-volume` input default is now `1000`
- request fallback is also `1000`

### UI note

Because the file already contained mojibake / partially broken legacy strings,
this pass commented out two older broken `buildMetaText` / `buildIndicatorFooter`
blocks and left a clean later override in place.

Current comment marker pairs in `scanner_cards.html`:

- `/*` at ~1287 and `*/` at ~1319
- `/*` at ~1510 and `*/` at ~1588

Those ranges intentionally neutralize the old broken UI text helpers.

## Validation

Verified:

```powershell
python -m py_compile backend_api.py
```

This passed.

Not verified in this shell:

- frontend JS parse through Node (`node` not available in PATH here)
- real browser runtime after reload

## Expected Runtime Behavior After Reload

For `DMI -> tangle`:

- short timeframes should no longer be blocked at API layer
- scan should no longer depend on mean-range controls
- result set now depends on:
  - date floor
  - spread threshold
  - volume / turnover filters

For `2455.TW 60m` specifically:

- with default `spread = 1.5`, still expected to miss
- with `spread = 2.0`, expected to become eligible on recent bars

## Guardrails

This pass did **not** intentionally touch:

- `1326.TW 15m` recent-cross protection
- `60m` parity logic
- `/scan` caching / steady-state performance path

The frontend default `min_volume = 1000` can of course reduce hit counts by
design, but that is requested UX behavior rather than a regression.

## Next Check After Reload

1. `DMI -> 全糾結 -> 60m`
   - test `2455`
   - first with `spread 1.5`
   - then with `spread 2.0`
2. `DMI -> 全糾結 -> 15m / 30m`
   - confirm results are no longer empty because of old mean gating
3. confirm frontend:
   - mean controls are not visible
   - tangle uses date input
   - default min volume shows `1000`

