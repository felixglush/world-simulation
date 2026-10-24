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
