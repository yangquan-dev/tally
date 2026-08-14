"""供 PyInstaller .spec 收集运行时资源。"""

from __future__ import annotations

from pathlib import Path


def collect_datas() -> list[tuple[str, str]]:
    datas: list[tuple[str, str]] = []
    root = Path(__file__).resolve().parent.parent
    app_assets = root / "assets"
    if app_assets.is_dir():
        datas.append((str(app_assets), "assets"))

    try:
        import customtkinter

        ctk_root = Path(customtkinter.__file__).resolve().parent
        assets = ctk_root / "assets"
        if assets.exists():
            datas.append((str(assets), "customtkinter/assets"))
    except Exception:
        pass

    return datas


def collect_package_resources() -> tuple[list, list, list[str]]:
    """收集 customtkinter / tkcalendar / babel 的数据、二进制与隐藏导入。"""
    datas = collect_datas()
    binaries: list = []
    hiddenimports = [
        "PIL._tkinter_finder",
        "babel.numbers",
        "tkcalendar",
        "customtkinter",
        "nacl",
        "nacl.signing",
        "nacl.exceptions",
        "nacl.bindings",
        # PyNaCl → cffi；Windows 打包缺此模块会 ModuleNotFoundError
        "cffi",
        "_cffi_backend",
        "openpyxl",
    ]

    try:
        from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs
    except Exception:
        return datas, binaries, hiddenimports

    for pkg in ("customtkinter", "tkcalendar", "babel", "nacl", "cffi", "openpyxl"):
        try:
            pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
            datas += pkg_datas
            binaries += pkg_binaries
            hiddenimports += pkg_hidden
        except Exception:
            continue

    for pkg in ("nacl", "cffi"):
        try:
            binaries += collect_dynamic_libs(pkg)
        except Exception:
            continue

    # 去重并保持顺序
    hiddenimports = list(dict.fromkeys(hiddenimports))
    return datas, binaries, hiddenimports
