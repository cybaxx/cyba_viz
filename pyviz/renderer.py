"""ModernGL render engine: feedback-based hypnotic compositor.

Pipeline per frame (TouchDesigner-ish):

    prev feedback ──(decay/zoom/rotate/aberration)──▶ target
    sprites (stills from atlases + animated gif textures) ──▶ target
    target ──(kaleidoscope/hue/sat/vignette/grain)──▶ screen
    swap(target, feedback)

The ``Renderer`` is context-agnostic: construct it with any ``moderngl.Context``
and size, then call ``update(dt)`` / ``render(fbo)``. The interactive app passes
``ctx.screen``; the headless test passes an offscreen framebuffer.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import moderngl
import numpy as np
from PIL import Image

from .assets import Gif, Library, Still
from .control import Controls

FULLSCREEN_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() { v_uv = in_uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

SPRITE_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
in vec4 in_color;
out vec2 v_uv;
out vec4 v_color;
void main() { v_uv = in_uv; v_color = in_color; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

SPRITE_FRAG = """
#version 330
uniform sampler2D tex;
in vec2 v_uv;
in vec4 v_color;
out vec4 f_color;
void main() {
    vec4 t = texture(tex, clamp(v_uv, 0.0, 1.0));
    f_color = vec4(t.rgb * v_color.rgb, t.a * v_color.a);
}
"""

FEEDBACK_FRAG = """
#version 330
uniform sampler2D prev;
uniform vec2 res;
uniform float decay;
uniform float zoom;
uniform float rot;
uniform float aberr;
uniform float displace;
uniform float disp_rgb;
uniform float disp_x;
uniform float disp_y;
uniform float disp_detail;
in vec2 v_uv;
out vec4 f_color;

const vec3 LUMA = vec3(0.299, 0.587, 0.114);

vec3 dsp(vec2 uv) { return texture(prev, clamp(uv, 0.0, 1.0)).rgb; }

void main() {
    vec2 p = v_uv - 0.5;
    float c = cos(rot), s = sin(rot);
    p = mat2(c, -s, s, c) * p;
    vec2 uv = p / zoom + 0.5;

    vec2 shift = vec2(0.0);
    if (displace > 0.0001) {
        vec2 tx = vec2(disp_detail) / res;
        float lx = dot(dsp(uv + vec2(tx.x, 0.0)), LUMA)
                 - dot(dsp(uv - vec2(tx.x, 0.0)), LUMA);
        float ly = dot(dsp(uv + vec2(0.0, tx.y)), LUMA)
                 - dot(dsp(uv - vec2(0.0, tx.y)), LUMA);
        vec2 grad = vec2(lx, ly) * 8.0;
        vec2 rgbv = dsp(uv).rg * 2.0 - 1.0;
        shift = mix(grad, rgbv, disp_rgb) * displace * vec2(disp_x, disp_y);
    }
    vec2 uv2 = clamp(uv + shift, 0.0, 1.0);

    vec3 col;
    if (aberr > 0.0001) {
        vec2 r = clamp((p * (1.0 + aberr)) / zoom + 0.5 + shift, 0.0, 1.0);
        vec2 b = clamp((p * (1.0 - aberr)) / zoom + 0.5 + shift, 0.0, 1.0);
        col = vec3(texture(prev, r).r, texture(prev, uv2).g, texture(prev, b).b);
    } else {
        col = texture(prev, uv2).rgb;
    }
    f_color = vec4(col * decay, 1.0);
}
"""

POST_FRAG = """
#version 330
uniform sampler2D tex;
uniform vec2 res;
uniform float time;
uniform float hue;
uniform float sat;
uniform float contrast;
uniform float kaleido;
uniform float vignette;
uniform float grain;
uniform float blackout;
uniform float exposure;
uniform float zoom;
in vec2 v_uv;
out vec4 f_color;

vec3 hue_rotate(vec3 c, float a) {
    const vec3 k = vec3(0.57735027);
    float ca = cos(a);
    return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);
}

vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec2 uv = v_uv;
    if (kaleido >= 2.0) {
        vec2 p = uv - 0.5;
        float r = length(p) * 1.41421356;
        float ang = atan(p.y, p.x);
        float seg = 6.28318530718 / kaleido;
        ang = mod(ang, seg);
        ang = abs(ang - seg * 0.5);
        p = vec2(cos(ang), sin(ang)) * r * 0.70710678;
        uv = p + 0.5;
    }
    uv = (uv - 0.5) / zoom + 0.5;
    vec3 c = texture(tex, clamp(uv, 0.0, 1.0)).rgb * exposure;
    c = aces(c);
    c = mix(vec3(dot(c, vec3(0.299, 0.587, 0.114))), c, sat);
    c = (c - 0.5) * contrast + 0.5;
    c = hue_rotate(c, hue);
    vec2 q = v_uv - 0.5;
    float v = smoothstep(0.95, 0.15, length(q));
    c *= mix(1.0, v, clamp(vignette, 0.0, 1.2));
    float n = fract(sin(dot(v_uv * res, vec2(12.9898, 78.233)) + time) * 43758.5453);
    c += (n - 0.5) * grain;
    c *= (1.0 - blackout);
    f_color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

OVERLAY_FRAG = """
#version 330
uniform sampler2D tex;
in vec2 v_uv;
out vec4 f_color;
void main() { f_color = texture(tex, v_uv); }
"""


@dataclass
class Sprite:
    still: Still | None = None
    gif: Gif | None = None
    atlas_index: int = 0
    uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    gif_slot: int = -1
    aspect: float = 1.0
    pos: tuple[float, float] = (0.0, 0.0)
    half: tuple[float, float] = (0.4, 0.4)
    rot: float = 0.0
    rot_speed: float = 0.0
    vel: tuple[float, float] = (0.0, 0.0)
    age: float = 0.0
    lifetime: float = 6.0
    blend_add: bool = True
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
    pulse_rate: float = 0.0
    phase: float = 0.0
    scale: float = 1.0
    spiral: bool = False
    depth: float = 0.0
    arm: int = 0
    own_rot: float = 0.0

    def alpha(self, t: float) -> float:
        fade = min(1.2, max(0.15, self.lifetime * 0.2))
        if self.age < fade:
            a = self.age / fade
        elif self.age > self.lifetime - fade:
            a = max(0.0, (self.lifetime - self.age) / fade)
        else:
            a = 1.0
        a *= 0.72 + 0.28 * math.sin(2 * math.pi * (self.age * self.pulse_rate + self.phase))
        return max(0.0, min(1.0, a))


