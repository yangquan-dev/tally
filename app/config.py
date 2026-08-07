from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_APP_NAME = "本地记账"
BOOTSTRAP_APP_DIR = "Tally"


def is_frozen() -> bool:
    """是否为 PyInstaller 打包后的可执行程序。"""
    return bool(getattr(sys, "frozen", False))


def get_resource_path(*parts: str) -> Path:
    """开发与打包环境下的资源路径（如 assets/…）。"""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def get_platform_app_support_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base


def get_install_dir() -> Path:
    """程序安装/解压目录。

    - Windows 打包版：Tally.exe 所在目录
    - macOS 打包版：Tally.app 所在目录
    - 开发模式：当前工作目录
    """
    if not is_frozen():
        return Path.cwd()
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.name.endswith(".app"):
                return parent.parent
    return exe.parent


def get_app_bundle_path() -> Path | None:
    """macOS 下返回 .app 包路径；其他情况返回 None。"""
    if not is_frozen() or sys.platform != "darwin":
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.name.endswith(".app"):
            return parent
    return None


def get_frozen_data_dir() -> Path:
    """打包版固定数据目录。

    - Windows：程序目录下的 data
    - macOS：~/Library/Application Support/Tally/data
    """
    if sys.platform == "darwin":
        return get_platform_app_support_dir() / BOOTSTRAP_APP_DIR / "data"
    return get_install_dir() / "data"


def get_portable_data_dir() -> Path:
    """兼容旧名，等同 get_frozen_data_dir。"""
    return get_frozen_data_dir()


def get_bootstrap_dir() -> Path:
    """引导配置目录。

    - Windows 打包版：程序目录/data
    - macOS 打包版与开发版：~/Library/Application Support/Tally
    """
    if is_frozen() and sys.platform == "win32":
        path = get_frozen_data_dir()
    else:
        path = get_platform_app_support_dir() / BOOTSTRAP_APP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_bootstrap_path() -> Path:
    return get_bootstrap_dir() / "bootstrap.json"


def get_license_path() -> Path:
    """License 文件路径（与 bootstrap 同目录）。"""
    return get_bootstrap_dir() / "license.lic"


def get_legacy_data_dir() -> Path:
    """系统 Application Support 下的 Tally 目录（含引导配置）。"""
    return get_platform_app_support_dir() / BOOTSTRAP_APP_DIR


def frozen_storage_hint() -> str:
    """打包版数据路径说明文案。"""
    if sys.platform == "darwin":
        return "打包版固定使用 ~/Library/Application Support/Tally/data，不可修改"
    return "打包版固定使用程序目录下的 data 文件夹，不可修改"


def frozen_storage_error() -> str:
    if sys.platform == "darwin":
        return "打包版数据存储位置固定为 ~/Library/Application Support/Tally/data，不可修改"
    return "打包版数据存储位置固定为程序目录下的 data，不可修改"
