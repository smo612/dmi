# Agent Context 2026-04-26K

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
- `AGENT_CONTEXT_2026-04-26J.md`

## Focus Of This Pass

User reported that `1d DMI tangle` still showed `0` hits in real runtime, even
though before the intraday-tangle repairs, daily tangle had been working.

This pass identified and fixed the actual backend regression.

## Root Cause

`strategy_dmi_tangle()` was comparing:

- `dt_series` from daily candles, which is timezone-naive (`datetime64[ns]`)

against:

- `start_date`, which had been converted to a timezone-aware Taipei timestamp
  (`2026-01-01 00:00:00+08:00`)

Pandas treats this as an invalid comparison:

- naive datetime series
- vs aware timestamp

That raises:

- `TypeError: Invalid comparison between dtype=datetime64[ns] and Timestamp`

Why the UI then showed `0` instead of a visible failure:

- the `/scan` loop wraps each ticker in `try/except`
- so each daily ticker would warn and continue
- result: no hits accumulated
- frontend looked like "0 hits" instead of a hard crash

This perfectly explains the observed behavior:

- local independent counting script still found daily matches
- real runtime `/scan` returned zero

## Fix

Changed file:

- `backend_api.py`

Inside `strategy_dmi_tangle()`:

- if `dt_series` is timezone-naive:
  - compare against `start_date.tz_localize(None)`
- else:
  - compare against `start_date` converted to the series timezone

Current logic:

```python
if getattr(dt_series.dt, "tz", None) is None:
    compare_start = start_date.tz_localize(None)
else:
    compare_start = start_date.tz_convert(dt_series.dt.tz)
```

and then:

```python
match = (dt_series >= compare_start) & (spread <= float(spread_max))
```

## Why This Is Safe

This is a narrow fix in the `tangle` strategy only.

It does not change:

- DMI math
- MACD logic
- updater
- `30m` flat-bar gate
- API cache behavior

It only fixes timezone compatibility for the tangle start-date comparison.

## Verification

Verified:

```powershell
python -m py_compile backend_api.py
```

This passed after the fix.

## Expected Result After Reload

After API reload:

- `1d -> DMI -> 全糾結` should no longer collapse to zero because of the
  timezone-comparison exception
- `60m / 15m / 30m` tangle should keep using the same start-date rule, but now
  daily and intraday both compare safely in their own datetime types

## Practical Note

This was the real "daily was good before, then became zero after tangle work"
regression.

The earlier local count of `117` daily tangle matches was consistent with the
data; the missing piece was that the production path was throwing away each
ticker due to the naive-vs-aware comparison failure.