def build_atlas(ctx: moderngl.Context, stills: list[Still], cell: int, atlas_size: int):
    cols = max(1, atlas_size // cell)
    canvas = Image.new("RGBA", (atlas_size, atlas_size), (0, 0, 0, 0))
    entries: dict[str, tuple[float, float, float, float]] = {}
    for i, still in enumerate(stills[: cols * cols]):
        try:
            img = Image.open(still.file).convert("RGBA")
        except Exception:
            continue
        scale = min(cell / img.width, cell / img.height)
        nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
        cx, cy = (i % cols) * cell, (i // cols) * cell
        ox, oy = cx + (cell - nw) // 2, cy + (cell - nh) // 2
        canvas.paste(img, (ox, oy), img)
        inset = 0.5 / atlas_size
        u0 = ox / atlas_size + inset
        v0 = oy / atlas_size + inset
        du = (nw / atlas_size) - 2 * inset
        dv = (nh / atlas_size) - 2 * inset
        entries[still.id] = (u0, v0, du, dv)
    tex = ctx.texture((atlas_size, atlas_size), 4, canvas.tobytes())
    tex.build_mipmaps()
    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
    tex.repeat_x = tex.repeat_y = False
    tex.anisotropy = 8.0
    return tex, entries


class GifPool:
    """A fixed pool of animated-gif textures, advanced only while in use."""

    def __init__(self, ctx: moderngl.Context, size: int, edge: int = 768) -> None:
        self.ctx = ctx
        self.size = size
        self.edge = edge
        self.slots: list[dict | None] = [None] * size

    def find(self, gif: Gif) -> int | None:
        for i, s in enumerate(self.slots):
            if s and s["gif"].id == gif.id:
                return i
        return None

    def acquire(self, gif: Gif, in_use: set[int]) -> int | None:
        idx = self.find(gif)
        if idx is not None:
            return idx
        free = [i for i in range(self.size) if i not in in_use]
        if not free:
            return None
        idx = free[0]
        self._load(idx, gif)
        return idx

    def _load(self, idx: int, gif: Gif) -> None:
        scale = min(1.0, self.edge / max(gif.w, gif.h))
        w, h = max(1, int(gif.w * scale)), max(1, int(gif.h * scale))
        tex = self.ctx.texture((w, h), 4)
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        self.slots[idx] = {
            "gif": gif, "tex": tex, "w": w, "h": h,
            "time": random.random() * 10, "frame": -1,
        }

    def update(self, dt: float, active: set[int]) -> None:
        for i, slot in enumerate(self.slots):
            if slot is None or i not in active:
                continue
            slot["time"] += dt
            frames = slot["gif"].frames
            n = len(frames)
            if n == 0:
                continue
            frame = int(slot["time"] * slot["gif"].fps) % n
            if frame == slot["frame"]:
                continue
            try:
                img = Image.open(frames[frame]).convert("RGBA").resize(
                    (slot["w"], slot["h"]), Image.BILINEAR
                )
            except Exception:
                continue
            slot["tex"].write(img.tobytes())
            slot["frame"] = frame

    def texture(self, idx: int):
        slot = self.slots[idx]
        return slot["tex"] if slot else None


class Renderer:
    def __init__(
        self,
        ctx: moderngl.Context,
        size: tuple[int, int],
        library: Library,
        controls: Controls,
        atlas_size: int = 4096,
        cell: int = 1024,
        atlases: int = 4,
        gif_slots: int = 12,
        gif_edge: int = 768,
        seed: int = 0,
    ) -> None:
        self.ctx = ctx
        self.width, self.height = size
        self.lib = library
        self.controls = controls
        self.rng = random.Random(seed)
        self.time = 0.0
        self.hue = 0.0
        self.atlas_size = atlas_size
        self.cell = cell
        self.n_atlases = atlases

        # spiral tunnel geometry (radius = r_min * exp(k * depth))
        self.r_min = 0.035
        self.r_max = 2.4
        self.spiral_k = 0.55
        self.depth_max = math.log(self.r_max / self.r_min) / self.spiral_k
        self._spiral_active: bool | None = None

        self.sprite_prog = ctx.program(vertex_shader=SPRITE_VERT, fragment_shader=SPRITE_FRAG)
        self.feedback_prog = ctx.program(vertex_shader=FULLSCREEN_VERT, fragment_shader=FEEDBACK_FRAG)
        self.post_prog = ctx.program(vertex_shader=FULLSCREEN_VERT, fragment_shader=POST_FRAG)

        verts = np.array([
            -1, -1, 0, 0,
             1, -1, 1, 0,
            -1,  1, 0, 1,
             1,  1, 1, 1,
        ], dtype="f4")
        self.full_vbo = ctx.buffer(verts.tobytes())
        fmt = [(self.full_vbo, "2f 2f", "in_pos", "in_uv")]
        self.feedback_vao = ctx.vertex_array(self.feedback_prog, fmt)
        self.post_vao = ctx.vertex_array(self.post_prog, fmt)
        self.overlay_prog = ctx.program(vertex_shader=FULLSCREEN_VERT, fragment_shader=OVERLAY_FRAG)
        self.overlay_vao = ctx.vertex_array(self.overlay_prog, fmt)

        self._quad_cache: dict = {}
        self.sprites: list[Sprite] = []
        self.gif_pool = GifPool(ctx, gif_slots, gif_edge)

        self._build_atlases()
        self._make_targets()
        self._ensure_density(force=True)

    # -- atlases -----------------------------------------------------------
    def _build_atlases(self) -> None:
        per = max(1, (self.atlas_size // self.cell) ** 2)
        pool = list(self.lib.stills)
        self.rng.shuffle(pool)
        self.atlases: list = []
        self.still_lookup: dict[str, tuple[int, tuple[float, float, float, float]]] = {}
        for k in range(self.n_atlases):
            chunk = pool[k * per : (k + 1) * per]
            if not chunk:
                break
            tex, entries = build_atlas(self.ctx, chunk, self.cell, self.atlas_size)
            self.atlases.append(tex)
            for sid, uv in entries.items():
                self.still_lookup[sid] = (k, uv)
        if not self.atlases:
            raise RuntimeError("no still images available to build an atlas")
        self.resident_stills = [s for s in self.lib.stills if s.id in self.still_lookup]
        if not self.resident_stills and not self.lib.gifs:
            raise RuntimeError("no usable stills in atlas and no gifs")

    def reshuffle(self) -> None:
        old = [s for s in self.sprites if s.still is not None]
        for t in self.atlases:
            t.release()
        self._build_atlases()
        self.sprites = [s for s in self.sprites if s.still is None]

    # -- targets -----------------------------------------------------------
    def _make_targets(self) -> None:
        self.tex_a = self.ctx.texture((self.width, self.height), 4, dtype="f2")
        self.tex_b = self.ctx.texture((self.width, self.height), 4, dtype="f2")
        for t in (self.tex_a, self.tex_b):
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = False
        self.fbo_a = self.ctx.framebuffer(color_attachments=[self.tex_a])
        self.fbo_b = self.ctx.framebuffer(color_attachments=[self.tex_b])
        self.feedback_fbo, self.target_fbo = self.fbo_a, self.fbo_b
        for f in (self.fbo_a, self.fbo_b):
            f.use()
            self.ctx.clear(0.0, 0.0, 0.0, 0.0)

    def resize(self, size: tuple[int, int]) -> None:
        self.width, self.height = size
        for t in (self.tex_a, self.tex_b):
            t.release()
        for f in (self.fbo_a, self.fbo_b):
            f.release()
        self._make_targets()

    # -- spawning ----------------------------------------------------------
    def _target_count(self) -> int:
        c = self.controls
        if c["spiral"] > 0.5:
            return max(4, int(c["spiral_count"] * c["density"]))
        return max(1, int(c["density"] * 24))

    def _ensure_density(self, force: bool = False) -> None:
        target = self._target_count()
        guard = 0
        while len(self.sprites) < target and guard < target * 2 + 16:
            guard += 1
            if self._spawn() is None:
                break

    def _pick_media(self):
        c = self.controls
        if self.lib.gifs and self.rng.random() < c["p_gif"]:
            cand = self.lib.pick_gif()
            in_use = {s.gif_slot for s in self.sprites if s.gif_slot >= 0}
            slot = self.gif_pool.acquire(cand, in_use)
            if slot is not None:
                return cand, None, 0, (0.0, 0.0, 1.0, 1.0), slot
        if not self.resident_stills:
            return None, None, 0, (0.0, 0.0, 1.0, 1.0), -1
        still = self.rng.choice(self.resident_stills)
        atlas_index, uv = self.still_lookup[still.id]
        return None, still, atlas_index, uv, -1

    def _tint_for(self, media) -> tuple[float, float, float]:
        if self.rng.random() < 0.25:
            base = np.array(media.avg_color, dtype="f4") / 255.0
            boosted = 0.5 + base * 1.6
            return tuple(float(min(1.6, v)) for v in boosted)
        return (1.0, 1.0, 1.0)

    def _watch(self, s: Sprite, gif, still, atlas_index, uv, gif_slot, media) -> None:
        s.still, s.gif = still, gif
        s.atlas_index, s.uv, s.gif_slot = atlas_index, uv, gif_slot
        s.aspect = media.aspect
        s.tint = self._tint_for(media)

    def _spawn(self) -> Sprite | None:
        c = self.controls
        gif, still, atlas_index, uv, gif_slot = self._pick_media()
        if gif is None and still is None:
            return None
        media = gif if gif is not None else still
        sprite = Sprite(
            still=still, gif=gif, atlas_index=atlas_index, uv=uv, gif_slot=gif_slot,
            aspect=media.aspect, tint=self._tint_for(media),
            pulse_rate=self.rng.uniform(0.15, 0.8), phase=self.rng.uniform(0, 1),
            blend_add=self.rng.random() < (c["bloom"] / 2.0),
        )
        if c["spiral"] > 0.5:
            sprite.spiral = True
            sprite.depth = self.rng.uniform(0.0, self.depth_max)
            sprite.arm = self.rng.randrange(max(1, int(round(c["spiral_arms"]))))
            sprite.own_rot = self.rng.uniform(-0.4, 0.4)
        else:
            size = self.rng.uniform(c["size_min"], c["size_max"])
            longest = size * 2.0
            a = sprite.aspect
            if a >= 1.0:
                sprite.half = (longest * 0.5, longest / a * 0.5)
            else:
                sprite.half = (longest * a * 0.5, longest * 0.5)
            sprite.pos = (self.rng.uniform(-1.0, 1.0), self.rng.uniform(-1.0, 1.0))
            sprite.rot = self.rng.uniform(0, 2 * math.pi)
            sprite.rot_speed = c["sprite_rot"] * self.rng.uniform(-1.0, 1.0)
            drift = c["drift"] * self.rng.uniform(0.05, 0.35)
            ang = self.rng.uniform(0, 2 * math.pi)
            sprite.vel = (math.cos(ang) * drift, math.sin(ang) * drift)
            sprite.lifetime = c["lifetime"] * self.rng.uniform(0.6, 1.4)
        self.sprites.append(sprite)
        return sprite

    def _respawn_spiral(self, s: Sprite) -> None:
        c = self.controls
        gif, still, atlas_index, uv, gif_slot = self._pick_media()
        if gif is None and still is None:
            return
        media = gif if gif is not None else still
        self._watch(s, gif, still, atlas_index, uv, gif_slot, media)
        s.depth = self.rng.uniform(0.0, 0.25)
        s.arm = self.rng.randrange(max(1, int(round(c["spiral_arms"]))))
        s.own_rot = self.rng.uniform(-0.4, 0.4)
        s.phase = self.rng.uniform(0, 1)

    # -- update ------------------------------------------------------------
    def update(self, dt: float) -> None:
        c = self.controls
        c.update(dt)
        audio = getattr(c, "audio", None)
        if c["audio_sync"] > 0.5 and audio is not None and audio.ready:
            audio.gain = c["audio_gain"]
            if audio.bpm > 0:
                c.set("bpm", audio.bpm, "mod")
        speed = c["speed"]
        if c["bpm_sync"] > 0.5:
            speed *= 1.0 + 0.5 * c.clock.beat_pulse(self.time)
        dt_s = dt * speed
        self.time += dt_s
        self.hue = (self.hue + c["hue_speed"] * dt_s * 6.28318530718) % 6.28318530718

        want_spiral = c["spiral"] > 0.5
        if want_spiral != self._spiral_active:
            self._spiral_active = want_spiral
            self.sprites = []
            self._ensure_density()

        if want_spiral:
            for s in self.sprites:
                s.depth += c["spiral_speed"] * dt_s
                if self.r_min * math.exp(self.spiral_k * s.depth) > self.r_max:
                    self._respawn_spiral(s)
        else:
            i = 0
            while i < len(self.sprites):
                s = self.sprites[i]
                s.age += dt_s
                s.pos = (s.pos[0] + s.vel[0] * dt_s, s.pos[1] + s.vel[1] * dt_s)
                s.rot += s.rot_speed * dt_s
                if s.age >= s.lifetime:
                    self.sprites.pop(i)
                    continue
                i += 1

        self._ensure_density()
        active = {s.gif_slot for s in self.sprites if s.gif_slot >= 0}
        self.gif_pool.update(dt_s, active)

    # -- draw --------------------------------------------------------------
    def _spiral_geom(self, s: Sprite):
        c = self.controls
        arms = max(1, int(round(c["spiral_arms"])))
        r = self.r_min * math.exp(self.spiral_k * s.depth)
        theta = s.depth * c["spiral_twist"] + s.arm * (2 * math.pi / arms) + self.time * c["spiral_rot"]
        asp = self.height / self.width if self.width else 1.0
        cx = r * math.cos(theta) * asp
        cy = r * math.sin(theta)
        size = r * c["spiral_size"]
        a = s.aspect
        if a >= 1.0:
            half = (size * 0.5, size / a * 0.5)
        else:
            half = (size * a * 0.5, size * 0.5)
        fade_in = min(1.0, max(0.0, (r - self.r_min) / (self.r_min * 1.5)))
        fade_out = min(1.0, max(0.0, (self.r_max - r) / (self.r_max * 0.3)))
        pulse = 0.8 + 0.2 * math.sin(2 * math.pi * (self.time * s.pulse_rate + s.phase))
        return (cx, cy), half, theta + s.own_rot, fade_in * fade_out * pulse

    def _quad(self, s: Sprite) -> np.ndarray:
        if s.spiral:
            pos, half, rot, a = self._spiral_geom(s)
        else:
            pos, half, rot, a = s.pos, s.half, s.rot, s.alpha(self.time)
        if s.blend_add:
            a *= 0.7
        if a <= 0.001:
            return np.empty((0,), dtype="f4")
        cs, sn = math.cos(rot), math.sin(rot)
        hx, hy = half
        u0, v0, du, dv = s.uv
        verts = []
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        for sx, sy in corners:
            x, y = sx * hx, sy * hy
            px = pos[0] + x * cs - y * sn
            py = pos[1] + x * sn + y * cs
            u = u0 + (sx * 0.5 + 0.5) * du
            v = v0 + (0.5 - sy * 0.5) * dv
            verts.append((px, py, u, v, s.tint[0], s.tint[1], s.tint[2], a))
        order = [0, 1, 2, 0, 2, 3]
        return np.array([verts[j] for j in order], dtype="f4").ravel()

    def _draw_bucket(self, key, data: np.ndarray, texture, additive: bool) -> None:
        count = data.size // 8
        if count == 0:
            return
        cache_key = (key, additive, texture.glo)
        entry = self._quad_cache.get(cache_key)
        nbytes = data.nbytes
        if entry is None or entry[2] < nbytes:
            if entry is not None:
                entry[0].release()
                entry[1].release()
            vbo = self.ctx.buffer(reserve=nbytes)
            vao = self.ctx.vertex_array(
                self.sprite_prog, [(vbo, "2f 2f 4f", "in_pos", "in_uv", "in_color")]
            )
            entry = (vbo, vao, nbytes)
            self._quad_cache[cache_key] = entry
        vbo, vao, _ = entry
        vbo.orphan(nbytes)
        vbo.write(data.tobytes())
        texture.use(0)
        self.sprite_prog["tex"].value = 0
        if additive:
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE)
        else:
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        vao.render(moderngl.TRIANGLES, vertices=count)

    def render(self, target=None) -> None:
        c = self.controls
        target = target if target is not None else self.ctx.screen

        bounce_pulse = 0.0
        if c["bounce"] > 0.5:
            audio = getattr(c, "audio", None)
            if (c["audio_sync"] > 0.5 and audio is not None and audio.ready
                    and audio.bpm > 0):
                bounce_pulse = audio.kick_pulse(time.monotonic())
            else:
                bounce_pulse = c.clock.beat_pulse(self.time, max(1.0, c["bounce_decay"]))
        zoom_punch = c["bounce_amount"] * bounce_pulse

        self.target_fbo.use()
        self.ctx.clear(0.0, 0.0, 0.0, 0.0)

        self.ctx.disable(moderngl.BLEND)
        prev_tex = self.feedback_fbo.color_attachments[0]
        self.feedback_prog["prev"].value = 0
        self.feedback_prog["res"].value = (float(self.width), float(self.height))
        self.feedback_prog["decay"].value = c["decay"]
        self.feedback_prog["zoom"].value = c["zoom"] * (1.0 + zoom_punch * c["bounce_fb"])
        self.feedback_prog["rot"].value = c["feed_rot"]
        self.feedback_prog["aberr"].value = c["aberr"]
        self.feedback_prog["displace"].value = c["displace"]
        self.feedback_prog["disp_rgb"].value = c["disp_rgb"]
        self.feedback_prog["disp_x"].value = c["disp_x"]
        self.feedback_prog["disp_y"].value = c["disp_y"]
        self.feedback_prog["disp_detail"].value = c["disp_detail"]
        prev_tex.use(0)
        self.feedback_vao.render(moderngl.TRIANGLE_STRIP)

        self.ctx.enable(moderngl.BLEND)
        buckets: dict = {}
        for s in self.sprites:
            data = self._quad(s)
            if data.size == 0:
                continue
            if s.gif is not None and s.gif_slot >= 0:
                key = ("gif", s.gif_slot)
            else:
                key = ("atlas", s.atlas_index)
            buckets.setdefault((key, s.blend_add), []).append(data)

        for (key, additive), chunks in buckets.items():
            data = np.concatenate(chunks)
            if key[0] == "gif":
                tex = self.gif_pool.texture(key[1])
            else:
                tex = self.atlases[key[1]]
            if tex is None:
                continue
            self._draw_bucket(key, data, tex, additive)

        new_tex = self.target_fbo.color_attachments[0]
        self.feedback_fbo, self.target_fbo = self.target_fbo, self.feedback_fbo

        target.use()
        self.ctx.disable(moderngl.BLEND)
        self.post_prog["tex"].value = 0
        self.post_prog["res"].value = (float(self.width), float(self.height))
        self.post_prog["time"].value = self.time
        self.post_prog["hue"].value = self.hue + c["hue"] * 6.28318530718
        self.post_prog["sat"].value = c["sat"]
        self.post_prog["contrast"].value = c["contrast"]
        self.post_prog["kaleido"].value = c["kaleido"]
        self.post_prog["vignette"].value = c["vignette"]
        self.post_prog["grain"].value = c["grain"]
        self.post_prog["blackout"].value = c["fade_to_black"]
        self.post_prog["exposure"].value = c["exposure"]
        self.post_prog["zoom"].value = 1.0 + zoom_punch
        new_tex.use(0)
        self.post_vao.render(moderngl.TRIANGLE_STRIP)

    def draw_overlay(self, texture) -> None:
        if texture is None:
            return
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        self.overlay_prog["tex"].value = 0
        texture.use(0)
        self.overlay_vao.render(moderngl.TRIANGLE_STRIP)
        self.ctx.disable(moderngl.BLEND)

    def release(self) -> None:
        for vbo, vao, _ in self._quad_cache.values():
            vbo.release()
            vao.release()
        for t in self.atlases:
            t.release()
        for t in (self.tex_a, self.tex_b):
            t.release()
        for f in (self.fbo_a, self.fbo_b):
            f.release()
