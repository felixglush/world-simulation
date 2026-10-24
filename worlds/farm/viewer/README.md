# Farm viewer

A single-page isometric replay of a farm run. It reads the JSONL log written by `python -m farm_world --output` and draws the real world state for each day: terraced paddies that fill and drain, wheat that changes colour by stage, canals with flowing water and open or closed gates, wells, houses, rain and the river. Farmers stand where their action happened and walk between days, with an emoji bubble for what they did and a speech bubble when they sent a message. Click a farmer for their barn, last reasoning, plan and reflections. Positions are real: they come from the world's snapshots, not from a mapping authored for the view.

```bash
cd worlds/farm/viewer && python -m http.server 8000
# open http://localhost:8000/?log=sample-run.jsonl
```

You can also drop any run log onto the page. Space plays or pauses, the arrow keys step a day. Logs written before `layout` was added to the header use `layout.json`, which `tests/test_viewer.py` keeps in step with the code.

`node viewer/smoke.js RUN.jsonl out` is a headless Chromium check (Playwright) that loads a log, scrubs, clicks a farmer and exits non-zero on any page error. Art is drawn in code on a canvas, so there are no assets to source; a tileset can replace it later without touching the log format.

## Hourly replay

The scrubber steps one working hour at a time (ten per day, 07:00 to 16:00). Terrain, crops and water come from the day's end-of-day snapshot; farmers walk between the tiles where their actions happened, and speech bubbles, trades and the message feed are placed at the hour they were sent. Arrow keys step an hour, shift plus arrow steps a day. Logs written before the hourly loop still play (every action lands at 07:00).

## Live view

`python -m farm_world --output RUN.jsonl` writes the log as the run goes (a `run` header, a `start` snapshot, each hour's events, and a day snapshot at the end of every day; a `summary` row closes it). To watch a run as it happens:

```bash
python worlds/farm/viewer/serve.py RUN.jsonl      # start before or after the run
# open http://127.0.0.1:8000/?log=/run.jsonl&live=1
```

The page asks for the bytes after the last row it has every 1.5 seconds and adds the new hours on a provisional day that uses the previous day's terrain until that day's snapshot lands. "Follow live" keeps the view on the newest hour; scrubbing or pressing Play turns it off. No hosting is needed: the log is a file on the machine running the simulation, so a static host such as Vercel cannot see it. To share a finished run, host the viewer folder and the `.jsonl` as static files and open `index.html?log=run.jsonl`.
