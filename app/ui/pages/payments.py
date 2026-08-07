from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import customtkinter as ctk

from app.services import AppServices, ValidationError
from app.ui.utils import (
    ask_save_filename,
    ask_yes_no,
    billing_month_count,
    format_money,
    parse_date,
    parse_float,
    period_end_by_months,
    show_error,
    show_info,
    today_str,
)
from app.ui.widgets import DataTable, DatePickerField, FormDialog


class PaymentFormDialog(FormDialog):
    def __init__(
        self,
        master,
        title: str,
        services: AppServices,
        leases: list[tuple[int, str]],
        initial: dict | None = None,
        lock_lease: bool = False,
    ) -> None:
        super().__init__(master, title, width=560, height=620)
        self.services = services
        self.leases = leases
        self._suspend_amount_sync = False
        initial = initial or {}

        lease_labels = [label for _, label in leases]
        self.lease_var = ctk.StringVar(
            value=initial.get("lease_label") or (lease_labels[0] if lease_labels else "")
        )
        self.period_choices: list[tuple[str, date, date, float]] = []
        self.period_var = ctk.StringVar(value="")
        self.start_var = ctk.StringVar(value=initial.get("period_start", ""))
        self.end_var = ctk.StringVar(value=initial.get("period_end", ""))
        self.amount_var = ctk.StringVar(value=str(initial.get("amount", "")))
        self.paid_at_var = ctk.StringVar(value=initial.get("paid_at", today_str()))
        self.note_var = ctk.StringVar(value=initial.get("note", ""))

        lease_widget = ctk.CTkOptionMenu(
            self.body,
            values=lease_labels or ["无租赁"],
            variable=self.lease_var,
            command=lambda _v: self._reload_periods(),
        )
        if lock_lease or not lease_labels:
            lease_widget.configure(state="disabled")
        self.add_field(0, "租赁合同", lease_widget)

        self.period_menu = ctk.CTkOptionMenu(
            self.body,
            values=["手动填写"],
            variable=self.period_var,
            command=self._on_period_selected,
        )
        self.add_field(1, "未缴应收期", self.period_menu)

        quick = ctk.CTkFrame(self.body, fg_color="transparent")
        ctk.CTkButton(
            quick, text="季度", width=70, command=lambda: self._apply_quick(3)
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            quick, text="半年", width=70, command=lambda: self._apply_quick(6)
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            quick, text="年", width=70, command=lambda: self._apply_quick(12)
        ).pack(side="left", padx=6)
        self.add_field(2, "快捷周期", quick)

        self.start_picker = DatePickerField(self.body, textvariable=self.start_var)
        self.end_picker = DatePickerField(self.body, textvariable=self.end_var)
        self.paid_at_picker = DatePickerField(self.body, textvariable=self.paid_at_var)
        self.add_field(3, "缴费起始", self.start_picker)
        self.add_field(4, "缴费结束", self.end_picker)
        self.add_field(5, "缴费金额", ctk.CTkEntry(self.body, textvariable=self.amount_var))
        self.add_field(6, "实缴日期", self.paid_at_picker)
        self.add_field(7, "备注", ctk.CTkEntry(self.body, textvariable=self.note_var))

        self.start_var.trace_add("write", self._on_dates_changed)
        self.end_var.trace_add("write", self._on_dates_changed)
        self.after(80, self._reload_periods)

    def _current_lease_id(self) -> int | None:
        if not self.leases:
            return None
        label = self.lease_var.get()
        return next((lid for lid, llabel in self.leases if llabel == label), None)

    def _current_lease(self):
        lease_id = self._current_lease_id()
        if lease_id is None or self.services.leases is None:
            return None
        return self.services.leases.get(lease_id)

    def _set_period_fields(
        self,
        start: date,
        end: date,
        amount: float | None = None,
        sync_amount: bool = True,
    ) -> None:
        self._suspend_amount_sync = True
        try:
            self.start_picker.set(start)
            self.end_picker.set(end)
            if amount is not None:
                self.amount_var.set(f"{amount:.2f}")
            elif sync_amount:
                self._sync_amount_from_dates()
        finally:
            self._suspend_amount_sync = False

    def _calc_amount(self, lease, start: date, end: date) -> float:
        if end < start:
            return 0.0
        # 优先按租赁应收期叠加（自动处理整月免租）
        if self.services.reminders is not None:
            total = 0.0
            for period in self.services.reminders.generate_rent_periods(lease):
                if period.period_end < start or period.period_start > end:
                    continue
                overlap_start = max(period.period_start, start)
                overlap_end = min(period.period_end, end)
                if overlap_end < overlap_start:
                    continue
                full_days = (period.period_end - period.period_start).days + 1
                used_days = (overlap_end - overlap_start).days + 1
                if full_days <= 0:
                    continue
                total += float(period.amount) * used_days / full_days
            return round(total, 2)
        months = billing_month_count(start, end)
        return round(float(lease.monthly_rent) * months, 2)

    def _sync_amount_from_dates(self) -> None:
        lease = self._current_lease()
        if lease is None:
            return
        start_text = self.start_var.get().strip()
        end_text = self.end_var.get().strip()
        if not start_text or not end_text:
            return
        try:
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
        except ValueError:
            return
        amount = self._calc_amount(lease, start, end)
        self.amount_var.set(f"{amount:.2f}")

    def _on_dates_changed(self, *_args) -> None:
        if self._suspend_amount_sync:
            return
        # 手动改日期后视为自定义周期
        if self.period_var.get() != "手动填写":
            self.period_var.set("手动填写")
        self._sync_amount_from_dates()

    def _reload_periods(self) -> None:
        lease_id = self._current_lease_id()
        choices = ["手动填写"]
        self.period_choices = [("手动填写", date.today(), date.today(), 0.0)]
        if lease_id is not None and self.services.leases and self.services.reminders:
            lease = self.services.leases.get(lease_id)
            if lease:
                unpaid = self.services.reminders.unpaid_periods(lease)
                for period in unpaid:
                    label = (
                        f"{period.period_start} ~ {period.period_end} "
                        f"/ ¥{period.amount:.2f}"
                    )
                    choices.append(label)
                    self.period_choices.append(
                        (label, period.period_start, period.period_end, period.amount)
                    )
        self.period_menu.configure(values=choices)
        if self.period_var.get() not in choices:
            self.period_var.set(choices[0])
        if self.period_var.get() != "手动填写":
            self._on_period_selected(self.period_var.get())
        elif self.start_var.get().strip() and self.end_var.get().strip():
            self._sync_amount_from_dates()

    def _on_period_selected(self, value: str) -> None:
        for label, start, end, amount in self.period_choices:
            if label == value and label != "手动填写":
                self._set_period_fields(start, end, amount=amount)
                break

    def _resolve_quick_start(self, lease) -> date:
        text = self.start_var.get().strip()
        if text:
            try:
                return date.fromisoformat(text)
            except ValueError:
                pass
        if self.services.reminders is not None:
            unpaid = self.services.reminders.unpaid_periods(lease)
            if unpaid:
                return unpaid[0].period_start
        return lease.start_date

    def _apply_quick(self, months: int) -> None:
        lease = self._current_lease()
        if lease is None:
            show_error("请先选择租赁合同")
            return
        start = self._resolve_quick_start(lease)
        if start < lease.start_date:
            start = lease.start_date
        if start > lease.end_date:
            show_error("缴费起始已超出租赁到期日")
            return
        end = period_end_by_months(start, months, hard_end=lease.end_date)
        self.period_var.set("手动填写")
        amount = self._calc_amount(lease, start, end)
        self._set_period_fields(start, end, amount=amount)

    def collect(self) -> dict:
        lease_id = self._current_lease_id()
        if lease_id is None:
            raise ValueError("请先创建生效中的租赁")
        return {
            "lease_id": lease_id,
            "period_start": parse_date(self.start_var.get(), "缴费起始"),
            "period_end": parse_date(self.end_var.get(), "缴费结束"),
            "amount": parse_float(self.amount_var.get(), "缴费金额"),
            "paid_at": parse_date(self.paid_at_var.get(), "实缴日期"),
            "note": self.note_var.get().strip(),
        }


