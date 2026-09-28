# cyba_viz

A GPU-driven, audio-reactive visual instrument that turns a Tumblr media archive
into hypnotic, concert-style visuals. Built on ModernGL/GLSL with a
TouchDesigner-style feedback pipeline.

Feed it thousands of images and GIFs; it spawns them in a self-warping feedback
field, bends them into infinite spiral tunnels, kaleidoscopes them, punches the
zoom on the kick drum, and can be driven live over OSC, MIDI, or your Mac's
microphone.

```
   prev feedback ──(decay / zoom / rotate / aberration / displacement)──▶ target
   sprites (stills from texture atlases + live GIF textures) ───────────▶ target
   target ──(kaleidoscope / hue / tonemap / vignette / grain)───────────▶ screen
   swap(target, feedback)
```

---

## Features

- **Feedback compositor** — decay, zoom, rotation, chromatic aberration, and a
  Resolume/Photoshop-style **displacement** that uses the previous rendered frames
  as its displacement map (the image warps itself).
- **Two modes** — *collage* (random overlapping sprites) and **spiral tunnel**
  (images on a logarithmic spiral, tunnelling forever).
- **Kaleidoscope, hue, filmic tonemapping, vignette, grain, bloom.**
- **Kick-drum bounce** — a beat-synced zoom punch (default 15%), tempo from the
  internal clock or the **microphone** (low-band onset detection).
- **Modulators** — `random` and slow `cycle` modes that morph parameters, with a
  global speed limit (spiral controls capped to a quarter of it).
- **OSC + optional MIDI** input, **presets**, and a live **HUD**.

## Requirements

- macOS (tested on Apple Silicon / Metal-backed GL 4.1), Linux or Windows.
- Python 3.10+.

## Install

```bash
git clone <your-remote>/cyba_viz.git
cd cyba_viz
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional extras:

```bash
.venv/bin/pip install mido python-rtmidi   # MIDI controllers
```

## Prepare assets

`cyba_viz` renders from a preprocessed asset library, not the raw scrape. Point
it at a media folder (e.g. the output of the separate **tumblr_scraper** repo):

```bash
# finds archives/<blog>/media in the cwd, a sibling tumblr_photos/ repo,
# or $TUMBLR_ARCHIVE_DIR
.venv/bin/python prep_assets.py auroraangelic

# or point at any folder of images/gifs
.venv/bin/python prep_assets.py auroraangelic --media-root /path/to/media
```

Stills are downscaled so their longest edge is capped at **2560 px** (never
upscaled below native); GIFs have up to **64** frames extracted at up to
**1280 px**. Output goes to `assets/<blog>/` (git-ignored).

Useful flags: `--max-edge`, `--gif-edge`, `--max-gif-frames`, `--quality`,
`--workers`, `--force`.

## Run

```bash
# windowed
.venv/bin/python run.py auroraangelic

# fullscreen, spiral tunnel, rave preset, mic sync
.venv/bin/python run.py auroraangelic --spiral --fullscreen --preset rave \
    --audio --audio-device 1

