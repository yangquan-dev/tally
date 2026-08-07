#!/usr/bin/env python3
"""本机构建当前平台的桌面应用。

用法:
  python scripts/build.py          # 自动识别当前系统
  python scripts/build.py mac      # 仅 macOS
  python scripts/build.py win      # 仅 Windows

说明:
  PyInstaller 不支持交叉编译。macOS 只能打 .app，Windows 只能打 .exe。
  若要一次产出双端产物，请打标签并推送（如 v1.0.0），
  触发 .github/workflows/build.yml 打包并发布 Release。
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _configure_stdio() -> None:
    """避免 Windows CI (cp1252) 打印中文时 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((message + "\n").encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()


def _run(cmd: list[str]) -> None:
    _print("+ " + " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def build_mac() -> Path:
    if sys.platform != "darwin":
        raise SystemExit("macOS build requires macOS")
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(ROOT / "build_macos.spec"),
        ]
    )
    app_path = ROOT / "dist" / "Tally.app"
    if not app_path.exists():
        raise SystemExit("dist/Tally.app not found")
    _print(f"macOS app built: {app_path}")
    return app_path


def build_win() -> Path:
    if sys.platform != "win32":
        raise SystemExit("Windows build requires Windows")
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(ROOT / "build_windows.spec"),
        ]
    )
    exe_path = ROOT / "dist" / "Tally" / "Tally.exe"
    if not exe_path.exists():
        raise SystemExit("dist/Tally/Tally.exe not found")
    _print(f"Windows app built: {exe_path}")
    return exe_path


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Build Tally desktop app")
    parser.add_argument(
        "target",
        nargs="?",
        choices=["auto", "mac", "win"],
        default="auto",
        help="build target (default: current OS)",
    )
    args = parser.parse_args()

    target = args.target
    if target == "auto":
        system = platform.system().lower()
        if system == "darwin":
            target = "mac"
        elif system == "windows":
            target = "win"
        else:
            raise SystemExit(f"unsupported OS: {platform.system()}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _print("Installing build dependencies...")
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(ROOT / "requirements-build.txt"),
            ]
        )

    dist = ROOT / "dist"
    build = ROOT / "build"
    if dist.exists():
        shutil.rmtree(dist)
    if build.exists():
        shutil.rmtree(build)

    if target == "mac":
        build_mac()
    else:
        build_win()


if __name__ == "__main__":
    main()
