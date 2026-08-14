from __future__ import annotations

import customtkinter as ctk

from app.models import (
    REMINDER_KIND_CONTRACT_EXPIRED,
    REMINDER_KIND_DEPOSIT,
    REMINDER_KIND_ORDER,
    REMINDER_KIND_RANK,
    REMINDER_KIND_RENT_DUE,
    REMINDER_KIND_RENT_OVERDUE,
)
from app.services import AppServices
from app.ui.utils import format_date_range, format_remaining_due_formula
from app.ui.widgets import DataTable


def _days_display(days_delta: int) -> tuple[str, str]:
    """返回 (展示文案, 行标签)。"""
    if days_delta < 0:
        return f"逾期 {abs(days_delta)} 天", "overdue"
    if days_delta == 0:
        return "今天到期", "today"
    if days_delta <= 3:
        return f"剩余 {days_delta} 天", "urgent"
    # 已进入提醒窗口但仍有一定余量：黄色警示，不用绿色
    return f"剩余 {days_delta} 天", "upcoming"


class HomePage(ctk.CTkFrame):
    def __init__(self, master, services: AppServices, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.services = services
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="提醒看板", font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(header, text="刷新", height=28, width=56, command=self.refresh).grid(
            row=0, column=1, sticky="e", padx=(10, 8)
        )

        self.stats = ctk.CTkFrame(self, fg_color="transparent")
        self.stats.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for col in range(4):
            self.stats.grid_columnconfigure(col, weight=1)
        self._stat_labels: dict[str, ctk.CTkLabel] = {}
        for col, (key, title, color) in enumerate(
            (
                ("total", "全部", "#111827"),
                ("overdue", "租金逾期", "#b91c1c"),
                ("due", "待收款", "#c2410c"),
                ("expire", "合同到期", "#1d4ed8"),
            )
        ):
            card = ctk.CTkFrame(self.stats, corner_radius=8)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
            ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(size=12),
                text_color="#6b7280",
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(10, 0))
            value = ctk.CTkLabel(
                card,
                text="0",
                font=ctk.CTkFont(size=22, weight="bold"),
                text_color=color,
                anchor="w",
            )
            value.pack(anchor="w", padx=14, pady=(2, 12))
            self._stat_labels[key] = value

        self.summary = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
            anchor="w",
        )
        self.summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.table = DataTable(
            self,
            columns=[
                ("days", "紧急度", 120),
                ("kind", "类型", 110),
                ("project", "项目", 120),
                ("room", "房间", 70),
                ("tenant", "租户", 100),
                ("period", "周期", 190),
                ("amount", "关注事项", 220),
            ],
            column_anchors={
                "days": "center",
                "kind": "center",
                "project": "w",
                "room": "center",
                "tenant": "w",
                "period": "center",
                "amount": "w",
            },
            rowheight=34,
            style_prefix="TallyReminder",
            emphasis_columns=("days",),
            fit_content_columns=("amount",),
        )
        self.table.grid(row=3, column=0, sticky="nsew")
        self._configure_day_tags()

    def _configure_day_tags(self) -> None:
        tree = self.table.tree
        tree.tag_configure(
            "overdue",
            foreground="#b91c1c",
            background="#fef2f2",
            font=("PingFang SC", 11, "bold"),
        )
        tree.tag_configure(
            "today",
            foreground="#b45309",
            background="#fffbeb",
            font=("PingFang SC", 11, "bold"),
        )
        tree.tag_configure(
            "urgent",
            foreground="#c2410c",
            background="#fff7ed",
            font=("PingFang SC", 11, "bold"),
        )
        tree.tag_configure(
            "upcoming",
            foreground="#a16207",
            background="#fefce8",
            font=("PingFang SC", 11, "bold"),
        )

    def refresh(self) -> None:
        if not self.services.is_ready or self.services.reminders is None:
            return
        items = self.services.reminders.list_reminders()
        overdue = sum(1 for i in items if i.kind == REMINDER_KIND_RENT_OVERDUE)
        due = sum(1 for i in items if i.kind == REMINDER_KIND_RENT_DUE)
        deposit = sum(1 for i in items if i.kind == REMINDER_KIND_DEPOSIT)
        expire = sum(1 for i in items if i.kind == REMINDER_KIND_CONTRACT_EXPIRED)
        self._stat_labels["total"].configure(text=str(len(items)))
        self._stat_labels["overdue"].configure(text=str(overdue))
        self._stat_labels["due"].configure(text=str(due + deposit))
        self._stat_labels["expire"].configure(text=str(expire))
        if items:
            self.summary.configure(
                text=(
                    "排序："
                    + " → ".join(REMINDER_KIND_ORDER)
                    + "；金额类展示剩余应缴明细，合同到期提示续签"
                )
            )
        else:
            self.summary.configure(text="今日暂无提醒事项")

        items = sorted(
            items,
            key=lambda i: (
                REMINDER_KIND_RANK.get(i.kind, 9),
                i.days_delta,
                i.project_name,
                i.room_no,
            ),
        )

        rows = []
        iids = []
        tags = []
        for idx, item in enumerate(items):
            period = ""
            if item.period_start and item.period_end:
                period = format_date_range(item.period_start, item.period_end)
            days_text, tag = _days_display(item.days_delta)
            rows.append(
                (
                    days_text,
                    item.kind,
                    item.project_name,
                    item.room_no,
                    item.tenant or "",
                    period,
                    self._amount_display(item),
                )
            )
            iids.append(str(idx))
            tags.append(tag)
        self.table.set_rows(rows, iids, tags=tags)

    @staticmethod
    def _amount_display(item):
        if item.kind == REMINDER_KIND_CONTRACT_EXPIRED:
            return "合同即将到期，请提醒续签！"
        if item.kind == REMINDER_KIND_DEPOSIT:
            amount_token = f"({item.amount:.2f})"
            suffix = (
                f"=约定({item.due_amount:.2f})-已收({item.paid_amount:.2f})"
            )
            return [
                ("剩余押金", None),
                (amount_token, "#b91c1c"),
                (suffix, None),
            ]
        # 剩余应缴金额（含英文括号）整段标红，避免拆 Label 产生缝隙
        body = format_remaining_due_formula(
            item.amount,
            item.due_amount,
            item.paid_amount,
            item.free_amount,
            item.discount_amount,
            with_prefix=False,
        )
        amount_token = f"({item.amount:.2f})"
        return [
            ("剩余应缴", None),
            (amount_token, "#b91c1c"),
            (body[len(amount_token) :], None),
        ]