class PaymentsPage(ctk.CTkFrame):
    def __init__(self, master, services: AppServices, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="收费登记", font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        self.filter_var = ctk.StringVar(value="全部项目")
        self.filter_menu = ctk.CTkOptionMenu(
            header,
            values=["全部项目"],
            variable=self.filter_var,
            command=lambda _v: self.refresh(),
            width=180,
        )
        self.filter_menu.grid(row=0, column=1, sticky="e", padx=(12, 8))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(actions, text="登记缴费", width=100, command=self.create_payment).pack(
            side="left", padx=4
        )
        ctk.CTkButton(actions, text="编辑", width=80, command=self.edit_payment).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="删除", width=80, fg_color="#b91c1c", command=self.delete_payment
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="导出", width=80, fg_color="#0f766e", command=self.export_payments
        ).pack(side="left", padx=4)

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 48),
                ("project", "项目", 120),
                ("room", "房间", 70),
                ("period", "对应租赁周期", 220),
                ("amount", "金额", 90),
                ("paid_at", "实缴日期", 110),
                ("note", "备注", 200),
            ],
            column_anchors={
                "id": "center",
                "project": "w",
                "room": "center",
                "period": "center",
                "amount": "e",
                "paid_at": "center",
                "note": "w",
            },
            style_prefix="TallyPayment",
            rowheight=34,
        )
        self.table.grid(row=1, column=0, sticky="nsew")

    def _reload_filters(self) -> None:
        if self.services.projects is None:
            return
        projects = self.services.projects.list_all()
        current = self.filter_var.get()
        values = ["全部项目"] + [p.name for p in projects]
        self.filter_menu.configure(values=values)
        if current in values:
            self.filter_var.set(current)
        else:
            self.filter_var.set("全部项目")

    def _current_project_id(self) -> int | None:
        if self.services.projects is None:
            return None
        name = self.filter_var.get()
        if name == "全部项目":
            return None
        for p in self.services.projects.list_all():
            if p.name == name:
                return p.id
        return None

    def _lease_options(self) -> list[tuple[int, str]]:
        leases = self.services.leases.list_all(status="生效")  # type: ignore[union-attr]
        return [
            (
                l.id,
                f"{l.project_name} / {l.room_no} ({l.start_date}~{l.end_date})",
            )
            for l in leases
        ]

    def _selected_id(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid else None

    def _current_payments(self):
        return self.services.payments.list_all(self._current_project_id())  # type: ignore[union-attr]

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.payments is None:
            return
        self._reload_filters()
        payments = self._current_payments()
        rows = [
            (
                p.id,
                p.project_name,
                p.room_no,
                f"{p.period_start} ~ {p.period_end}",
                format_money(p.amount),
                p.paid_at.isoformat(),
                p.note,
            )
            for p in payments
        ]
        self.table.set_rows(rows, [str(p.id) for p in payments])

    def export_payments(self) -> None:
        if not self.services.is_ready or self.services.payments is None:
            show_info("请先完成通用配置")
            return
        payments = self._current_payments()
        if not payments:
            show_info("当前没有可导出的收费记录")
            return

        project_name = self.filter_var.get()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if project_name and project_name != "全部项目":
            default_name = f"收费记录_{project_name}_{stamp}.csv"
        else:
            default_name = f"收费记录_{stamp}.csv"

        path = ask_save_filename(
            title="导出收费记录",
            parent=self,
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "序号",
                        "项目",
                        "房间",
                        "对应租赁周期起",
                        "对应租赁周期止",
                        "金额",
                        "实缴日期",
                        "备注",
                    ]
                )
                for idx, p in enumerate(payments, start=1):
                    writer.writerow(
                        [
                            idx,
                            p.project_name,
                            p.room_no,
                            p.period_start.isoformat(),
                            p.period_end.isoformat(),
                            f"{p.amount:.2f}",
                            p.paid_at.isoformat(),
                            p.note or "",
                        ]
                    )
        except OSError as exc:
            show_error(f"导出失败：{exc}")
            return

        show_info(f"已导出 {len(payments)} 条收费记录\n{Path(path).name}")

    def create_payment(self) -> None:
        leases = self._lease_options()
        if not leases:
            show_info("请先创建生效中的租赁")
            return
        data = PaymentFormDialog(self, "登记缴费", self.services, leases).show()
        if not data:
            return
        try:
            self.services.payments.create(**data)  # type: ignore[union-attr]
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def edit_payment(self) -> None:
        payment_id = self._selected_id()
        if payment_id is None:
            show_info("请先选择一条缴费记录")
            return
        payment = self.services.payments.get(payment_id)  # type: ignore[union-attr]
        if not payment:
            show_error("缴费记录不存在")
            return
        lease = self.services.leases.get(payment.lease_id)  # type: ignore[union-attr]
        if not lease:
            show_error("关联租赁不存在")
            return
        leases = [
            (
                lease.id,
                f"{lease.project_name} / {lease.room_no} ({lease.start_date}~{lease.end_date})",
            )
        ]
        data = PaymentFormDialog(
            self,
            "编辑缴费",
            self.services,
            leases,
            {
                "lease_label": leases[0][1],
                "period_start": payment.period_start.isoformat(),
                "period_end": payment.period_end.isoformat(),
                "amount": payment.amount,
                "paid_at": payment.paid_at.isoformat(),
                "note": payment.note,
            },
            lock_lease=True,
        ).show()
        if not data:
            return
        try:
            self.services.payments.update(  # type: ignore[union-attr]
                payment_id,
                period_start=data["period_start"],
                period_end=data["period_end"],
                amount=data["amount"],
                paid_at=data["paid_at"],
                note=data["note"],
            )
            self.refresh()
        except (ValidationError, ValueError) as exc:
            show_error(str(exc))

    def delete_payment(self) -> None:
        payment_id = self._selected_id()
        if payment_id is None:
            show_info("请先选择一条缴费记录")
            return
        if not ask_yes_no("确认删除该缴费记录？"):
            return
        self.services.payments.delete(payment_id)  # type: ignore[union-attr]
        self.refresh()
