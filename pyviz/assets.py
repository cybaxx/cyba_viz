"""Media library + manifest loading for the visualizer."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Still:
    id: str
    file: Path
    w: int
    h: int
    avg_color: tuple[int, int, int]

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 1.0


@dataclass
class Gif:
    id: str
    dir: Path
    frames: list[Path]
    fps: float
    w: int
    h: int
    avg_color: tuple[int, int, int]

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 1.0


@dataclass
class Library:
    blog: str
    manifest_path: Path
    assets_root: Path
    stills: list[Still] = field(default_factory=list)
    gifs: list[Gif] = field(default_factory=list)

    @property
    def all(self):
        return [*self.stills, *self.gifs]

    def gif_by_id(self, gid: str) -> Gif | None:
        for g in self.gifs:
            if g.id == gid:
                return g
        return None

    def pick_still(self) -> Still:
        return random.choice(self.stills)

    def pick_gif(self) -> Gif:
        return random.choice(self.gifs)

    def pick(self, p_gif: float):
        if self.gifs and random.random() < p_gif:
            return self.pick_gif()
        if self.stills:
            return self.pick_still()
        return random.choice(self.all)


def load_library(blog: str, assets_dir: str | Path | None = None) -> Library:
    here = Path(__file__).resolve().parent.parent  # visualizer/
    base = Path(assets_dir) if assets_dir else here / "assets"
    manifest_path = base / blog / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest not found: {manifest_path}\n"
            f"Run:  python visualizer/prep_assets.py {blog}"
        )
    data = json.loads(manifest_path.read_text())
    lib = Library(blog=blog, manifest_path=manifest_path, assets_root=base)
    for item in data["items"]:
        color = tuple(item.get("avg_color", (128, 128, 128)))
        if item["kind"] == "still":
            lib.stills.append(
                Still(
                    id=item["id"],
                    file=base / item["path"],
                    w=item["w"],
                    h=item["h"],
                    avg_color=color,
                )
            )
        elif item["kind"] == "gif":
            frame_dir = base / item["path"]
            lib.gifs.append(
                Gif(
                    id=item["id"],
                    dir=frame_dir,
                    frames=[frame_dir / f for f in item["frames"]],
                    fps=float(item.get("fps", 12.0)),
                    w=item["w"],
                    h=item["h"],
                    avg_color=color,
                )
            )
    return lib
