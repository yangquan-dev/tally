#!/usr/bin/env python3
"""从 assets/app-logo.png 生成打包用图标。

用法:
  python scripts/make_icons.py

产出:
  assets/app.ico        — Windows 打包
  assets/app.icns       — macOS 打包（仅在 darwin 上生成）
  assets/app-icon-256.png — 窗口图标
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "app-logo.png"
ICO = ROOT / "assets" / "app.ico"
ICNS = ROOT / "assets" / "app.icns"
PNG256 = ROOT / "assets" / "app-icon-256.png"

ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
ICNS_SIZES = {
    "icon_16x16.png": 16,
    "diana.p@example.org": 32,
    "icon_32x32.png": 32,
    "ivan.p@example.net": 64,
    "icon_128x128.png": 128,
    "wendy.h@example.net": 256,
    "icon_256x256.png": 256,
    "wendy.h@example.net": 512,
    "icon_512x512.png": 512,
    "walt.e@example.net": 1024,
}


def _clear_black_outside_corners(img: Image.Image, threshold: int = 45) -> Image.Image:
    """将圆角卡片外近黑区域改为透明（从四角洪水填充）。"""
    img = img.copy()
    w, h = img.size
    px = img.load()

    def is_black(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        return a > 0 and r <= threshold and g <= threshold and b <= threshold

    visited: set[tuple[int, int]] = set()
    stack = [
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (5, 5),
        (w - 6, 5),
        (5, h - 6),
        (w - 6, h - 6),
    ]
    while stack:
        x, y = stack.pop()
        if (x, y) in visited or not (0 <= x < w and 0 <= y < h):
            continue
        visited.add((x, y))
        if not is_black(x, y):
            continue
        px[x, y] = (0, 0, 0, 0)
        stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
    return img


def _load_square_rgba(path: Path) -> Image.Image:
    if not path.is_file():
        raise SystemExit(f"源图不存在: {path}")
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return _clear_black_outside_corners(img)


def make_ico(src: Image.Image, dest: Path) -> None:
    # Pillow 以最大尺寸为基底，并按 sizes 生成多分辨率 ICO
    base = src.resize(ICO_SIZES[-1], Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    base.save(dest, format="ICO", sizes=ICO_SIZES)
    print(f"wrote {dest}")


def make_icns(src: Image.Image, dest: Path) -> None:
    if sys.platform != "darwin":
        print("skip .icns (requires macOS iconutil)")
        return
    if not shutil.which("iconutil"):
        raise SystemExit("未找到 iconutil，无法生成 .icns")

    with tempfile.TemporaryDirectory(prefix="tally-iconset-") as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for name, size in ICNS_SIZES.items():
            resized = src.resize((size, size), Image.Resampling.LANCZOS)
            resized.save(iconset / name, format="PNG")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        subprocess.check_call(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(dest)]
        )
    print(f"wrote {dest}")


def make_png256(src: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.resize((256, 256), Image.Resampling.LANCZOS).save(dest, format="PNG")
    print(f"wrote {dest}")


def main() -> None:
    src = _load_square_rgba(SRC)
    make_ico(src, ICO)
    make_icns(src, ICNS)
    make_png256(src, PNG256)


if __name__ == "__main__":
    main()