# headless smoke test (writes a PNG, no window)
.venv/bin/python run.py auroraangelic --headless --frames 240 --out frame.png
```

### Keyboard

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| `space` | pause | `f` | fullscreen |
| `q` / `esc` | quit | `i` | settings + keymap HUD |
| `h` / `shift+h` | hue +30° / −30° | `u` | hue drift speed |
| `k` | kaleidoscope | `b` | bloom bias |
| `s` | spiral ⇄ collage | `d` / `shift+d` | displacement level ± |
| `g` | gif chance | `n` | reshuffle atlases |
| `x` | bounce (kick zoom) | `z` | bounce amount |
| `r` | random mode | `c` | cycle mode |
| `p` / `shift+p` | trigger / save `rave` preset | `shift+r` | reset settings |
| `t` | tap tempo (BPM) | `a` | mic kick-sync |
| `[` / `]` | spiral twist | `,` / `.` | tunnel speed |
| `↑` / `↓` | density | `←` / `→` | feedback decay |

`i` opens the HUD: full keymap, every live parameter, current mode, FPS, and a
BPM readout with a beat meter and mic level. Manually changed parameters are
held for 12 s (marked `*`) even while a modulator runs.

## OSC

The app listens on **`127.0.0.1:9001`** by default (change with `--osc-port`,
bind to the LAN with `--osc-host 0.0.0.0`).

Every parameter in `pyviz/control.py` is addressable as `/<name>`, e.g.
`/decay`, `/displace`, `/spiral_twist`, `/bounce_amount`, `/mod_speed`.

Commands:

| Address | Value | Effect |
| --- | --- | --- |
| `/tap` | – | tap tempo |
| `/reset` | – | reset all settings |
| `/preset` | `<name>` | load a preset |
| `/preset/save` | `<name>` | save current settings |
| `/random` | `0`/`1` | random mode |
| `/cycle` | `0`/`1` | cycle mode |
| `/spiral` | `0`/`1` | spiral tunnel |
| `/mode` | `spiral`/`collage` | set mode |

Send with the bundled helper:

```bash
python osc_send.py /bpm 128
python osc_send.py /bounce 1 /bounce_amount 0.15 /displace 0.06
python osc_send.py /preset rave
python osc_send.py --host 192.168.1.20 --port 9001 /cycle 1
```

…or from TouchDesigner / Resolume / TouchOSC: UDP → `127.0.0.1:9001`.

## MIDI

```bash
.venv/bin/pip install mido python-rtmidi
.venv/bin/python run.py auroraangelic --midi
```

CC → parameter mappings live in `pyviz/control.py` (`cc_map`) — edit for your
controller (e.g. a Pioneer). OSC and MIDI both write into the same parameter
bus, so they compose freely.

## Microphone kick-sync

Estimates BPM from the low-frequency "kick" band (default **35–160 Hz**) using
FFT band energy + spectral-flux onset detection, then drives both the tempo and
the beat-synced bounce directly from the music.

```bash
.venv/bin/python run.py auroraangelic --list-audio          # list inputs
.venv/bin/python run.py auroraangelic --audio --audio-device 1
.venv/bin/python run.py auroraangelic --audio --audio-device 1 --audio-band 40 120
```

Tip: install [BlackHole](https://existential.audio/blackhole/) and route your DJ
app's output to it for a clean feed (`--audio-device 0`) instead of using a room
mic. If it reads silence, grant mic access in
System Settings → Privacy & Security → Microphone.

`audio_gain` (OSC `/audio_gain`) adjusts sensitivity.

## Presets

Presets are stored in `presets.json` as `{ name: { param: value } }`.

- `p` triggers the **`rave`** preset; `shift+p` overwrites it with the current
  settings.
- Load one at startup with `--preset rave`.

## Parameters

All of these are visible in the HUD (`i`) and settable over OSC (`/<name>`).
Defaults in parentheses.

**Field** — `density` (1.0), `p_gif` (0.35), `decay` (0.92), `zoom` (1.004),
`feed_rot` (0), `aberr` (0), `speed` (1.0), `fade_to_black` (0).

**Sprites** — `sprite_rot` (0.12), `drift` (0.35), `bloom` (0.6),
`size_min` (0.35), `size_max` (1.15), `lifetime` (6.0).

**Displacement** — `displace` (0), `disp_rgb` (0.4), `disp_x` (1.0),
`disp_y` (1.0), `disp_detail` (2.0).

**Look** — `hue` (0), `hue_speed` (0), `sat` (1.15), `contrast` (1.05),
`kaleido` (0), `vignette` (0.55), `grain` (0.03), `exposure` (0.8).

**Spiral tunnel** — `spiral` (0), `spiral_twist` (2.0), `spiral_speed` (0.7),
`spiral_arms` (3), `spiral_size` (1.2), `spiral_count` (90), `spiral_rot` (0.12).

**Bounce** — `bounce` (0), `bounce_amount` (0.15), `bounce_decay` (11),
`bounce_fb` (0.5).

**Timing / sync** — `bpm` (124), `bpm_sync` (0), `audio_sync` (0),
`audio_gain` (1.0).

**Modulators** — `mod_speed` (0.5).

## CLI reference

```
--width --height          window size (default 1280x720)
--render-scale            internal render resolution vs display (e.g. 0.5 for 4K/5K)
--fullscreen
--atlas-size --cell --atlases   still-image texture atlas layout
--gif-slots --gif-edge          animated GIF pool
--seed
--osc-port --osc-host           OSC (default 127.0.0.1:9001)
--midi
--spiral --bounce --bpm --preset
--audio --audio-device --audio-band --list-audio
--headless --frames --out
```

Atlas quality/variety trade-off: `--cell 1024 --atlases 4` = 64 resident stills
at 1024 px (default); `--cell 2048 --atlases 2` = fewer but sharper;
`--cell 768 --atlases 6` = more variety.

## Architecture

```
run.py            entry point: window loop, key handling, CLI
prep_assets.py    media → WebP tiles + GIF frames + manifest.json
osc_send.py       OSC command-line sender
pyviz/
  renderer.py     ModernGL engine, GLSL shaders, atlases, GIF pool, sprite model
  assets.py       manifest loader / media library
  control.py      parameter bus, presets, modulators, OSC/MIDI listeners
  audio.py        mic kick detector + BPM estimator
  hud.py          on-screen settings + keymap overlay
```

Media is packed into GPU texture atlases with mipmaps; animated GIFs live in a
small pool of dynamic textures advanced only while visible. Feedback uses
ping-pong float16 framebuffers, so effects compound frame to frame.

## Troubleshooting

- **Fullscreen fills only a corner** — Retina scaling; handled via
  `get_framebuffer_size()`. On very large displays add `--render-scale 0.5`.
- **No such file: assets/<blog>/manifest.json** — run `prep_assets.py` first, or
  pass `--assets`.
- **Mic reads silence** — grant Microphone permission; try a different
  `--audio-device`; raise `audio_gain`.
- **Low FPS** — lower `density`, use `--render-scale 0.5`, or reduce
  `--cell`/`--atlases`.
