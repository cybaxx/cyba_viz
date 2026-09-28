#!/usr/bin/env python3
"""Preprocess scraped Tumblr media into GPU-friendly tiles for the visualizer.

- Still images (jpg/png/webp): resize so the longest edge is capped at
  ``--max-edge`` (2560). Images smaller than ``--min-edge`` (1280) are left at
  native resolution (never upscaled). Output as WebP (alpha preserved).
- Animated GIFs: extract up to ``--max-gif-frames`` frames, evenly sampled,
  resized to ``--gif-edge``, written as individual WebP frames plus metadata.

Emits ``assets/<blog>/manifest.json`` describing every item, including an
average colour per item (handy for seeding tints / palette-driven effects).

Examples:
    python3 prep_assets.py auroraangelic
    python3 prep_assets.py auroraangelic --max-edge 1920 --workers 8
    python3 prep_assets.py auroraangelic --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageSequence, ImageOps

Image.MAX_IMAGE_PIXELS = None
STILL_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
GIF_EXTS = {".gif"}
SKIP_EXTS = {".mp4", ".webm", ".mov", ".mp3", ".m4a", ".ogg"}


def fit_longest(size: tuple[int, int], max_edge: int) -> tuple[int, int] | None:
    """Downscale so longest edge <= max_edge. None if no resize needed."""
    w, h = size
    longest = max(w, h)
    if longest <= max_edge:
        return None
    scale = max_edge / longest
    return (max(1, round(w * scale)), max(1, round(h * scale)))


def avg_color(img: Image.Image) -> list[int]:
    small = img.convert("RGBA").resize((1, 1), Image.BILINEAR)
    r, g, b, _ = small.getpixel((0, 0))
    return [int(r), int(g), int(b)]


def save_webp(img: Image.Image, dest: Path, quality: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "WEBP", quality=quality, method=4)


def process_still(src: Path, out_root: Path, args_dict: dict) -> dict | None:
    max_edge = args_dict["max_edge"]
    quality = args_dict["quality"]
    rel = src.relative_to(args_dict["media_root"])
    dest = out_root / "tiles" / rel.with_suffix(".webp")

    if not args_dict["force"] and dest.exists() and dest.stat().st_size > 0:
        pass
    else:
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                native = im.size
                new_size = fit_longest(native, max_edge)
                if new_size:
                    im = im.resize(new_size, Image.LANCZOS)
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
                save_webp(im, dest, quality)
        except Exception as exc:  # noqa: BLE001
            return {"src": str(src), "error": f"{type(exc).__name__}: {exc}"}

    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            native = im.size
            color = avg_color(im)
    except Exception as exc:  # noqa: BLE001
        return {"src": str(src), "error": f"{type(exc).__name__}: {exc}"}

    with Image.open(dest) as out:
        w, h = out.size
    return {
        "id": rel.with_suffix("").as_posix(),
        "kind": "still",
        "path": str(dest.relative_to(out_root.parent)),
        "w": w,
        "h": h,
        "src_w": native[0],
        "src_h": native[1],
        "avg_color": color,
        "bytes": dest.stat().st_size,
    }


def process_gif(src: Path, out_root: Path, args_dict: dict) -> dict | None:
    rel = src.relative_to(args_dict["media_root"])
    frame_dir = out_root / "gifs" / rel.with_suffix("")
    max_frames = args_dict["max_gif_frames"]
    gif_edge = args_dict["gif_edge"]
    quality = args_dict["quality"]
    force = args_dict["force"]

    done_marker = frame_dir / "_done.json"
    if not force and done_marker.exists():
        info = json.loads(done_marker.read_text())
        info["path"] = frame_dir.relative_to(out_root.parent).as_posix()
        return info

    frame_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as im:
            n_frames = getattr(im, "n_frames", 1)
            duration = im.info.get("duration", 100) or 100
            src_size = im.size
            indices = list(range(n_frames))
            if n_frames > max_frames:
                step = n_frames / max_frames
                indices = sorted({int(i * step) for i in range(max_frames)})
            saved = []
            color = [0, 0, 0]
            for out_idx, frame_idx in enumerate(indices):
                im.seek(frame_idx)
                frame = im.convert("RGBA")
                if out_idx == 0:
                    color = avg_color(frame)
                new_size = fit_longest(frame.size, gif_edge)
                if new_size:
                    frame = frame.resize(new_size, Image.LANCZOS)
                fp = frame_dir / f"{out_idx:04d}.webp"
                save_webp(frame, fp, quality)
                saved.append(fp.name)
            fps = 1000.0 / duration if duration else 10.0
    except Exception as exc:  # noqa: BLE001
        return {"src": str(src), "error": f"{type(exc).__name__}: {exc}"}

    with Image.open(frame_dir / saved[0]) as first:
        w, h = first.size
    info = {
        "id": rel.with_suffix("").as_posix(),
        "kind": "gif",
        "path": frame_dir.relative_to(out_root.parent).as_posix(),
        "frames": saved,
        "n_frames": len(saved),
        "src_frames": n_frames,
        "fps": round(fps, 2),
        "w": w,
        "h": h,
        "src_w": src_size[0],
        "src_h": src_size[1],
        "avg_color": color,
        "bytes": sum((frame_dir / f).stat().st_size for f in saved),
    }
    done_marker.write_text(json.dumps(info))
    return info


def worker(task: dict) -> dict | None:
    src = Path(task["src"])
    out_root = Path(task["out_root"])
    args_dict = task["args"]
    if src.suffix.lower() in GIF_EXTS:
        return process_gif(src, out_root, args_dict)
    if src.suffix.lower() in STILL_EXTS:
        return process_still(src, out_root, args_dict)
    return None


def collect_sources(media_root: Path) -> list[Path]:
    files = [p for p in media_root.rglob("*") if p.is_file()]
    return sorted(
        p for p in files if p.suffix.lower() in (STILL_EXTS | GIF_EXTS)
    )


def resolve_media_root(args, here: Path) -> Path:
    """Locate archives/<blog>/media whether the scraper repo is beside us or elsewhere."""
    if args.media_root:
        return Path(args.media_root).expanduser()
    blog = args.blog
    candidates: list[Path] = []
    env = os.environ.get("TUMBLR_ARCHIVE_DIR")
    if env:
        candidates.append(Path(env).expanduser() / blog / "media")
    candidates += [
        Path.cwd() / "archives" / blog / "media",
        here / "archives" / blog / "media",
        here.parent / "tumblr_photos" / "archives" / blog / "media",
        Path.home() / "git" / "tumblr_photos" / "archives" / blog / "media",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("blog", help="blog name (finds archives/<blog>/media)")
    parser.add_argument("--media-root", help="override source media directory")
    parser.add_argument("-o", "--out", help="output dir (default: ./assets/<blog>)")
    parser.add_argument("--max-edge", type=int, default=2560, help="max longest edge for stills")
    parser.add_argument("--min-edge", type=int, default=1280, help="informational lower bound (no upscaling)")
    parser.add_argument("--gif-edge", type=int, default=1280, help="max longest edge for GIF frames")
    parser.add_argument("--max-gif-frames", type=int, default=64, help="evenly sample GIFs down to this many frames")
    parser.add_argument("--quality", type=int, default=88, help="WebP quality")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    parser.add_argument("--force", action="store_true", help="reprocess already-processed files")
    args = parser.parse_args(argv)

    here = Path(__file__).resolve().parent
    out_root = Path(args.out) if args.out else here / "assets" / args.blog
    media_root = resolve_media_root(args, here)
    out_root.mkdir(parents=True, exist_ok=True)

    if not media_root.exists():
        print(f"source not found: {media_root}", file=sys.stderr)
        print("pass --media-root /path/to/archives/<blog>/media or set TUMBLR_ARCHIVE_DIR", file=sys.stderr)
        return 1

    sources = collect_sources(media_root)
    n_gif = sum(1 for p in sources if p.suffix.lower() in GIF_EXTS)
    print(f"source : {media_root}")
    print(f"output : {out_root}")
    print(f"files  : {len(sources)} ({n_gif} gifs, {len(sources) - n_gif} stills)")
    print(f"workers: {args.workers}, max-edge {args.max_edge}, gif-edge {args.gif_edge}\n")

    args_dict = {
        "max_edge": args.max_edge,
        "min_edge": args.min_edge,
        "gif_edge": args.gif_edge,
        "max_gif_frames": args.max_gif_frames,
        "quality": args.quality,
        "force": args.force,
        "media_root": media_root,
    }

    started = time.time()
    items: list[dict] = []
    errors: list[dict] = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(worker, {"src": str(p), "out_root": str(out_root), "args": args_dict})
            for p in sources
        ]
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if res is None:
                continue
            if "error" in res:
                errors.append(res)
            else:
                items.append(res)
            if done % 100 == 0 or done == len(sources):
                el = time.time() - started
                rate = done / el if el else 0
                print(f"  {done}/{len(sources)}  ({rate:.1f}/s, {el:.0f}s elapsed)")

    items.sort(key=lambda r: r["id"])
    total_bytes = sum(r["bytes"] for r in items)
    manifest = {
        "blog": args.blog,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(items),
        "stills": sum(1 for r in items if r["kind"] == "still"),
        "gifs": sum(1 for r in items if r["kind"] == "gif"),
        "total_bytes": total_bytes,
        "settings": {
            "max_edge": args.max_edge,
            "min_edge": args.min_edge,
            "gif_edge": args.gif_edge,
            "max_gif_frames": args.max_gif_frames,
            "quality": args.quality,
        },
        "items": items,
    }
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")))

    if errors:
        (out_root / "errors.json").write_text(json.dumps(errors, indent=2))

    print(f"\nDone in {time.time() - started:.0f}s")
    print(f"  items : {len(items)} ({manifest['stills']} stills, {manifest['gifs']} gifs)")
    print(f"  size  : {total_bytes / 1e9:.2f} GB")
    print(f"  errors: {len(errors)}")
    print(f"  manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
