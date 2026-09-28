"""Microphone kick-drum detector for BPM sync.

Listens to the default input, measures energy in the low "kick" band
(~35-160 Hz) via an FFT per block, detects onsets using spectral-flux with an
adaptive threshold, and estimates BPM from inter-onset intervals (median,
folded into a musical range).

Also exposes ``kick_pulse(now)``: a decaying envelope that resets on every
detected kick, ideal for driving the beat-synced bounce zoom directly from the
music (so it stays phase-locked, not just tempo-locked).
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque

import numpy as np


class KickSync:
    def __init__(
        self,
        samplerate: int = 44100,
        blocksize: int = 2048,
        band: tuple[float, float] = (35.0, 160.0),
        device=None,
        threshold: float = 1.6,
        floor: float = 1e-5,
        refractory: float = 0.20,
        history: int = 16,
        pulse_width: float = 0.16,
    ) -> None:
        self.sr = samplerate
        self.blocksize = blocksize
        self.lo, self.hi = band
        self.device = device
        self.threshold = threshold
        self.floor = floor
        self.refractory = refractory
        self.pulse_width = pulse_width

        self.gain = 1.0
        self.ready = False
        self.bpm = 0.0
        self.level = 0.0
        self._onsets = 0

        self._window = np.hanning(blocksize).astype(np.float32)
        freqs = np.fft.rfftfreq(blocksize, 1.0 / samplerate)
        self._band = (freqs >= self.lo) & (freqs <= self.hi)

        self._ioi: deque[float] = deque(maxlen=history)
        self._prev = 0.0
        self._flux_avg = 1e-6
        self._last_onset = -1e9
        self._pulse_t = -1e9
        self._lock = threading.Lock()
        self._stream = None

    # -- analysis (also usable offline) ------------------------------------
    def band_energy(self, x: np.ndarray) -> float:
        if x.shape[0] != self.blocksize:
            x = np.resize(x, self.blocksize)
        spectrum = np.fft.rfft(x * self._window)
        power = spectrum.real ** 2 + spectrum.imag ** 2
        return float(power[self._band].sum()) / (self.blocksize ** 2)

    def push(self, x: np.ndarray, now: float) -> bool:
        """Feed one block; returns True if a kick onset was detected."""
        energy = self.band_energy(x) * self.gain
        flux = energy - self._prev
        self._prev = energy
        onset = False
        if flux > 0:
            if (flux > self._flux_avg * self.threshold and energy > self.floor
                    and (now - self._last_onset) > self.refractory):
                onset = True
                gap = now - self._last_onset
                if 0.25 <= gap <= 1.5:
                    self._ioi.append(gap)
                    self._update_bpm()
                self._last_onset = now
                self._pulse_t = now
                self._onsets += 1
        self._flux_avg = self._flux_avg * 0.97 + max(0.0, flux) * 0.03
        self.level = self.level * 0.8 + energy * 0.2
        return onset

    def _update_bpm(self) -> None:
        if len(self._ioi) < 4:
            return
        bpm = 60.0 / statistics.median(self._ioi)
        while bpm < 70.0:
            bpm *= 2.0
        while bpm > 190.0:
            bpm /= 2.0
        self.bpm = self.bpm * 0.7 + bpm * 0.3 if self.bpm else bpm

    # -- live input --------------------------------------------------------
    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.sr,
            blocksize=self.blocksize,
            callback=self._callback,
        )
        self._stream.start()
        self.ready = True

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            self.push(np.asarray(indata[:, 0], dtype=np.float32), time.monotonic())
        except Exception:
            pass

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self.ready = False

    # -- outputs -----------------------------------------------------------
    def kick_pulse(self, now: float) -> float:
        if self.bpm <= 0.0:
            return 0.0
        period = 60.0 / self.bpm
        tau = max(0.04, period * self.pulse_width)
        dt = now - self._pulse_t
        if dt < 0.0:
            return 0.0
        return float(np.exp(-dt / tau))

    @property
    def onset_count(self) -> int:
        return self._onsets

    @staticmethod
    def input_devices() -> list[str]:
        try:
            import sounddevice as sd

            names = []
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    names.append(f"{idx}: {dev['name']}")
            return names
        except Exception:
            return []
