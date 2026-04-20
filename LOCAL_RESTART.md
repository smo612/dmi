# Local Restart

Last updated: `2026-04-20`

## Project layout

- `backend_api.py`: FastAPI backend. It preloads `stock_data.db` into memory when the API starts.
- `market_watcher.py`: background watcher. During trading hours it updates recent intraday bars, and after `14:00` it runs end-of-day refresh.
- `update_db.py`: manual catch-up / rebuild tool.
- `scanner.html`, `scanner_cards.html`, `scanner_terminal.html`: static frontends. Local default API is `http://127.0.0.1:8000`.

## Two-terminal mode

- Terminal 1 runs the API.
- Terminal 2 runs `ngrok`.

This split matters because the frontend only talks to the API, and outside users need an `https://...ngrok...` address that forwards to your local `8000` port.

## Recommended restart flow

Use `restart_project.cmd`.

It opens two terminals for you:

1. API terminal:
   `start_api.cmd`
2. ngrok terminal:
   `start_ngrok.cmd`

## Three-terminal mode

If you want automatic sentinel polling and intraday DB refresh, open a third terminal:

```bat
python market_watcher.py
```

Summary:

- `2` terminals: website can use your local API through `ngrok`
- `3` terminals: website works and watcher also auto-polls sentinels

## Normal daily use

- If you only need the API: run `start_api.cmd`
- If you need the public tunnel only: run `start_ngrok.cmd`
- If you want outside users to open the hosted page and hit your local API: use `restart_project.cmd`
- If you also want automatic sentinel scanning: add `python market_watcher.py`

## Quick checks

- API health: `http://127.0.0.1:8000/status`
- Frontend: open `scanner_cards.html`
- Public API: open the `https://...ngrok.../status` URL shown by ngrok
- Watcher log: `market_watcher.log`
- DB update log: `update_db.log`

## Status display

The frontend status badge now shows:

- latest `30m` timestamp loaded by the API
- `stock_data.db` last modified time

This is meant to make it obvious whether the page is still looking at old data.

## Important note

`backend_api.py` keeps market data in memory. Because of that, the watcher now calls `/reload` after it writes new data, so the frontend can see fresh data without manually restarting the API again.

## Watcher note

- `market_watcher.py` was updated on `2026-04-20` to handle current `yfinance` MultiIndex formats when reading sentinel bars.
- If the watcher log keeps showing the same old `target=...` during a live trading day, that usually means the upstream `30m` source still has not exposed the new bar yet.
- In that case, check both:
  - watcher log
  - `/status` on the API

## Share link format

If your hosted frontend is, for example:

```text
https://yourname.github.io/your-repo/
```

and ngrok shows:

```text
https://abcd-1234.ngrok-free.app
```

then the share link is:

```text
https://yourname.github.io/your-repo/?api=https://abcd-1234.ngrok-free.app
```

That is the simplest way to let other people click one link and use your local API without editing the frontend every time.

## Recent updates

- Added local restart scripts for `uvicorn` and `ngrok`
- Added API status fields for latest `30m` data time and DB updated time
- Fixed watcher sentinel parsing for current `yfinance` MultiIndex layout
- Fixed intraday download window so same-day `15m/30m/60m` data is not excluded by Yahoo's end-exclusive date handling
- Added clearer watcher stale warning when market is open but sentinel `30m` still stays on the previous trading day
- When sentinel `30m` stays stale during market hours, watcher now forces a periodic intraday refresh and can synthesize `30m` from fresh `15m` data
