"""Hypnotic Tumblr visualizer — entry point.

Interactive (opens a window):
    python run.py auroraangelic

Headless smoke test (renders frames to PNG, no window needed):
    python run.py auroraangelic --headless --frames 240 --out frame.png

Controls (interactive):
    space pause       f fullscreen       q/esc quit       i settings+keymap HUD
    h hue +30         shift+h hue -30    u hue drift      k kaleidoscope
    b bloom bias      n reshuffle        t tap tempo      shift+r reset
    g gif chance      s spiral mode      up/down density  left/right decay
    d displace level  [/] spiral twist   ,/. tunnel speed
    r random mode     c cycle mode       x bounce mode    z bounce amount
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pyviz.assets import load_library
from pyviz.control import Controls, SPEC
from pyviz.hud import Hud
from pyviz.renderer import Renderer


def build_renderer(ctx, size, lib, controls, args):
    return Renderer(
        ctx,
        size,
        lib,
        controls,
        atlas_size=args.atlas_size,
        cell=args.cell,
        atlases=args.atlases,
        gif_slots=args.gif_slots,
        gif_edge=args.gif_edge,
        seed=args.seed,
    )


def setup_audio(controls, args) -> bool:
    if controls.audio is not None:
        return True
    try:
        from pyviz.audio import KickSync
    except Exception as exc:  # noqa: BLE001
        controls.notify(f"audio unavailable ({exc})")
        return False
    device = args.audio_device
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    band = tuple(args.audio_band) if args.audio_band else (35.0, 160.0)
    ks = KickSync(device=device, band=band)
    try:
        ks.start()
    except Exception as exc:  # noqa: BLE001
        controls.notify(f"mic failed: {exc}")
        return False
    controls.audio = ks
    controls.notify("mic listening for kicks")
    return True


def run_headless(args, lib, controls) -> int:
    import moderngl
    from PIL import Image

    ctx = moderngl.create_standalone_context(require=330)
    w, h = args.width, args.height
    renderer = build_renderer(ctx, (w, h), lib, controls, args)

    out_tex = ctx.texture((w, h), 4)
    out_fbo = ctx.framebuffer(color_attachments=[out_tex])
    frames = args.frames
    dt = 1.0 / 60.0
    print(f"headless: rendering {frames} frames at {w}x{h}")
    print(f"  library: {len(lib.stills)} stills, {len(lib.gifs)} gifs")
    for i in range(frames):
        controls.set("kaleido", 0.0 if i < frames * 0.3 else 6.0)
        renderer.update(dt)
        renderer.render(target=out_fbo)
    ctx.finish()
    data = out_fbo.read(components=4, dtype="f1")
    img = Image.frombytes("RGBA", (w, h), data).transpose(Image.FLIP_TOP_BOTTOM)
    out = Path(args.out)
    img.convert("RGB").save(out)
    print(f"  wrote {out} ({img.width}x{img.height})")
    print(f"  sprites alive: {len(renderer.sprites)}")
    return 0


def run_window(args, lib, controls) -> int:
    import moderngl
    import pyglet
    from pyglet.window import key

    pyglet.options["shadow_window"] = False
    cfg = pyglet.gl.Config(double_buffer=True, depth_size=24, sample_buffers=1, samples=4)
    try:
        window = pyglet.window.Window(
            width=args.width, height=args.height, caption="tumblr visualizer",
            config=cfg, vsync=True, fullscreen=args.fullscreen, resizable=True,
        )
    except Exception:
        window = pyglet.window.Window(
            width=args.width, height=args.height, caption="tumblr visualizer",
            vsync=True, fullscreen=args.fullscreen, resizable=True,
        )
    window.switch_to()
    ctx = moderngl.create_context()

    def framebuffer_size() -> tuple[int, int]:
        try:
            w, h = window.get_framebuffer_size()
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
        return max(1, window.width), max(1, window.height)

    def render_size(fb: tuple[int, int]) -> tuple[int, int]:
        s = args.render_scale
        if s >= 1.0:
            return fb
        return max(1, int(fb[0] * s)), max(1, int(fb[1] * s))

    renderer = build_renderer(ctx, render_size(framebuffer_size()), lib, controls, args)
    hud = Hud(ctx)

    controls.start_listeners(osc_port=args.osc_port, midi=args.midi, osc_host=args.osc_host)
    if args.audio and setup_audio(controls, args):
        controls.set("audio_sync", 1.0)

    state = {"last": time.monotonic(), "fps": 0.0, "acc": 0.0, "n": 0}
    hue_levels = [0.0, 0.06, 0.15, -0.06]
    kal_levels = [0.0, 4.0, 6.0, 8.0, 12.0]
    bloom_levels = [0.4, 0.8, 1.4, 2.0]
    gif_levels = [0.0, 0.35, 0.6]
    disp_levels = [0.0, 0.02, 0.04, 0.08, 0.15]
    bounce_levels = [0.05, 0.10, 0.15, 0.25, 0.35, 0.5]
    st = {"hue": 0, "kal": 0, "bloom": 1, "gif": 1, "disp": 0, "bounce": 2}

    def sync_viewport() -> None:
        fb = framebuffer_size()
        rs = render_size(fb)
        if rs != (renderer.width, renderer.height):
            renderer.resize(rs)
        ctx.viewport = (0, 0, fb[0], fb[1])

    @window.event
    def on_draw():
        now = time.monotonic()
        dt = min(0.05, now - state["last"])
        state["last"] = now
        if not controls.paused:
            renderer.update(dt)
        sync_viewport()
        renderer.render(target=ctx.screen)
        if hud.visible:
            hud.refresh(controls, state["fps"], framebuffer_size(), renderer.time)
            renderer.draw_overlay(hud.texture)
        state["acc"] += dt
        state["n"] += 1
        if state["acc"] >= 0.5:
            state["fps"] = state["n"] / state["acc"]
            state["acc"] = 0.0
            state["n"] = 0
            window.set_caption(
                f"tumblr visualizer  |  {state['fps']:.0f}fps  |  {controls.describe()}"
            )

    @window.event
    def on_resize(width, height):
        if width > 0 and height > 0:
            sync_viewport()

    @window.event
    def on_key_press(symbol, modifiers):
        c = controls
        if symbol in (key.ESCAPE, key.Q):
            window.close()
        elif symbol == key.SPACE:
            c.paused = not c.paused
            c.notify("paused" if c.paused else "playing")
        elif symbol == key.F:
            window.set_fullscreen(not window.fullscreen)
        elif symbol == key.I:
            hud.toggle()
            c.notify("HUD on" if hud.visible else "HUD off")
        elif symbol == key.H:
            step = -1.0 if (modifiers & key.MOD_SHIFT) else 1.0
            c.nudge("hue", step / 12.0)
            c.notify(f"hue {c['hue'] * 360:.0f} deg")
        elif symbol == key.U:
            st["hue"] = (st["hue"] + 1) % len(hue_levels)
            c.set("hue_speed", hue_levels[st["hue"]])
            c.notify(f"hue drift {hue_levels[st['hue']]}")
        elif symbol == key.K:
            st["kal"] = (st["kal"] + 1) % len(kal_levels)
            c.set("kaleido", kal_levels[st["kal"]])
            c.notify(f"kaleidoscope {int(kal_levels[st['kal']])}")
        elif symbol == key.B:
            st["bloom"] = (st["bloom"] + 1) % len(bloom_levels)
            c.set("bloom", bloom_levels[st["bloom"]])
            c.notify(f"bloom {bloom_levels[st['bloom']]}")
        elif symbol == key.G:
            st["gif"] = (st["gif"] + 1) % len(gif_levels)
            c.set("p_gif", gif_levels[st["gif"]])
            c.notify(f"gif chance {gif_levels[st['gif']]}")
        elif symbol == key.D:
            step = -1 if (modifiers & key.MOD_SHIFT) else 1
            st["disp"] = (st["disp"] + step) % len(disp_levels)
            c.set("displace", disp_levels[st["disp"]])
            c.notify(f"displace {disp_levels[st['disp']]:.3f}")
        elif symbol == key.N:
            renderer.reshuffle()
            c.notify("reshuffled atlases")
        elif symbol == key.S:
            new = 0.0 if c["spiral"] > 0.5 else 1.0
            c.set("spiral", new)
            c.notify("spiral tunnel ON" if new else "collage mode")
        elif symbol == key.BRACKETLEFT:
            c.nudge("spiral_twist", -0.2)
            c.notify(f"spiral twist {c['spiral_twist']:.2f}")
        elif symbol == key.BRACKETRIGHT:
            c.nudge("spiral_twist", 0.2)
            c.notify(f"spiral twist {c['spiral_twist']:.2f}")
        elif symbol == key.COMMA:
            c.nudge("spiral_speed", -0.1)
            c.notify(f"spiral speed {c['spiral_speed']:.2f}")
        elif symbol == key.PERIOD:
            c.nudge("spiral_speed", 0.1)
            c.notify(f"spiral speed {c['spiral_speed']:.2f}")
        elif symbol == key.R:
            if modifiers & key.MOD_SHIFT:
                for name, (default, _, _) in SPEC.items():
                    c.set(name, default)
                c.clear_overrides()
                c.notify("reset settings")
            else:
                on = c.modulator.toggle_random()
                c.notify("random mode ON" if on else "random mode OFF")
        elif symbol == key.C:
            on = c.modulator.toggle_cycle()
            c.notify("cycle mode ON" if on else "cycle mode OFF")
        elif symbol == key.T:
            c.clock.tap()
            c.set("bpm", c.clock.bpm)
            c.notify(f"bpm {c.clock.bpm:.1f}")
        elif symbol == key.X:
            on = 0.0 if c["bounce"] > 0.5 else 1.0
            c.set("bounce", on)
            c.notify(f"bounce {'ON' if on else 'OFF'} @ {c.clock.bpm:.0f} bpm")
        elif symbol == key.Z:
            st["bounce"] = (st["bounce"] + 1) % len(bounce_levels)
            c.set("bounce_amount", bounce_levels[st["bounce"]])
            c.notify(f"bounce amount {bounce_levels[st['bounce']] * 100:.0f}%")
        elif symbol == key.P:
            if modifiers & key.MOD_SHIFT:
                c.save_preset("rave")
            else:
                if not c.load_preset("rave"):
                    c.notify("no 'rave' preset yet - shift+p to save one")
        elif symbol == key.A:
            if c.audio is None:
                setup_audio(c, args)
            on = 0.0 if c["audio_sync"] > 0.5 else 1.0
            c.set("audio_sync", on)
            c.notify("mic kick-sync ON" if on else "mic kick-sync OFF")
        elif symbol == key.UP:
            c.nudge("density", 0.1)
            c.notify(f"density {c['density']:.2f}")
        elif symbol == key.DOWN:
            c.nudge("density", -0.1)
            c.notify(f"density {c['density']:.2f}")
        elif symbol == key.RIGHT:
            c.nudge("decay", 0.01)
            c.notify(f"decay {c['decay']:.3f}")
        elif symbol == key.LEFT:
            c.nudge("decay", -0.01)
            c.notify(f"decay {c['decay']:.3f}")

    print("tumblr visualizer running — press h for hue, k kaleidoscope, f fullscreen, q to quit")
    for m in controls.messages:
        print("  " + m)
    pyglet.app.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("blog")
    p.add_argument("--assets", help="assets dir (default ./assets/<blog>)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--render-scale", type=float, default=1.0,
                   help="internal render resolution vs display (e.g. 0.5 for 4K/5K displays)")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--atlas-size", type=int, default=4096)
    p.add_argument("--cell", type=int, default=1024)
    p.add_argument("--atlases", type=int, default=4)
    p.add_argument("--gif-slots", type=int, default=12)
    p.add_argument("--gif-edge", type=int, default=768)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--osc-port", type=int, default=9001)
    p.add_argument("--osc-host", default="127.0.0.1", help="OSC bind host (0.0.0.0 for LAN)")
    p.add_argument("--midi", action="store_true")
    p.add_argument("--spiral", action="store_true", help="start in infinite spiral-tunnel mode")
    p.add_argument("--bpm", type=float, default=None, help="start BPM (e.g. 128)")
    p.add_argument("--bounce", action="store_true", help="start with kick-drum bounce zoom on")
    p.add_argument("--preset", default=None, help="load a saved preset at startup (e.g. rave)")
    p.add_argument("--audio", action="store_true", help="enable mic kick/BPM sync")
    p.add_argument("--audio-device", default=None, help="input device index or name")
    p.add_argument("--audio-band", type=float, nargs=2, default=None, metavar=("LO", "HI"),
                   help="kick band in Hz (default 35 160)")
    p.add_argument("--list-audio", action="store_true", help="list input devices and exit")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--frames", type=int, default=240)
    p.add_argument("--out", default="frame.png")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_audio:
        from pyviz.audio import KickSync

        for line in KickSync.input_devices():
            print(line)
        return 0
    lib = load_library(args.blog, args.assets)
    if not lib.all:
        print("no media in manifest", file=sys.stderr)
        return 1
    controls = Controls()
    if getattr(args, "spiral", False):
        controls.set("spiral", 1.0)
    if getattr(args, "bpm", None):
        controls.set("bpm", args.bpm)
        controls.clock.bpm = args.bpm
    if getattr(args, "bounce", False):
        controls.set("bounce", 1.0)
    if getattr(args, "preset", None):
        if not controls.load_preset(args.preset):
            print(f"  preset '{args.preset}' not found", file=sys.stderr)
    if args.headless:
        return run_headless(args, lib, controls)
    return run_window(args, lib, controls)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
