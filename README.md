# Marathon Auto Kill Recorder

Automatically saves a video clip and bumps an on-screen counter every time you
get a kill (or assist) in **Marathon**.

Marathon has no public kill API, so this tool *watches the kill feed*: it reads
the feed region, OCRs the text, and when a `downed` line contains your name it
(1) tells **OBS** to save its **Replay Buffer** as a clip and (2) updates a
**Text source** counter in OBS.

```
feed frame  ->  OCR  ->  detector (your name? + dedup)  ->  OBS save replay + counter
```

## Anti-cheat note

This tool never touches the game: it does **not** read game memory, inject code,
hook APIs, or send any input. It only reads the *picture* (your screen or OBS's
output) and controls OBS — the same category as OBS, ShadowPlay, or Medal.tv,
which give no gameplay advantage. That is not what anti-cheat targets.

To minimize even theoretical exposure, the default `capture_source` is
**`obs_virtualcam`**: OBS captures the game (universally tolerated) and this tool
only reads OBS's **Virtual Camera** (a webcam device). Set `capture_source: screen`
if you'd rather grab the monitor directly. For provably zero risk, run everything
on a **second PC** fed by a capture card so nothing runs on the game machine.

No guarantees are made about Bungie's policies — use at your own discretion.

## Requirements

- Windows PC with an **NVIDIA GPU** (the game machine — this must run there).
- **OBS 28+** (ships with obs-websocket v5).
- **Python 3.9+** on the same PC.

## 1. One-time OBS setup

1. **WebSocket:** OBS → *Tools → WebSocket Server Settings* → enable it. Note the
   **Port** (default 4455) and set/copy the **Password**.
2. **Replay Buffer:** OBS → *Settings → Output → Replay Buffer* → enable, set
   *Maximum Replay Time* to ~30s. (The tool will start the buffer for you on launch.)
   Make sure a *Recording Path* is set so clips have somewhere to land.
3. **Counter text source:** in your Scene, add *Source → Text (GDI+)*, name it
   exactly **`KillCounter`**. Position/size/font it however you like.
4. **Virtual Camera** (if using the default `capture_source: obs_virtualcam`):
   click **Start Virtual Camera** in OBS's Controls dock. Skip if you set
   `capture_source: screen`.

## 2. Install

```bat
git clone <your-repo-url> marathon-kill-recorder
cd marathon-kill-recorder
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

> EasyOCR pulls in PyTorch (large). If you'd rather stay light, set
> `ocr_engine: tesseract` in `config.yaml` and install the Tesseract binary
> (https://github.com/UB-Mannheim/tesseract/wiki).

## 3. Configure

Edit `config.yaml`:

- `player_name` — your **exact** in-game display name.
- `name_aliases` — common OCR misreads of your name (e.g. `l`/`I`, `0`/`O`).
- `obs.password` — the OBS websocket password from step 1.
- `trigger_keywords` — leave `["downed"]`; add `eliminated`/`killed` if you see
  the game use other verbs.
- `match_mode` — `self_or_assist` (kills + assists) or `self_only`.

## 4. Calibrate the feed region

With Marathon (or a screenshot showing the kill feed) on screen:

```bat
python calibrate.py
```

Drag a box around the kill-feed area, press **ENTER**. This writes `feed_region`
into `config.yaml`. Re-run if you change resolution or HUD scale.

## 5. Test before going live

**Detection logic only** (no OCR, no OBS):

```bat
python main.py --test-lines "YourName downed Ripper" "Ghost downed Bob" "Ripper downed YourName"
```

**OCR on a saved screenshot** (grab one with the kill feed visible):

```bat
python main.py --test-image path\to\shot.png
```

Confirm it reads the feed lines and flags *your* kills. Tune `ocr_upscale`,
`name_match_threshold`, and `name_aliases` until detection is reliable.

**Full pipeline, but OBS actions only logged** (safe live rehearsal):

```bat
python main.py --dry-run
```

Play a bit; watch the console print `KILL #n` on your kills.

## 6. Go live

```bat
python main.py
```

On each detected kill it saves an OBS replay clip, updates the `KillCounter`
source, and appends a row to `session_log.csv`.

## Files

| file | role |
|------|------|
| `config.yaml` | all settings (name, region, OBS, timing) |
| `calibrate.py` | drag-select the kill-feed region |
| `capture.py` | mss screen-region grab |
| `ocr.py` | preprocess + OCR (EasyOCR / Tesseract) |
| `detector.py` | parse feed, fuzzy name match, dedup — the core logic |
| `obs_client.py` | obs-websocket: save replay + update counter |
| `main.py` | wires it together; test/dry-run modes |
| `tests/` | unit tests for the detector (`python tests/test_detector.py`) |

## Tuning / troubleshooting

- **Missing kills:** lower `name_match_threshold`, add `name_aliases`, raise
  `ocr_upscale`, or re-calibrate a tighter `feed_region`.
- **False positives / double counts:** raise `name_match_threshold`, increase
  `dedup_ttl_seconds`.
- **No clips saved:** confirm Replay Buffer is enabled and a recording path is set;
  check the OBS websocket password/port.
- **OCR slow:** EasyOCR's first call loads models (slow once). Ensure GPU is used;
  or switch to `tesseract`.

## Known limits

- Detection depends on the kill feed being visible and readable — obscured or
  very fast feeds may be missed.
- "downed" in an extraction shooter may differ from a confirmed elimination;
  adjust `trigger_keywords` to match what you actually want to clip.
