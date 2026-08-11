from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import customtkinter as ctk

from app.services import AppServices, ValidationError
from app.ui.utils import (
    ask_save_filename,
    ask_yes_no,
    format_date_range,
    format_money,
    parse_date,
    parse_float,
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
        exclude_payment_id: int | None = None,
    ) -> None:
        super().__init__(master, title, width=760, height=480)
        self.services = services
        self.leases = leases
        self.exclude_payment_id = exclude_payment_id
        self._due_amount = 0.0  # 剩余应缴（当次缴纳上限）
        self._period_start: date | None = None
        self._period_end: date | None = None
        initial = initial or {}

        lease_labels = [label for _, label in leases]
        self.lease_var = ctk.StringVar(
            value=initial.get("lease_label") or (lease_labels[0] if lease_labels else "")
        )
        # label, start, end, due, paid, free, discount, remaining
        self.period_choices: list[
            tuple[str, date, date, float, float, float, float, float]
        ] = []
        self.period_var = ctk.StringVar(value="")
        self.term_var = ctk.StringVar(value="—")
        self._summary_prefix_var = ctk.StringVar(value="剩余应缴")
        self._summary_remaining_var = ctk.StringVar(value="(—)")
        self._summary_suffix_var = ctk.StringVar(value="")
        init_amount = initial.get("amount", "")
        self.amount_var = ctk.StringVar(
            value="" if init_amount in ("", None) else str(init_amount)
        )
        self.paid_at_var = ctk.StringVar(value=initial.get("paid_at", today_str()))
        self.note_var = ctk.StringVar(value=initial.get("note", ""))
        self._keep_initial_amount = bool(
            str(init_amount).strip() and exclude_payment_id is not None
        )
        self._initial_start = (initial.get("period_start") or "").strip()
        self._initial_end = (initial.get("period_end") or "").strip()

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
            values=["暂无未缴应收期"],
            variable=self.period_var,
            command=self._on_period_selected,
        )
        self.add_field(1, "未缴应收期", self.period_menu)

        readonly_color = "#374151"
        self.term_label = ctk.CTkLabel(
            self.body, textvariable=self.term_var, anchor="w", text_color=readonly_color
        )
        summary_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self._summary_prefix_label = ctk.CTkLabel(
            summary_box,
            textvariable=self._summary_prefix_var,
            anchor="w",
            text_color=readonly_color,
        )
        self._summary_remaining_label = ctk.CTkLabel(
            summary_box,
            textvariable=self._summary_remaining_var,
            anchor="w",
            text_color=readonly_color,
        )
        self._summary_suffix_label = ctk.CTkLabel(
            summary_box,
            textvariable=self._summary_suffix_var,
            anchor="w",
            text_color=readonly_color,
            justify="left",
        )
        self._summary_prefix_label.pack(side="left")
        self._summary_remaining_label.pack(side="left")
        self._summary_suffix_label.pack(side="left", fill="x", expand=True)
        self.add_field(2, "缴费起止", self.term_label)
        self.add_field(3, "费用明细", summary_box)
        self.add_field(4, "当次缴纳", ctk.CTkEntry(self.body, textvariable=self.amount_var))
        self.paid_at_picker = DatePickerField(self.body, textvariable=self.paid_at_var)
        self.add_field(5, "实缴日期", self.paid_at_picker)
        self.add_field(6, "备注", ctk.CTkEntry(self.body, textvariable=self.note_var))

        self.after(80, self._reload_periods)

    @staticmethod
    def _format_amount_summary(
        due: float, paid: float, free: float, discount: float, remaining: float
    ) -> str:
        return (
            f"剩余应缴({remaining:g})=应缴({due:g})-已缴({paid:g})-"
            f"免租({free:g})-折/减({discount:g})"
        )

    def _set_amount_summary(
        self,
        due: float,
        paid: float,
        free: float,
        discount: float,
        remaining: float,
    ) -> None:
        remaining = round(float(remaining), 2)
        self._summary_prefix_var.set("剩余应缴")
        self._summary_remaining_var.set(f"({remaining:g})")
        self._summary_suffix_var.set(
            f"=应缴({due:g})-已缴({paid:g})-免租({free:g})-折/减({discount:g})"
        )
        self._summary_remaining_label.configure(
            text_color="#b91c1c" if remaining > 0 else "#374151"
        )

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

    def _apply_period(
        self,
        start: date,
        end: date,
        due: float,
        paid: float,
        free: float,
        discount: float,
        remaining: float,
        *,
        sync_amount: bool = True,
    ) -> None:
        self._period_start = start
        self._period_end = end
        self._due_amount = round(float(remaining), 2)
        self.term_var.set(format_date_range(start, end))
        self._set_amount_summary(due, paid, free, discount, remaining)
        if sync_amount:
            if self._keep_initial_amount:
                self._keep_initial_amount = False
            else:
                self.amount_var.set(
                    f"{self._due_amount:.2f}" if self._due_amount > 0 else ""
                )

    def _clear_period(self) -> None:
        self._period_start = None
        self._period_end = None
        self._due_amount = 0.0
        self.term_var.set("—")
        self._summary_prefix_var.set("")
        self._summary_remaining_var.set("(—)")
        self._summary_suffix_var.set("")
        self._summary_remaining_label.configure(text_color="#374151")
        if not self._keep_initial_amount:
            self.amount_var.set("")

    def _reload_periods(self) -> None:
        lease_id = self._current_lease_id()
        choices: list[str] = []
        self.period_choices = []
        if lease_id is not None and self.services.leases and self.services.reminders:
            lease = self.services.leases.get(lease_id)
            if lease:
                for period in self.services.reminders.generate_rent_periods(lease):
                    if period.fully_free or period.amount <= 0:
                        continue
                    # 应缴=未免租未折减；剩余=应缴-已缴-免租-折/减
                    due = self.services.reminders.period_gross_amount(lease, period)
                    free = self.services.reminders.period_free_amount(lease, period)
                    discount = self.services.reminders.period_discount_amount(
                        lease, period
                    )
                    paid = self.services.reminders.paid_amount_for_period(
                        lease.id,
                        period,
                        exclude_payment_id=self.exclude_payment_id,
                    )
                    remaining = round(max(0.0, due - paid - free - discount), 2)
                    if remaining <= 0:
                        continue
                    summary = self._format_amount_summary(
                        due, paid, free, discount, remaining
                    )
                    label = (
                        f"{format_date_range(period.period_start, period.period_end)}"
                        f"　{summary}"
                    )
                    choices.append(label)
                    self.period_choices.append(
                        (
                            label,
                            period.period_start,
                            period.period_end,
                            due,
                            paid,
                            free,
                            discount,
                            remaining,
                        )
                    )

        if not choices:
            self.period_menu.configure(values=["暂无未缴应收期"])
            self.period_var.set("暂无未缴应收期")
            self.period_menu.configure(state="disabled")
            self._clear_period()
            return

        self.period_menu.configure(state="normal", values=choices)
        matched = None
        if self._initial_start and self._initial_end:
            try:
                start = date.fromisoformat(self._initial_start)
                end = date.fromisoformat(self._initial_end)
            except ValueError:
                start = end = None  # type: ignore[assignment]
            if start and end:
                for item in self.period_choices:
                    label, p_start, p_end, due, paid, free, discount, remaining = item
                    if p_start == start and p_end == end:
                        matched = item
                        break
            self._initial_start = ""
            self._initial_end = ""

        selected = matched or self.period_choices[0]
        label, start, end, due, paid, free, discount, remaining = selected
        keep = self._keep_initial_amount
        self.period_var.set(label)
        self._apply_period(
            start, end, due, paid, free, discount, remaining, sync_amount=not keep
        )
        if keep:
            self._keep_initial_amount = False

    def _on_period_selected(self, value: str) -> None:
        for label, start, end, due, paid, free, discount, remaining in self.period_choices:
            if label == value:
                self._apply_period(
                    start, end, due, paid, free, discount, remaining, sync_amount=True
                )
                break

    def collect(self) -> dict:
        lease_id = self._current_lease_id()
        if lease_id is None:
            raise ValueError("请先创建生效中的租赁")
        if self._period_start is None or self._period_end is None:
            raise ValueError("请选择未缴应收期")
        if self._due_amount <= 0:
            raise ValueError("当前周期剩余应缴为 0，无需缴费")
        amount = parse_float(self.amount_var.get(), "当次缴纳")
        if amount <= 0 or amount > self._due_amount + 0.009:
            raise ValueError(
                f"当次缴纳须大于 0 且不超过剩余应缴 ¥{self._due_amount:.2f}"
            )
        amount = min(amount, self._due_amount)
        return {
            "lease_id": lease_id,
            "period_start": self._period_start,
            "period_end": self._period_end,
            "amount": round(amount, 2),
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

        filter_box = ctk.CTkFrame(header, fg_color="transparent")
        filter_box.grid(row=0, column=1, sticky="e", padx=(12, 8))
        ctk.CTkLabel(filter_box, text="项目", text_color="#6b7280").pack(
            side="left", padx=(0, 8)
        )
        self.project_var = ctk.StringVar(value="全部项目")
        self.project_menu = ctk.CTkOptionMenu(
            filter_box,
            values=["全部项目"],
            variable=self.project_var,
            command=self._on_project_filter_changed,
            width=160,
        )
        self.project_menu.pack(side="left")
        ctk.CTkLabel(filter_box, text="房间号", text_color="#6b7280").pack(
            side="left", padx=(12, 8)
        )
        self.room_var = ctk.StringVar(value="全部房间")
        self.room_menu = ctk.CTkOptionMenu(
            filter_box,
            values=["全部房间"],
            variable=self.room_var,
            command=lambda _v: self.refresh(),
            width=120,
        )
        self.room_menu.pack(side="left")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(
            actions, text="登记缴费", width=100, command=self.create_payment
        ).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="编辑", width=80, command=self.edit_payment).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions, text="导出 CSV", width=100, command=self.export_csv
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="删除", width=80, fg_color="#b91c1c", command=self.delete_payment
        ).pack(side="left", padx=4)

        self.table = DataTable(
            self,
            columns=[
                ("id", "ID", 48),
                ("project", "项目", 140),
                ("room", "房间", 80),
                ("period", "缴费周期", 150),
                ("amount", "缴纳金额", 90),
                ("paid_at", "实缴日期", 90),
                ("registered_at", "更新时间", 130),
                ("note", "备注", 160),
            ],
            column_anchors={
                "id": "center",
                "project": "w",
                "room": "center",
                "period": "center",
                "amount": "e",
                "paid_at": "center",
                "registered_at": "center",
                "note": "w",
            },
            style_prefix="TallyPay",
            fit_content_columns=("period", "paid_at", "registered_at"),
        )
        self.table.grid(row=1, column=0, sticky="nsew")

    @staticmethod
    def _format_registered_at(value: str) -> str:
        """展示更新时间，格式 yyyy-MM-dd HH:mm:ss。"""
        text = (value or "").strip().replace("T", " ")
        if not text:
            return "—"
        if len(text) >= 19:
            return text[:19]
        if len(text) == 16:
            return f"{text}:00"
        return text

    def _lease_options(self) -> list[tuple[int, str]]:
        if self.services.leases is None:
            return []
        leases = self.services.leases.list_all(status="生效")
        return [
            (
                lease.id,
                f"{lease.project_name} / {lease.room_no} ({lease.start_date}~{lease.end_date})",
            )
            for lease in leases
        ]

    def _selected_id(self) -> int | None:
        iid = self.table.selected_iid()
        return int(iid) if iid else None

    def _reload_project_filters(self) -> None:
        if self.services.projects is None:
            return
        projects = self.services.projects.list_all()
        current = self.project_var.get()
        values = ["全部项目"] + [p.name for p in projects]
        self.project_menu.configure(values=values)
        if current in values:
            self.project_var.set(current)
        else:
            self.project_var.set("全部项目")
        self._reload_room_filters(preserve_selection=True)

    def _reload_room_filters(self, preserve_selection: bool = True) -> None:
        if self.services.rooms is None:
            return
        project_id = self._current_project_id()
        if project_id is None:
            values = ["全部房间"]
        else:
            rooms = self.services.rooms.list_by_project(project_id)
            room_nos = sorted({r.room_no for r in rooms}, key=lambda x: (len(x), x))
            values = ["全部房间"] + room_nos
        current = self.room_var.get() if preserve_selection else "全部房间"
        self.room_menu.configure(values=values)
        if current in values:
            self.room_var.set(current)
        else:
            self.room_var.set("全部房间")

    def _on_project_filter_changed(self, _value: str) -> None:
        self._reload_room_filters(preserve_selection=False)
        self.refresh()

    def _current_project_id(self) -> int | None:
        if self.services.projects is None:
            return None
        name = self.project_var.get()
        if name == "全部项目":
            return None
        for p in self.services.projects.list_all():
            if p.name == name:
                return p.id
        return None

    def _current_room_no(self) -> str | None:
        room_no = self.room_var.get().strip()
        if not room_no or room_no == "全部房间":
            return None
        return room_no

    def _filtered_payments(self):
        payments = self.services.payments.list_all(self._current_project_id())  # type: ignore[union-attr]
        room_no = self._current_room_no()
        if room_no is not None:
            payments = [p for p in payments if p.room_no == room_no]
        return payments

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.payments is None:
            return
        self._reload_project_filters()
        payments = self._filtered_payments()
        rows = []
        for p in payments:
            rows.append(
                (
                    p.id,
                    p.project_name,
                    p.room_no,
                    format_date_range(p.period_start, p.period_end),
                    format_money(p.amount),
                    p.paid_at.isoformat(),
                    self._format_registered_at(p.updated_at or p.created_at),
                    p.note,
                )
            )
        self.table.set_rows(rows, [str(p.id) for p in payments])

    def export_csv(self) -> None:
        if not self.services.is_ready or self.services.payments is None:
            return
        payments = self._filtered_payments()
        if not payments:
            show_info("当前没有可导出的缴费记录")
            return
        path = ask_save_filename(
            title="导出缴费记录",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            initialfile=f"缴费记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            with Path(path).open("w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "ID",
                        "项目",
                        "房间",
                        "缴费起始",
                        "缴费结束",
                        "缴纳金额",
                        "实缴日期",
                        "更新时间",
                        "备注",
                    ]
                )
                for p in payments:
                    writer.writerow(
                        [
                            p.id,
                            p.project_name,
                            p.room_no,
                            p.period_start.isoformat(),
                            p.period_end.isoformat(),
                            f"{p.amount:.2f}",
                            p.paid_at.isoformat(),
                            self._format_registered_at(p.updated_at or p.created_at),
                            p.note,
                        ]
                    )
            show_info(f"已导出 {len(payments)} 条记录")
        except OSError as exc:
            show_error(f"导出失败：{exc}")

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
            exclude_payment_id=payment_id,
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
