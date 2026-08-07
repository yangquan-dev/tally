from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from app.license import LicenseInfo, LicenseStatus
from app.services import LicenseService
from app.ui.utils import ask_open_filename, center_window, show_error, show_info


class LicenseDialog(ctk.CTkToplevel):
    """授权提示 / 导入对话框。"""

    def __init__(
        self,
        master,
        license_service: LicenseService,
        info: LicenseInfo,
        on_imported: Optional[Callable[[LicenseInfo], None]] = None,
    ) -> None:
        super().__init__(master)
        self.license_service = license_service
        self.on_imported = on_imported
        self.result: Optional[LicenseInfo] = None

        self.title("软件授权")
        self.geometry("480x280")
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.grab_set()

        title = "需要授权"
        if info.status == LicenseStatus.EXPIRED:
            title = "授权已过期"
        elif info.status == LicenseStatus.INVALID:
            title = "授权无效"
        elif info.status == LicenseStatus.MISSING:
            title = "尚未激活"

        ctk.CTkLabel(
            self, text=title, font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=24, pady=(24, 8))
        ctk.CTkLabel(
            self,
            text=info.message,
            wraplength=420,
            justify="left",
            text_color="#4b5563",
        ).pack(anchor="w", padx=24, pady=(0, 16))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(8, 24))
        ctk.CTkButton(actions, text="关闭", width=90, fg_color="#6b7280", command=self.destroy).pack(
            side="right"
        )
        ctk.CTkButton(
            actions, text="导入授权", width=110, command=self._import_license
        ).pack(side="right", padx=(0, 8))

        self.after(40, lambda: center_window(self, 480, 280))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _import_license(self) -> None:
        path = ask_open_filename(
            title="导入授权文件",
            parent=self,
            filetypes=[("授权文件", "*.lic"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            info = self.license_service.import_file(Path(path))
        except ValueError as exc:
            show_error(str(exc))
            return
        self.result = info
        show_info("授权已导入")
        if self.on_imported:
            self.on_imported(info)
        self.destroy()


def prompt_import_license(
    master,
    license_service: LicenseService,
    info: LicenseInfo,
    on_imported: Optional[Callable[[LicenseInfo], None]] = None,
) -> Optional[LicenseInfo]:
    dialog = LicenseDialog(master, license_service, info, on_imported=on_imported)
    master.wait_window(dialog)
    return dialog.result
