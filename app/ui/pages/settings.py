from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from app.config import frozen_storage_hint, is_frozen
from app.database import export_database, import_database
from app.license import LicenseStatus
from app.services import AppServices, ValidationError
from app.ui.utils import (
    ask_directory,
    ask_open_filename,
    ask_save_filename,
    ask_yes_no,
    parse_int,
    show_error,
    show_info,
)
from app.ui.widgets import TimePickerField


class SettingsPage(ctk.CTkFrame):
    def __init__(
        self,
        master,
        services: AppServices,
        on_storage_configured: Optional[Callable[[], None]] = None,
        on_app_name_changed: Optional[Callable[[str], None]] = None,
        on_uninstall: Optional[Callable[[], None]] = None,
        on_database_replaced: Optional[Callable[[], None]] = None,
        on_license_changed: Optional[Callable[..., None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.on_storage_configured = on_storage_configured
        self.on_app_name_changed = on_app_name_changed
        self.on_uninstall = on_uninstall
        self.on_database_replaced = on_database_replaced
        self.on_license_changed = on_license_changed
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部固定：标题 + 说明与保存按钮同行
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(
            header, text="通用配置", font=ctk.CTkFont(size=22, weight="bold")
        ).pack(anchor="w")
        meta = ctk.CTkFrame(header, fg_color="transparent")
        meta.pack(fill="x", pady=(4, 0))
        meta.grid_columnconfigure(0, weight=1)
        self.subtitle = ctk.CTkLabel(
            meta,
            text="请先配置数据存储位置，配置后才能使用其他功能",
            font=ctk.CTkFont(size=13),
            text_color="#6b7280",
            anchor="w",
        )
        self.subtitle.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.save_btn = ctk.CTkButton(
            meta, text="保存配置", height=28, width=80, command=self.save
        )
        self.save_btn.grid(row=0, column=1, sticky="e", padx=(10, 8))

        # 下方配置内容可滚动，顶部保持固定
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(self.scroll)
        card.grid(row=0, column=0, sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        row = 0
        ctk.CTkLabel(
            card, text="基础设置", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(20, 12))

        row = 1
        ctk.CTkLabel(card, text="应用名称", anchor="w", width=180).grid(
            row=row, column=0, sticky="w", padx=20, pady=10
        )
        self.app_name_var = ctk.StringVar(value="")
        self.app_name_entry = ctk.CTkEntry(card, textvariable=self.app_name_var)
        self.app_name_entry.grid(
            row=row, column=1, columnspan=2, sticky="ew", padx=(0, 20), pady=10
        )

        row = 2
        ctk.CTkLabel(card, text="数据存储位置", anchor="w", width=180).grid(
            row=row, column=0, sticky="w", padx=20, pady=10
        )
        self.storage_var = ctk.StringVar(value="")
        self.storage_entry = ctk.CTkEntry(card, textvariable=self.storage_var)
        self.storage_entry.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=10)
        self.browse_btn = ctk.CTkButton(
            card, text="选择文件夹", height=28, width=88, command=self.browse_storage
        )
        self.browse_btn.grid(row=row, column=2, sticky="e", padx=(0, 20), pady=10)

        row = 3
        self.storage_hint = ctk.CTkLabel(
            card,
            text="选择后保存即锁定，之后不可修改",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        )
        self.storage_hint.grid(
            row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 8)
        )

        row = 4
        ctk.CTkLabel(
            card, text="提醒设置", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 12))

        row = 5
        ctk.CTkLabel(card, text="合同到期提前提醒天数", anchor="w", width=180).grid(
            row=row, column=0, sticky="w", padx=20, pady=10
        )
        self.expire_var = ctk.StringVar(value="7")
        self.expire_entry = ctk.CTkEntry(
            card, textvariable=self.expire_var, width=160, placeholder_text="例如 7"
        )
        self.expire_entry.grid(row=row, column=1, sticky="w", padx=(0, 20), pady=10)

        row = 6
        ctk.CTkLabel(
            card,
            text="合同到期日前多少天开始提醒",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 8))

        row = 7
        ctk.CTkLabel(card, text="应收提前提醒天数", anchor="w", width=180).grid(
            row=row, column=0, sticky="w", padx=20, pady=10
        )
        self.rent_var = ctk.StringVar(value="7")
        self.rent_entry = ctk.CTkEntry(
            card, textvariable=self.rent_var, width=160, placeholder_text="例如 7"
        )
        self.rent_entry.grid(row=row, column=1, sticky="w", padx=(0, 20), pady=10)

        row = 8
        ctk.CTkLabel(
            card,
            text="应收起始日前多少天开始提醒",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 20))

        # 数据备份独立区域
        backup_card = ctk.CTkFrame(self.scroll)
        backup_card.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        backup_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            backup_card, text="数据备份", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 12))
        ctk.CTkLabel(backup_card, text="数据库文件", anchor="w", width=180).grid(
            row=1, column=0, sticky="w", padx=20, pady=10
        )
        db_actions = ctk.CTkFrame(backup_card, fg_color="transparent")
        db_actions.grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 20), pady=10)
        self.export_db_btn = ctk.CTkButton(
            db_actions, text="导出数据库", height=28, width=88, command=self.export_database
        )
        self.export_db_btn.pack(side="left", padx=(0, 4))
        self.import_db_btn = ctk.CTkButton(
            db_actions, text="导入数据库", height=28, width=88, command=self.import_database
        )
        self.import_db_btn.pack(side="left", padx=(4, 0))
        ctk.CTkLabel(
            backup_card,
            text="导出备份当前数据；导入将覆盖现有数据（覆盖前自动生成 .bak）",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 16))

        # 钉钉推送
        dingtalk_card = ctk.CTkFrame(self.scroll)
        dingtalk_card.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        dingtalk_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            dingtalk_card, text="钉钉推送", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 8))
        ctk.CTkLabel(
            dingtalk_card,
            text="联网时按设定时间每天推送一次；若到点未联网，恢复联网后会自动补推",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 8))

        ctk.CTkLabel(dingtalk_card, text="启用推送", anchor="w", width=180).grid(
            row=2, column=0, sticky="w", padx=20, pady=10
        )
        self.dingtalk_enabled_var = ctk.BooleanVar(value=False)
        self.dingtalk_enabled_switch = ctk.CTkSwitch(
            dingtalk_card,
            text="",
            variable=self.dingtalk_enabled_var,
            onvalue=True,
            offvalue=False,
        )
        self.dingtalk_enabled_switch.grid(row=2, column=1, sticky="w", padx=(0, 20), pady=10)

        ctk.CTkLabel(dingtalk_card, text="推送时间", anchor="w", width=180).grid(
            row=3, column=0, sticky="w", padx=20, pady=10
        )
        self.dingtalk_time_var = ctk.StringVar(value="09:00")
        self.dingtalk_time_picker = TimePickerField(
            dingtalk_card, textvariable=self.dingtalk_time_var
        )
        self.dingtalk_time_picker.grid(row=3, column=1, sticky="w", padx=(0, 20), pady=10)

        ctk.CTkLabel(dingtalk_card, text="Webhook 地址", anchor="w", width=180).grid(
            row=4, column=0, sticky="w", padx=20, pady=10
        )
        self.dingtalk_webhook_var = ctk.StringVar(value="")
        self.dingtalk_webhook_entry = ctk.CTkEntry(
            dingtalk_card,
            textvariable=self.dingtalk_webhook_var,
            placeholder_text="https://oapi.dingtalk.com/robot/send?access_token=...",
        )
        self.dingtalk_webhook_entry.grid(
            row=4, column=1, columnspan=2, sticky="ew", padx=(0, 20), pady=10
        )

        ctk.CTkLabel(dingtalk_card, text="加签密钥", anchor="w", width=180).grid(
            row=5, column=0, sticky="w", padx=20, pady=10
        )
        self.dingtalk_secret_var = ctk.StringVar(value="")
        self.dingtalk_secret_entry = ctk.CTkEntry(
            dingtalk_card,
            textvariable=self.dingtalk_secret_var,
            placeholder_text="SEC 开头的加签密钥",
            show="*",
        )
        self.dingtalk_secret_entry.grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=(0, 20), pady=10
        )
        ctk.CTkLabel(
            dingtalk_card,
            text="请在钉钉机器人安全设置中选择「加签」，并将 SEC 密钥填入上方",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        ).grid(row=6, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 4))

        self.dingtalk_last_label = ctk.CTkLabel(
            dingtalk_card,
            text="最近推送：—",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
            justify="left",
        )
        self.dingtalk_last_label.grid(
            row=7, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 4)
        )
        self.dingtalk_error_label = ctk.CTkLabel(
            dingtalk_card,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#b91c1c",
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.dingtalk_error_label.grid(
            row=8, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 12)
        )
        self.dingtalk_test_btn = ctk.CTkButton(
            dingtalk_card,
            text="测试推送",
            height=28,
            width=80,
            command=self.test_dingtalk_push,
        )
        self.dingtalk_test_btn.grid(row=8, column=2, sticky="e", padx=20, pady=(0, 16))

        # 授权独立区域
        license_card = ctk.CTkFrame(self.scroll)
        license_card.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        license_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            license_card, text="软件授权", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(16, 8))
        self.license_status_label = ctk.CTkLabel(
            license_card,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#111827",
            anchor="w",
            justify="left",
        )
        self.license_status_label.grid(
            row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 4)
        )
        self.license_detail_label = ctk.CTkLabel(
            license_card,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.license_detail_label.grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 12)
        )
        license_actions = ctk.CTkFrame(license_card, fg_color="transparent")
        license_actions.grid(row=2, column=1, sticky="e", padx=20, pady=(0, 12))
        self.remove_license_btn = ctk.CTkButton(
            license_actions,
            text="移除授权",
            height=28,
            width=80,
            fg_color="#6b7280",
            hover_color="#4b5563",
            command=self.remove_license,
        )
        self.remove_license_btn.pack(side="right", padx=(4, 0))
        self.import_license_btn = ctk.CTkButton(
            license_actions,
            text="导入授权",
            height=28,
            width=80,
            command=self.import_license,
        )
        self.import_license_btn.pack(side="right", padx=(0, 4))
        ctk.CTkLabel(
            license_card,
            text="安装后须手动导入 .lic；到期后有 7 天宽限期，宽限期结束后需更换新授权",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 16))

        # 卸载区独立于配置内容卡片
        self.uninstall_card = ctk.CTkFrame(self.scroll)
        self.uninstall_card.grid(row=4, column=0, sticky="ew", pady=(16, 12))
        self.uninstall_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.uninstall_card,
            text="应用卸载",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(16, 8))
        self.uninstall_hint = ctk.CTkLabel(
            self.uninstall_card,
            text="卸载将删除程序目录、本地数据与配置，且不可恢复",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        )
        self.uninstall_hint.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))
        self.uninstall_btn = ctk.CTkButton(
            self.uninstall_card,
            text="卸载",
            height=28,
            width=56,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            command=self.uninstall,
        )
        self.uninstall_btn.grid(row=1, column=1, sticky="e", padx=(10, 20), pady=(0, 16))

    def browse_storage(self) -> None:
        if not self.services.license.allow_business:
            show_info("请先导入授权后再配置")
            return
        if is_frozen() or self.services.bootstrap.is_portable():
            show_info(frozen_storage_hint())
            return
        if self.services.bootstrap.is_storage_configured():
            show_info("数据存储位置已配置，不可修改")
            return
        selected = ask_directory(title="选择数据存储文件夹", parent=self)
        if selected:
            self.storage_var.set(selected)

    def uninstall(self) -> None:
        if not ask_yes_no(
            "确认卸载？\n将删除程序文件与全部本地数据，且不可恢复。"
        ):
            return
        if not is_frozen():
            show_info("开发运行模式不提供卸载")
            return
        if self.on_uninstall:
            self.on_uninstall()

    def export_database(self) -> None:
        if not self.services.license.allow_business:
            show_info("请先导入授权后再操作")
            return
        db_path = self.services.bootstrap.get_db_path()
        if db_path is None:
            show_info("请先配置数据存储位置")
            return
        if not Path(db_path).is_file():
            show_info("当前还没有数据库文件，请先使用系统生成数据后再导出")
            return
        default_name = f"tally-backup-{date.today().isoformat()}.db"
        target = ask_save_filename(
            title="导出数据库",
            parent=self,
            defaultextension=".db",
            initialfile=default_name,
            filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")],
        )
        if not target:
            return
        try:
            export_database(db_path, Path(target))
            show_info(f"数据库已导出到：\n{target}")
        except ValueError as exc:
            show_error(str(exc))
        except OSError as exc:
            show_error(f"导出失败：{exc}")

    def import_database(self) -> None:
        if not self.services.license.allow_business:
            show_info("请先导入授权后再操作")
            return
        db_path = self.services.bootstrap.get_db_path()
        if db_path is None:
            show_info("请先配置数据存储位置")
            return
        if not ask_yes_no(
            "导入将覆盖当前全部业务数据，覆盖前会自动备份为 tally.db.bak。是否继续？"
        ):
            return
        source = ask_open_filename(
            title="导入数据库",
            parent=self,
            filetypes=[("SQLite 数据库", "*.db"), ("所有文件", "*.*")],
        )
        if not source:
            return
        try:
            import_database(Path(source), Path(db_path))
            if self.on_database_replaced:
                self.on_database_replaced()
            show_info("数据库已导入，数据已刷新")
            self.refresh()
        except ValueError as exc:
            show_error(str(exc))
        except OSError as exc:
            show_error(f"导入失败：{exc}")

    def import_license(self) -> None:
        source = ask_open_filename(
            title="导入授权文件",
            parent=self,
            filetypes=[("授权文件", "*.lic"), ("所有文件", "*.*")],
        )
        if not source:
            return
        try:
            info = self.services.license.import_file(Path(source))
            show_info("授权已导入")
            self.refresh()
            if self.on_license_changed:
                self.on_license_changed(info)
        except ValueError as exc:
            show_error(str(exc))
        except OSError as exc:
            show_error(f"导入失败：{exc}")

    def remove_license(self) -> None:
        if not self.services.license.allow_business:
            show_info("当前未授权，无法移除")
            return
        if self.services.license.check().status == LicenseStatus.MISSING:
            show_info("当前尚未导入授权")
            return
        if not ask_yes_no("确认移除本地授权？移除后需重新导入才能使用业务功能。"):
            return
        try:
            info = self.services.license.remove()
            show_info("授权已移除")
            self.refresh()
            if self.on_license_changed:
                self.on_license_changed(info)
        except ValueError as exc:
            show_error(str(exc))
        except OSError as exc:
            show_error(f"移除失败：{exc}")

    def _refresh_license_ui(self) -> None:
        info = self.services.license.check()
        lines = [f"状态：{info.status_label}"]
        if info.customer:
            lines.append(f"客户：{info.customer}")
        if info.expires_at is not None:
            lines.append(f"到期日：{info.expires_at.isoformat()}")
        if info.grace_end is not None and info.status in {
            LicenseStatus.VALID,
            LicenseStatus.GRACE,
            LicenseStatus.EXPIRED,
        }:
            lines.append(f"宽限截止：{info.grace_end.isoformat()}")
        if info.status == LicenseStatus.VALID and info.days_remaining is not None:
            lines.append(f"剩余：{info.days_remaining} 天")
        self.license_status_label.configure(text="　".join(lines))
        self.license_detail_label.configure(text=info.message)

    def test_dingtalk_push(self) -> None:
        if not self.services.license.allow_business:
            show_info("请先导入授权后再操作")
            return
        try:
            self._save_core(show_success=False)
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))
            return
        try:
            msg = self.services.dingtalk.push_reminders(force=True)
            show_info(msg)
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def _set_dingtalk_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.dingtalk_enabled_switch.configure(state=state)
        self.dingtalk_time_picker.configure(state=state)
        self.dingtalk_webhook_entry.configure(state=state)
        self.dingtalk_secret_entry.configure(state=state)
        self.dingtalk_test_btn.configure(state=state)

    def _apply_license_gate(self, licensed: bool) -> None:
        """未授权时仅允许「导入授权」「卸载」。"""
        if licensed:
            self.save_btn.configure(state="normal")
            self.app_name_entry.configure(state="normal")
            self.import_license_btn.configure(state="normal")
            self.uninstall_btn.configure(state="normal")
            info = self.services.license.check()
            self.remove_license_btn.configure(
                state="disabled" if info.status == LicenseStatus.MISSING else "normal"
            )
            self._set_dingtalk_enabled(self.services.is_ready)
            return

        self.save_btn.configure(state="disabled")
        self.app_name_entry.configure(state="disabled")
        self._set_storage_entry_readonly(True)
        self.browse_btn.configure(state="disabled")
        self._set_remind_enabled(False)
        self._set_db_actions_enabled(False)
        self._set_dingtalk_enabled(False)
        self.remove_license_btn.configure(state="disabled")
        self.import_license_btn.configure(state="normal")
        self.uninstall_btn.configure(state="normal")
        self.subtitle.configure(text="请先导入授权文件，授权后才能修改配置")

    def _set_remind_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.expire_entry.configure(state=state)
        self.rent_entry.configure(state=state)

    def _set_db_actions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.export_db_btn.configure(state=state)
        self.import_db_btn.configure(state=state)

    def _set_storage_entry_readonly(self, readonly: bool) -> None:
        """不可修改时置灰路径框，可修改时恢复可编辑样式。"""
        if readonly:
            self.storage_entry.configure(
                state="disabled",
                fg_color=("#e5e7eb", "#374151"),
                text_color=("#6b7280", "#9ca3af"),
                border_color=("#d1d5db", "#4b5563"),
            )
        else:
            self.storage_entry.configure(
                state="normal",
                fg_color=("#f9fafb", "#343638"),
                text_color=("#111827", "#DCE4EE"),
                border_color=("#979DA2", "#565B5E"),
            )

    def _set_storage_locked_ui(self, locked: bool) -> None:
        portable = is_frozen() or self.services.bootstrap.is_portable()
        if portable:
            self._set_storage_entry_readonly(True)
            self.browse_btn.grid_remove()
            self.storage_entry.grid_configure(columnspan=2, padx=(0, 20))
            self.storage_hint.configure(text=frozen_storage_hint())
            self.subtitle.configure(text="应用于全部项目的提醒与系统参数")
            self.uninstall_btn.configure(state="normal")
            self.uninstall_hint.configure(
                text="卸载将删除程序目录、本地数据与配置，且不可恢复"
            )
            return

        self.browse_btn.grid()
        self.storage_entry.grid_configure(columnspan=1, padx=(0, 8))
        # 开发模式也保留卸载入口；实际卸载仅打包版可用
        self.uninstall_btn.configure(state="normal")
        self.uninstall_hint.configure(
            text="卸载将删除程序目录、本地数据与配置，且不可恢复（开发运行下点击会提示不可用）"
        )
        if locked:
            self._set_storage_entry_readonly(True)
            self.browse_btn.configure(state="disabled")
            self.storage_hint.configure(text="数据存储位置已锁定，不可修改")
            self.subtitle.configure(text="应用于全部项目的提醒与系统参数")
        else:
            self._set_storage_entry_readonly(False)
            self.browse_btn.configure(state="normal")
            self.storage_hint.configure(text="选择后保存即锁定，之后不可修改")
            self.subtitle.configure(
                text="请先配置数据存储位置，配置后才能使用其他功能"
            )

    def refresh_dingtalk_status(self) -> None:
        """仅刷新钉钉推送状态文案（供定时调度调用）。"""
        if not hasattr(self, "dingtalk_last_label"):
            return
        settings = self.services.settings.get()
        last = settings.dingtalk_last_push_date or "—"
        self.dingtalk_last_label.configure(text=f"最近推送：{last}")
        err = self.services.settings.dingtalk_last_error()
        self.dingtalk_error_label.configure(
            text=f"最近失败：{err}" if err else ""
        )

    def refresh(self) -> None:
        settings = self.services.settings.get()
        licensed = self.services.license.allow_business
        # 先切到可写再写入路径/名称，避免 disabled 状态下显示不更新
        self.app_name_entry.configure(state="normal")
        self.app_name_var.set(settings.app_name)
        self.storage_entry.configure(state="normal")
        self.storage_var.set(settings.data_storage_path)
        self.expire_var.set(str(settings.lease_expire_remind_days))
        self.rent_var.set(str(settings.rent_due_remind_days))
        self.dingtalk_enabled_var.set(settings.dingtalk_enabled)
        self.dingtalk_time_picker.set(settings.dingtalk_push_time or "09:00")
        self.dingtalk_webhook_entry.configure(state="normal")
        self.dingtalk_secret_entry.configure(state="normal")
        self.dingtalk_webhook_var.set(settings.dingtalk_webhook)
        self.dingtalk_secret_var.set(settings.dingtalk_secret)
        self.refresh_dingtalk_status()
        self._set_storage_locked_ui(settings.storage_locked)
        ready = self.services.is_ready
        self._set_remind_enabled(ready)
        self._set_db_actions_enabled(settings.storage_locked or ready)
        self._refresh_license_ui()
        self._apply_license_gate(licensed)

    def _save_core(self, *, show_success: bool = True) -> None:
        if not self.services.license.allow_business:
            raise ValidationError("请先导入授权后再保存配置")
        app_name = self.app_name_var.get().strip()
        storage_path = self.storage_var.get().strip()
        was_ready = self.services.is_ready
        portable = is_frozen() or self.services.bootstrap.is_portable()

        expire = None
        rent = None
        if was_ready or self.services.bootstrap.is_storage_configured() or portable:
            expire = parse_int(self.expire_var.get(), "合同到期提前提醒天数")
            rent = parse_int(self.rent_var.get(), "应收提前提醒天数")
        elif self.expire_var.get().strip() or self.rent_var.get().strip():
            expire = parse_int(self.expire_var.get() or "7", "合同到期提前提醒天数")
            rent = parse_int(self.rent_var.get() or "7", "应收提前提醒天数")

        storage_just_set = self.services.settings.update(
            app_name=app_name,
            data_storage_path=None if (was_ready or portable) else storage_path,
            lease_expire_remind_days=expire if (was_ready or portable) else None,
            rent_due_remind_days=rent if (was_ready or portable) else None,
            dingtalk_enabled=self.dingtalk_enabled_var.get(),
            dingtalk_webhook=self.dingtalk_webhook_var.get(),
            dingtalk_secret=self.dingtalk_secret_var.get(),
            dingtalk_push_time=self.dingtalk_time_var.get(),
        )

        if storage_just_set and self.on_storage_configured:
            self.on_storage_configured()

        if self.services.is_ready and expire is not None and rent is not None:
            self.services.settings.update(
                app_name=app_name,
                data_storage_path=None,
                lease_expire_remind_days=expire,
                rent_due_remind_days=rent,
                dingtalk_enabled=self.dingtalk_enabled_var.get(),
                dingtalk_webhook=self.dingtalk_webhook_var.get(),
                dingtalk_secret=self.dingtalk_secret_var.get(),
                dingtalk_push_time=self.dingtalk_time_var.get(),
            )

        if self.on_app_name_changed:
            self.on_app_name_changed(self.services.bootstrap.app_name)

        if show_success:
            show_info("配置已保存")
        self.refresh()

    def save(self) -> None:
        if not self.services.license.allow_business:
            show_info("请先导入授权后再保存配置")
            return
        try:
            self._save_core(show_success=True)
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))
