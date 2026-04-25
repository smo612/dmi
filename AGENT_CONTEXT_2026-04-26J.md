# Agent Context 2026-04-26J

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
- `AGENT_CONTEXT_2026-04-26I.md`

## Focus Of This Pass

User reported a regression:

- previously `1d DMI tangle` had hits
- short intraday `tangle` was empty
- after recent changes, `1d tangle` now looked like `0` too

The immediate priority was to restore confidence in `tangle`, not to continue
expanding other scan work.

## Most Important Finding

The backend `1d tangle` logic is **not actually zero** on the current local DB.

Using an independent local verification script against `daily_candles` with:

- start date `2026-01-01`
- spread threshold `<= 1.5`
- current Wilder DMI/ADX/ADXR calculation

results were:

- `min_volume = 0` -> `117` hits
- `min_volume = 1000` -> `117` hits

That means:

- the `1d tangle` collapse to zero is **not** explained by the new backend
  `start_date`
- it is **not** explained by the new frontend default `min_volume = 1000`

So the user's observed "daily = 0" issue was most likely coming from the
frontend state / file condition, not the backend `tangle` scan logic itself.

## Concrete 2455 Check

For `2455.TW 60m`, local verification showed:

- `2026-04-24 10:00` spread `1.89`, mean `41.49`
- `2026-04-24 11:00` spread `1.66`, mean `38.26`

This confirms:

- phone screenshot was not a missing-data issue
- the bar is excluded mainly because `spread <= 1.5` is too strict
- removing mean filtering was still correct

Additional check:

- from `2026-01-01` onward, `2455.TW 60m` has:
  - `0` matches with `spread <= 1.5`
  - `3` matches with `spread <= 2.0`

## What Was Wrong In Frontend

`scanner_cards.html` had become polluted by multiple generations of:

- old commented-out `buildMetaText`
- old commented-out `buildIndicatorFooter`
- duplicated helper variants left behind from the previous repair pass

That created a high-risk frontend state where:

- the file was much harder to reason about
- old tangle UI logic and new tangle UI logic were mixed together
- it was too easy for runtime behavior to diverge from what the backend was
  actually doing

## What Changed In This Pass

Changed file:

- `scanner_cards.html`

### Frontend cleanup

Instead of trying to surgically keep patching the messy file, this pass first
restored `scanner_cards.html` from the previous clean version (`HEAD~1`) and
then re-applied only the intended current `tangle` changes.

### Re-applied minimal intended changes

Kept:

- `min-volume` default value `1000`
- new `tangle` date input
- `tangle` date default `2026-01-01`
- `tangle` date min `2026-01-01`
- hide mean / lock controls in the UI
- `runScan()` sends:
  - `dmi_tangle_spread`
  - `dmi_tangle_start_date`
- `buildMetaText()` now shows date + spread, not mean
- `buildIndicatorFooter()` for `tangle` no longer shows mean

Removed:

- duplicate / commented stale helper blocks that had accumulated in the file

## Files Not Changed In This Pass

Not changed:

- `backend_api.py`
- `update_db_fubon.py`
- DMI core math
- MACD logic
- `30m` gate logic

## Validation

Verified:

```powershell
python -m py_compile backend_api.py
```

Also verified by direct local counting:

- current backend-style `1d tangle` conditions still produce `117` hits on the
  current DB

## Current Best Interpretation

If the user still sees `1d tangle = 0` after reload, likely causes are now:

1. stale frontend / stale deployed page
2. stale backend instance not reloaded
3. browser cache serving old JS

It is much less likely to be:

- `tangle` backend logic itself

## Next Validation After Reload

1. hard refresh browser / clear cached frontend
2. reload API
3. test `1d -> DMI -> 全糾結`
   - expected: not zero on current DB
4. test `60m -> DMI -> 全糾結` on `2455`
   - with `spread 1.5`: still likely no hit
   - with `spread 2.0`: should become eligible

