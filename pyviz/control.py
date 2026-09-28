"""Parameter bus with keyboard/OSC/MIDI-ready hooks.

Everything the show does is driven by named parameters in ``SPEC``. Keyboards,
OSC, MIDI and (later) a control surface all write into the same bus, so wiring
up a Pioneer controller or TouchDesigner/Resolume is just mapping messages to
parameter names.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

PRESET_PATH = Path(__file__).resolve().parent.parent / "presets.json"

# name -> (default, min, max)
SPEC: dict[str, tuple[float, float, float]] = {
    "density": (1.0, 0.05, 4.0),      # how many sprites on screen
    "p_gif": (0.35, 0.0, 1.0),        # chance a new sprite is a gif
    "decay": (0.92, 0.0, 0.995),      # feedback persistence (trails)
    "zoom": (1.004, 0.94, 1.06),      # feedback zoom (tunnel in/out)
    "feed_rot": (0.0, -0.05, 0.05),   # feedback rotation per frame
    "aberr": (0.0, 0.0, 0.02),        # feedback chromatic aberration
    # -- displacement (self-warp driven by previous rendered frames) --
    "displace": (0.0, 0.0, 0.25),     # master strength (UV units)
    "disp_rgb": (0.4, 0.0, 1.0),      # 0 = luma gradient map, 1 = rgb-vector map
    "disp_x": (1.0, 0.0, 3.0),        # horizontal scale
    "disp_y": (1.0, 0.0, 3.0),        # vertical scale
    "disp_detail": (2.0, 0.5, 8.0),   # gradient sample distance (px)
    "hue": (0.0, -1.0, 1.0),          # manual hue offset (turns, direct control)
    "hue_speed": (0.0, -0.5, 0.5),    # automatic hue drift per second
    "sat": (1.15, 0.0, 2.5),
    "contrast": (1.05, 0.4, 2.0),
    "kaleido": (0.0, 0.0, 12.0),      # 0 = off, else segments
    "vignette": (0.55, 0.0, 1.2),
    "grain": (0.03, 0.0, 0.25),
    "exposure": (0.8, 0.1, 3.0),      # pre-tonemap gain
    "sprite_rot": (0.12, 0.0, 2.0),   # sprite spin speed multiplier
    "drift": (0.35, 0.0, 3.0),        # sprite drift speed multiplier
    "bloom": (0.6, 0.0, 2.0),         # additive blend bias
    "size_min": (0.35, 0.05, 2.0),    # fraction of screen height
    "size_max": (1.15, 0.05, 3.0),
    "lifetime": (6.0, 0.5, 60.0),
    "speed": (1.0, 0.0, 4.0),         # global time multiplier
    "bpm": (124.0, 40.0, 200.0),
    "bpm_sync": (0.0, 0.0, 1.0),      # 0 = free-running, 1 = locked to beat
    # -- kick-drum bounce (beat-synced zoom punch) --
    "bounce": (0.0, 0.0, 1.0),        # toggle (x)
    "bounce_amount": (0.15, 0.0, 0.6),# zoom punch size (15% = 0.15)
    "bounce_decay": (11.0, 1.0, 30.0),# pulse sharpness (higher = snappier)
    "bounce_fb": (0.5, 0.0, 1.0),     # how much the punch drives feedback zoom
    # -- mic kick-sync --
    "audio_sync": (0.0, 0.0, 1.0),    # listen to mic for kick/BPM (a)
    "audio_gain": (1.0, 0.1, 30.0),   # mic sensitivity
    # -- modulator speed limit --
    "mod_speed": (0.5, 0.05, 1.0),    # max modulation speed (fraction of range/sec)
    "fade_to_black": (0.0, 0.0, 1.0), # momentary blackout amount
    # -- spiral tunnel mode --
    "spiral": (0.0, 0.0, 1.0),        # 0 = collage, 1 = infinite spiral
    "spiral_twist": (2.0, 0.0, 6.0),  # spiral tightness (radians per depth)
    "spiral_speed": (0.7, 0.02, 4.0), # tunnel speed
    "spiral_arms": (3.0, 1.0, 8.0),   # number of spiral arms
    "spiral_size": (1.2, 0.1, 3.0),   # image size vs radius
    "spiral_count": (90.0, 8.0, 300.0),
    "spiral_rot": (0.12, -2.0, 2.0),  # global spiral spin
}

# Parameters the auto-modulators are allowed to touch, with soft ranges
# (narrower than SPEC so the show never goes fully off the rails).
MODULATE_RANGES: dict[str, tuple[float, float]] = {
    "decay": (0.62, 0.96),
    "zoom": (0.99, 1.02),
    "feed_rot": (-0.012, 0.012),
    "aberr": (0.0, 0.006),
    "displace": (0.0, 0.08),
    "disp_rgb": (0.0, 1.0),
    "disp_x": (0.3, 2.0),
    "disp_y": (0.3, 2.0),
    "disp_detail": (1.0, 4.0),
    "sat": (0.6, 1.8),
    "contrast": (0.8, 1.4),
    "vignette": (0.1, 0.9),
    "grain": (0.0, 0.08),
    "exposure": (0.5, 1.3),
    "sprite_rot": (0.0, 0.5),
    "drift": (0.1, 1.0),
    "bloom": (0.3, 1.4),
    "size_min": (0.2, 0.8),
    "size_max": (0.7, 1.6),
    "lifetime": (2.0, 12.0),
    "density": (0.5, 2.0),
    "p_gif": (0.0, 0.6),
    "spiral_twist": (0.8, 3.5),
    "spiral_speed": (0.2, 1.6),
    "spiral_size": (0.6, 1.8),
    "spiral_rot": (-0.4, 0.4),
    "spiral_arms": (2.0, 5.0),
}

# Discrete picks applied only in random mode.
MODULATE_DISCRETE = {
    "kaleido": [0.0, 0.0, 4.0, 6.0, 8.0, 12.0],
}

# Extra speed limit per parameter (multiplies the global mod_speed cap).
# Spiral controls move at a quarter of the global speed.
MOD_SPEED_FACTORS: dict[str, float] = {
    "spiral_twist": 0.25,
    "spiral_speed": 0.25,
    "spiral_arms": 0.25,
    "spiral_size": 0.25,
    "spiral_rot": 0.25,
    "spiral_count": 0.25,
}


@dataclass
class BPMClock:
    bpm: float = 124.0
    _last_tap: float = 0.0
    _taps: list[float] = None  # type: ignore

    def __post_init__(self) -> None:
        self._taps = []

    def tap(self) -> None:
        now = time.monotonic()
        if self._taps and now - self._taps[-1] > 2.5:
            self._taps.clear()
        self._taps.append(now)
        self._taps = self._taps[-4:]
        if len(self._taps) >= 2:
            intervals = [b - a for a, b in zip(self._taps, self._taps[1:])]
            avg = sum(intervals) / len(intervals)
            if avg > 0:
                self.bpm = max(40.0, min(200.0, 60.0 / avg))

    def beat_phase(self, t: float) -> float:
        """Phase within the current beat, 0..1."""
        period = 60.0 / max(1.0, self.bpm)
        return (t % period) / period

    def beat_pulse(self, t: float, sharpness: float = 6.0) -> float:
        """1.0 at the beat, decaying to 0 before the next."""
        return pow(1.0 - self.beat_phase(t), sharpness)


class Modulator:
    """Auto-modulates show parameters.

    - random mode: quick, jumpy target changes (short eased segments)
    - cycle mode : slow, smooth, per-parameter phased morphs

    Each parameter eases from its current value toward a random target over its
    own duration, then picks a new target. Because durations differ, parameters
    drift out of phase with each other for an organic, non-synchronised flow.
    """

    def __init__(self, controls: "Controls") -> None:
        self.c = controls
        self.random_mode = False
        self.cycle_mode = False
        self.state: dict[str, dict] = {}
        for name, (lo, hi) in MODULATE_RANGES.items():
            cur = controls.values[name]
            period = self._period()
            self.state[name] = {
                "start": cur,
                "target": random.uniform(lo, hi),
                "t": random.random() * period,
                "period": period,
            }
        self._last_discrete = 0.0

    def _period(self) -> float:
        if self.random_mode:
            return random.uniform(0.5, 1.6)
        return random.uniform(8.0, 28.0)

    def toggle_random(self) -> bool:
        self.random_mode = not self.random_mode
        if self.random_mode:
            self.cycle_mode = False
            self._retarget_all(quick=True)
        return self.random_mode

    def toggle_cycle(self) -> bool:
        self.cycle_mode = not self.cycle_mode
        if self.cycle_mode:
            self.random_mode = False
            self._retarget_all(quick=False)
        return self.cycle_mode

    def _retarget_all(self, quick: bool) -> None:
        for name, st in self.state.items():
            lo, hi = MODULATE_RANGES[name]
            st["start"] = self.c.values[name]
            st["target"] = random.uniform(lo, hi)
            st["t"] = 0.0
            st["period"] = self._period()
            if not quick:
                st["t"] = random.random() * st["period"] * 0.5

    @property
    def active(self) -> bool:
        return self.random_mode or self.cycle_mode

    @property
    def label(self) -> str:
        if self.random_mode:
            return "RANDOM MODE"
        if self.cycle_mode:
            return "CYCLE MODE"
        return ""

    def update(self, dt: float) -> None:
        if not self.active:
            return
        for name, st in self.state.items():
            if self.c.is_held(name):
                st["start"] = self.c.values[name]
                st["target"] = self.c.values[name]
                st["t"] = 0.0
                st["period"] = self._period()
                continue
            st["t"] += dt
            if st["t"] >= st["period"]:
                lo, hi = MODULATE_RANGES[name]
                st["start"] = self.c.values[name]
                st["target"] = random.uniform(lo, hi)
                st["t"] = 0.0
                st["period"] = self._period()
            prog = min(1.0, st["t"] / st["period"])
            ease = prog * prog * (3.0 - 2.0 * prog)  # smoothstep
            value = st["start"] + (st["target"] - st["start"]) * ease
            # speed limit: never move faster than mod_speed * range per second
            lo, hi = MODULATE_RANGES[name]
            factor = MOD_SPEED_FACTORS.get(name, 1.0)
            cap = self.c["mod_speed"] * factor * (hi - lo) * dt
            cur = self.c.values[name]
            if cap > 0.0:
                value = max(cur - cap, min(cur + cap, value))
            self.c.set(name, value, "mod")

        if self.random_mode:
            self._last_discrete += dt
            if self._last_discrete > random.uniform(3.0, 9.0):
                self._last_discrete = 0.0
                for name, choices in MODULATE_DISCRETE.items():
                    if not self.c.is_held(name):
                        self.c.set(name, random.choice(choices), "mod")


class Controls:
    def __init__(self) -> None:
        self.values: dict[str, float] = {k: v[0] for k, v in SPEC.items()}
        self.clock = BPMClock(self.values["bpm"])
        self.modulator = Modulator(self)
        self.paused = False
        self.quit = False
        self.messages: list[str] = []
        self._lock = threading.Lock()
        self.overrides: dict[str, float] = {}
        self.override_hold = 12.0
        self.audio = None

    def update(self, dt: float) -> None:
        self.modulator.update(dt)

    def get(self, name: str) -> float:
        return self.values.get(name, 0.0)

    def __getitem__(self, name: str) -> float:
        return self.get(name)

    def set(self, name: str, value: float, source: str = "user") -> None:
        if name not in SPEC:
            return
        lo, hi = SPEC[name][1], SPEC[name][2]
        with self._lock:
            self.values[name] = max(lo, min(hi, float(value)))
        if name == "bpm":
            self.clock.bpm = self.values[name]
        if source == "user" and name in MODULATE_RANGES:
            self.overrides[name] = time.monotonic() + self.override_hold

    def clear_overrides(self) -> None:
        self.overrides.clear()

    def is_held(self, name: str) -> bool:
        return self.overrides.get(name, 0.0) > time.monotonic()

    # -- presets -----------------------------------------------------------
    def _read_presets(self) -> dict:
        if not PRESET_PATH.exists():
            return {}
        try:
            return json.loads(PRESET_PATH.read_text())
        except Exception:
            return {}

    def save_preset(self, name: str) -> None:
        data = self._read_presets()
        data[name] = {k: self.values[k] for k in SPEC}
        PRESET_PATH.write_text(json.dumps(data, indent=2))
        self.notify(f"saved preset '{name}'")

    def load_preset(self, name: str) -> bool:
        data = self._read_presets()
        if name not in data:
            return False
        for key, value in data[name].items():
            if key in SPEC:
                self.set(key, value, "user")
        self.clear_overrides()
        self.notify(f"preset '{name}'")
        return True

    def list_presets(self) -> list[str]:
        return list(self._read_presets().keys())

    def nudge(self, name: str, delta: float) -> None:
        self.set(name, self.get(name) + delta)

    def notify(self, text: str) -> None:
        self.messages.append(text)
        self.messages = self.messages[-6:]

    def describe(self) -> str:
        order = ["density", "decay", "zoom", "hue_speed", "kaleido", "bpm", "bloom"]
        return "  ".join(f"{k}={self.values[k]:.2f}" for k in order)

    def handle_osc(self, address: str, *args) -> None:
        """Map an OSC message to a parameter or command.

        Parameters:   /decay 0.9   /bpm 128   /bounce 1   /disp_x 1.5
        Commands:     /tap   /reset   /preset rave   /preset/save rave
                      /random 1   /cycle 0   /spiral 1   /mode spiral
        Any address form works: /param/decay and /decay are equivalent.
        """
        path = address.strip("/")
        parts = [p for p in path.split("/") if p]
        val = args[0] if args else None
        if path in ("tap", "tempo/tap", "tempo_tap"):
            self.clock.tap()
            self.set("bpm", self.clock.bpm, "mod")
            return
        if path == "reset":
            for key, (default, _, _) in SPEC.items():
                self.set(key, default, "mod")
            self.clear_overrides()
            return
        if parts and parts[0] == "preset":
            if len(parts) > 1 and parts[1] == "save":
                name = str(val) if val is not None else (parts[2] if len(parts) > 2 else "rave")
                self.save_preset(name)
            else:
                name = str(val) if val is not None else (parts[1] if len(parts) > 1 else "rave")
                self.load_preset(name)
            return
        if path == "random" and val is not None:
            if (float(val) > 0.5) != self.modulator.random_mode:
                self.modulator.toggle_random()
            return
        if path == "cycle" and val is not None:
            if (float(val) > 0.5) != self.modulator.cycle_mode:
                self.modulator.toggle_cycle()
            return
        if path == "mode" and val is not None:
            self.set("spiral", 1.0 if str(val).lower().startswith("s") else 0.0)
            return
        key = parts[-1] if parts else path
        key = key.replace("-", "_")
        if key in SPEC and val is not None:
            self.set(key, float(val))

    def start_listeners(self, osc_port: int = 9001, midi: bool = False,
                        osc_host: str = "127.0.0.1") -> None:
        """Best-effort OSC/MIDI listeners; safe no-ops if libs absent."""
        try:
            from pythonosc import dispatcher, osc_server  # type: ignore
        except Exception:
            if osc_port:
                self.notify("OSC: python-osc not installed (skipped)")
        else:
            disp = dispatcher.Dispatcher()
            disp.set_default_handler(lambda address, *a: self.handle_osc(address, *a))
            try:
                server = osc_server.ThreadingOSCUDPServer((osc_host, osc_port), disp)
            except OSError as exc:
                self.notify(f"OSC: could not bind {osc_host}:{osc_port} ({exc})")
            else:
                def run():
                    server.serve_forever()

                threading.Thread(target=run, daemon=True).start()
                self.notify(f"OSC listening on {osc_host}:{osc_port}")

        if midi:
            try:
                import mido  # type: ignore
            except Exception:
                self.notify("MIDI: mido not installed (skipped)")
            else:
                try:
                    names = mido.get_input_names()
                    if not names:
                        self.notify("MIDI: no input devices found")
                        return
                    port = mido.open_input(names[0])

                    # CC -> parameter map (edit for your Pioneer controller)
                    cc_map = {1: "density", 2: "decay", 3: "zoom", 4: "hue_speed",
                              5: "kaleido", 6: "bloom", 7: "sat", 8: "grain"}

                    def run_midi():
                        for msg in port:
                            if msg.type == "control_change" and msg.control in cc_map:
                                lo, hi = SPEC[cc_map[msg.control]][1], SPEC[cc_map[msg.control]][2]
                                self.set(cc_map[msg.control], lo + (hi - lo) * (msg.value / 127.0))

                    threading.Thread(target=run_midi, daemon=True).start()
                    self.notify(f"MIDI: listening on {names[0]}")
                except Exception as exc:  # noqa: BLE001
                    self.notify(f"MIDI error: {exc}")
