"""On-screen HUD: live settings + keymap, toggled with `i`.

Rendered offscreen with Pillow into a texture and blitted through the
ModernGL overlay pass, so it never fights pyglet/moderngl GL state.
"""

from __future__ import annotations

import time

import moderngl
from PIL import Image, ImageDraw, ImageFont

KEY_LINES = [
    "ESC / Q ...... quit",
    "SPACE ........ pause",
    "F ............ fullscreen",
    "I ............ toggle this HUD",
    "H ............ hue +30 (shift = -30)",
    "U ............ hue drift speed",
    "K ............ kaleidoscope",
    "S ............ spiral tunnel mode",
    "D ............ displace level (self-warp)",
    "B ............ bloom bias",
    "G ............ gif chance",
    "N ............ reshuffle atlases",
    "R ............ random mode",
    "C ............ cycle mode (slow morph)",
    "P ............ trigger 'rave' preset",
    "Shift+P ...... save current as 'rave'",
    "Shift+R ...... reset settings",
    "T ............ tap tempo (BPM)",
    "X ............ bounce mode (kick zoom)",
    "Z ............ bounce amount",
    "A ............ mic kick-sync (bpm from mic)",
    "[ / ] ........ spiral twist",
    ", / . ........ tunnel speed",
    "UP / DOWN .... density",
    "LEFT / RIGHT . feedback decay",
]

_FONT_PATHS = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
]

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def load_font(size: int):
    if size in _font_cache:
        return _font_cache[size]
    font = None
    for path in _FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font


class Hud:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.texture = None
        self.visible = False
        self._last = 0.0
        self._size = (0, 0)
        self._params_dirty = True

    def toggle(self) -> None:
        self.visible = not self.visible
        self._last = 0.0

    def mark_dirty(self) -> None:
        self._params_dirty = True

    def refresh(self, controls, fps: float, size: tuple[int, int],
                time_now: float = 0.0, force: bool = False) -> None:
        if not self.visible:
            return
        now = time.monotonic()
        if not force and size == self._size and now - self._last < 0.2:
            return
        self._last = now
        self._size = size
        w, h = size
        base = max(12, int(h * 0.019))
        font = load_font(base)
        title = load_font(int(base * 1.35))

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w, h], fill=(6, 8, 14, 186))

        pad = int(h * 0.035)
        line_h = int(base * 1.5)

        d.text((pad, pad), "KEYS", font=title, fill=(120, 255, 200, 255))
        y = pad + int(base * 2.2)
        for line in KEY_LINES:
            d.text((pad, y), line, font=font, fill=(225, 228, 238, 255))
            y += line_h

        rx = int(w * 0.40)
        d.text((rx, pad), "ACTIVE SETTINGS", font=title, fill=(120, 255, 200, 255))
        items = list(controls.values.items())
        half = (len(items) + 1) // 2
        col_y = pad + int(base * 2.2)
        now = time.monotonic()
        overrides = getattr(controls, "overrides", {})
        for col, chunk in enumerate((items[:half], items[half:])):
            for i, (name, value) in enumerate(chunk):
                cx = rx + col * int(w * 0.30)
                cy = col_y + i * line_h
                held = overrides.get(name, 0.0) > now
                d.text((cx, cy), name + (" *" if held else ""), font=font,
                       fill=(255, 175, 95, 255) if held else (170, 175, 190, 255))
                d.text((cx + int(w * 0.155), cy), f"{value:8.3f}", font=font,
                       fill=(255, 195, 95, 255) if held else (255, 235, 150, 255))

        mode = "SPIRAL TUNNEL" if controls["spiral"] > 0.5 else "COLLAGE"
        if controls["bounce"] > 0.5:
            mode += "  ·  BOUNCE"
        label = getattr(getattr(controls, "modulator", None), "label", "")
        if label:
            mode = f"{mode}  ·  {label}"
        d.text((pad, h - pad - line_h), mode, font=title, fill=(255, 150, 200, 255))

        # BPM readout + beat meter
        bpm = controls.clock.bpm
        beat = controls.clock.beat_phase(time_now)
        pulse = controls.clock.beat_pulse(time_now, 11.0)
        meter_w = int(w * 0.18)
        meter_h = int(base * 0.7)
        bx = (w - meter_w) // 2
        by = h - pad - int(line_h * 1.9)
        d.text((bx, by - int(base * 2.4)), f"{bpm:5.1f} BPM", font=title, fill=(255, 120, 180, 255))
        d.rectangle([bx, by, bx + meter_w, by + meter_h], fill=(45, 45, 58, 255))
        d.rectangle([bx, by, bx + int(meter_w * (1.0 - beat)), by + meter_h], fill=(255, 90, 160, 255))
        dot_r = int(base * 0.5 + pulse * base * 1.3)
        dcx = bx + meter_w + int(base * 2.4)
        dcy = by + meter_h // 2
        d.ellipse([dcx - dot_r, dcy - dot_r, dcx + dot_r, dcy + dot_r], fill=(255, 205, 90, 255))

        # mic kick-sync indicator + level
        audio = getattr(controls, "audio", None)
        if audio is not None:
            live = audio.ready and controls["audio_sync"] > 0.5
            mic = "MIC LIVE" if live else "MIC off"
            d.text((pad, by - int(base * 0.2)), mic, font=font,
                   fill=(120, 255, 140, 255) if live else (120, 120, 130, 255))
            lvl_w = int(w * 0.07)
            lx = pad + int(w * 0.075)
            d.rectangle([lx, by, lx + lvl_w, by + meter_h], fill=(30, 34, 42, 255))
            lvl = max(0.0, min(1.0, audio.level * 1500.0))
            d.rectangle([lx, by, lx + int(lvl_w * lvl), by + meter_h], fill=(120, 255, 140, 255))

        d.text((rx, h - pad - line_h), f"{fps:5.0f} fps", font=font, fill=(140, 220, 255, 255))
        d.text((rx + int(w * 0.16), h - pad - line_h), "* = manual override (held 12s)",
               font=font, fill=(255, 175, 95, 255))

        tex = self.ctx.texture((w, h), 4, img.transpose(Image.FLIP_TOP_BOTTOM).tobytes())
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        if self.texture is not None:
            self.texture.release()
        self.texture = tex
