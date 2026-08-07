from __future__ import annotations

import tkinter as tk
from datetime import date, datetime
from tkinter import ttk
from typing import Callable, Optional, Sequence, Union

import customtkinter as ctk
from tkcalendar import Calendar

from app.ui.utils import center_window


class TimePickerField(ctk.CTkFrame):
    """时间选择器：时、分下拉，值为 HH:MM。"""

    def __init__(
        self,
        master: tk.Misc,
        textvariable: Optional[tk.StringVar] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.var = textvariable or ctk.StringVar(value="09:00")
        self._hours = [f"{h:02d}" for h in range(24)]
        self._minutes = [f"{m:02d}" for m in range(60)]
        self.hour_var = ctk.StringVar(value="09")
        self.minute_var = ctk.StringVar(value="00")

        self.hour_menu = ctk.CTkOptionMenu(
            self,
            variable=self.hour_var,
            values=self._hours,
            width=72,
            command=self._on_changed,
        )
        self.hour_menu.pack(side="left")
        ctk.CTkLabel(self, text=":", width=16).pack(side="left", padx=2)
        self.minute_menu = ctk.CTkOptionMenu(
            self,
            variable=self.minute_var,
            values=self._minutes,
            width=72,
            command=self._on_changed,
        )
        self.minute_menu.pack(side="left")
        self._apply_from_var(self.var.get())
        self.var.trace_add("write", self._on_var_write)

    def get(self) -> str:
        return f"{self.hour_var.get()}:{self.minute_var.get()}"

    def set(self, value: str | None) -> None:
        self._apply_from_var(value or "09:00")
        self.var.set(self.get())

    def configure(self, **kwargs):  # type: ignore[override]
        state = kwargs.pop("state", None)
        if state is not None:
            self.hour_menu.configure(state=state)
            self.minute_menu.configure(state=state)
        if kwargs:
            super().configure(**kwargs)

    def _on_changed(self, _value: str | None = None) -> None:
        self.var.set(self.get())

    def _on_var_write(self, *_args) -> None:
        current = self.get()
        incoming = (self.var.get() or "").strip()
        if incoming == current:
            return
        self._apply_from_var(incoming)

    def _apply_from_var(self, raw: str) -> None:
        text = (raw or "").strip() or "09:00"
        parts = text.split(":")
        hour, minute = "09", "00"
        if len(parts) >= 2:
            try:
                h = max(0, min(23, int(parts[0])))
                m = max(0, min(59, int(parts[1])))
                hour, minute = f"{h:02d}", f"{m:02d}"
            except ValueError:
                pass
        self.hour_var.set(hour)
        self.minute_var.set(minute)


class DatePickerField(ctk.CTkFrame):
    """日期输入框：可手动输入，也可弹出日历选择。"""

    def __init__(
        self,
        master: tk.Misc,
        textvariable: Optional[tk.StringVar] = None,
        allow_empty: bool = False,
        placeholder: str = "YYYY-MM-DD",
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.allow_empty = allow_empty
        self.var = textvariable or ctk.StringVar(value="")
        self.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            self, textvariable=self.var, placeholder_text=placeholder
        )
        self.entry.grid(row=0, column=0, sticky="ew")
        self.pick_btn = ctk.CTkButton(
            self, text="选择", width=56, command=self._open_picker
        )
        self.pick_btn.grid(row=0, column=1, padx=(8, 0))
        if allow_empty:
            self.clear_btn = ctk.CTkButton(
                self,
                text="清空",
                width=56,
                fg_color="#6b7280",
                command=lambda: self.var.set(""),
            )
            self.clear_btn.grid(row=0, column=2, padx=(6, 0))

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: Union[str, date, None]) -> None:
        if value in (None, ""):
            self.var.set("")
            return
        if isinstance(value, date):
            self.var.set(value.isoformat())
            return
        self.var.set(str(value))

    def _parse_current(self) -> date:
        text = self.get()
        if text:
            try:
                return date.fromisoformat(text)
            except ValueError:
                pass
        return date.today()

    def _open_picker(self) -> None:
        current = self._parse_current()
        popup = ctk.CTkToplevel(self)
        popup.title("选择日期")
        popup.resizable(False, False)
        popup.transient(self.winfo_toplevel())
        popup.withdraw()
        popup.geometry("320x340")
        center_window(popup, 320, 340)
        popup.deiconify()
        popup.grab_set()
        popup.lift()
        popup.focus_force()

        # 优先中文 locale；不可用时回退，避免打包环境缺语言包导致弹窗失败
        cal_kwargs = dict(
            master=popup,
            selectmode="day",
            year=current.year,
            month=current.month,
            day=current.day,
            date_pattern="yyyy-mm-dd",
            showweeknumbers=False,
        )
        cal = None
        for locale_name in ("zh_CN", "zh_Hans_CN", "zh"):
            try:
                cal = Calendar(locale=locale_name, **cal_kwargs)
                break
            except Exception:
                cal = None
        if cal is None:
            cal = Calendar(**cal_kwargs)
        cal.pack(fill="both", expand=True, padx=12, pady=(12, 8))

        def confirm() -> None:
            selected = cal.selection_get()
            if isinstance(selected, datetime):
                selected = selected.date()
            self.var.set(selected.isoformat())
            popup.destroy()

        bar = ctk.CTkFrame(popup, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(
            bar, text="取消", width=80, fg_color="#6b7280", command=popup.destroy
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bar, text="确定", width=80, command=confirm).pack(side="right")


class FreePeriodsEditor(ctk.CTkFrame):
    """多时段免租期编辑器。"""

    def __init__(
        self,
        master: tk.Misc,
        initial: Optional[Sequence[tuple[str, str]]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._rows: list[dict[str, object]] = []
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="免租期（可多段）", anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(header, text="添加时段", width=90, command=self.add_row).grid(
            row=0, column=1, sticky="e"
        )

        self.list_frame = ctk.CTkScrollableFrame(self, height=140)
        self.list_frame.grid(row=1, column=0, sticky="nsew")
        self.list_frame.grid_columnconfigure(1, weight=1)
        self.list_frame.grid_columnconfigure(2, weight=1)

        if initial:
            for start, end in initial:
                self.add_row(start, end)
        else:
            self._render_empty()

    def _render_empty(self) -> None:
        if self._rows:
            return
        for child in self.list_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.list_frame,
            text="暂无免租期，可点击「添加时段」",
            text_color="#9ca3af",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=8)

    def add_row(self, start: str = "", end: str = "") -> None:
        self._rows.append(
            {
                "start_var": ctk.StringVar(value=start),
                "end_var": ctk.StringVar(value=end),
            }
        )
        self._rebuild()

    def _rebuild(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not self._rows:
            self._render_empty()
            return
        rebuilt: list[dict[str, object]] = []
        for idx, item in enumerate(self._rows):
            start_var = item["start_var"]
            end_var = item["end_var"]
            assert isinstance(start_var, ctk.StringVar)
            assert isinstance(end_var, ctk.StringVar)
            label = ctk.CTkLabel(self.list_frame, text=f"时段{idx + 1}", width=50)
            start_picker = DatePickerField(
                self.list_frame, textvariable=start_var, placeholder="开始"
            )
            end_picker = DatePickerField(
                self.list_frame, textvariable=end_var, placeholder="结束"
            )

            def make_remove(target_var: ctk.StringVar):
                def _remove() -> None:
                    self._rows = [
                        row for row in self._rows if row["start_var"] is not target_var
                    ]
                    self._rebuild()

                return _remove

            remove_btn = ctk.CTkButton(
                self.list_frame,
                text="删除",
                width=56,
                fg_color="#b91c1c",
                command=make_remove(start_var),
            )
            label.grid(row=idx, column=0, padx=(0, 6), pady=4, sticky="w")
            start_picker.grid(row=idx, column=1, padx=4, pady=4, sticky="ew")
            end_picker.grid(row=idx, column=2, padx=4, pady=4, sticky="ew")
            remove_btn.grid(row=idx, column=3, padx=(6, 0), pady=4, sticky="e")
            rebuilt.append(
                {
                    "start_var": start_var,
                    "end_var": end_var,
                    "label": label,
                    "start_picker": start_picker,
                    "end_picker": end_picker,
                    "remove_btn": remove_btn,
                }
            )
        self._rows = rebuilt

    def get_periods(self) -> list[tuple[date, date]]:
        from app.ui.utils import parse_date

        periods: list[tuple[date, date]] = []
        for idx, item in enumerate(self._rows, start=1):
            start_var = item["start_var"]
            end_var = item["end_var"]
            assert isinstance(start_var, ctk.StringVar)
            assert isinstance(end_var, ctk.StringVar)
            start_text = start_var.get().strip()
            end_text = end_var.get().strip()
            if not start_text and not end_text:
                continue
            if not start_text or not end_text:
                raise ValueError(f"第 {idx} 段免租期起止需同时填写")
            periods.append(
                (
                    parse_date(start_text, f"第 {idx} 段免租期起"),
                    parse_date(end_text, f"第 {idx} 段免租期止"),
                )
            )
        return periods


class DataTable(ctk.CTkFrame):
    """带单元格边框的数据表格（表头/表体同宽对齐）+ 分页。"""

    PAGE_SIZE_OPTIONS = ("10", "20", "50", "100")
    BORDER_COLOR = "#94a3b8"
    HEADER_BG = "#e2e8f0"
    ROW_BG = "#ffffff"
    ROW_BG_ALT = "#f8fafc"
    SELECT_BG = "#dbeafe"
    SCROLL_GUTTER = 16
    CELL_FONT = ("PingFang SC", 11)
    HEADER_FONT = ("PingFang SC", 11, "bold")
    EMPTY_FONT = ("PingFang SC", 11)
    CELL_PADX = 5
    CELL_PADY = 4
    MAX_WRAP_LINES = 4

    def __init__(
        self,
        master: tk.Misc,
        columns: Sequence[tuple[str, str, int]],
        on_select: Optional[Callable[[], None]] = None,
        column_anchors: Optional[dict[str, str]] = None,
        rowheight: int = 34,
        style_prefix: str = "Tally",
        page_size: int = 20,
        enable_pagination: bool = True,
        emphasis_columns: Optional[Sequence[str]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._columns = list(columns)
        self._anchors = column_anchors or {}
        self._on_select = on_select
        self._base_rowheight = rowheight
        self._current_rowheight = rowheight
        self._enable_pagination = enable_pagination
        # 若指定，标签前景色/加粗仅作用于这些列；背景色仍整行生效
        self._emphasis_columns = set(emphasis_columns or [])
        self.page_size = max(1, int(page_size))
        self.page = 1
        self._all_rows: list[list[object]] = []
        self._all_iids: list[str] = []
        self._all_tags: list[str] = []
        self._tag_styles: dict[str, dict[str, object]] = {}
        self._selected_iid: Optional[str] = None
        self._row_widgets: dict[str, list[tk.Frame]] = {}
        self._header_cells: list[tk.Frame] = []
        self._style_prefix = style_prefix
        self._col_minsizes = [max(40, int(width)) for _, _, width in self._columns]

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 外层边框容器：表头置顶，表体紧贴表头
        table_wrap = tk.Frame(
            self,
            bg=self.BORDER_COLOR,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.BORDER_COLOR,
            bd=0,
        )
        table_wrap.grid(row=0, column=0, sticky="nsew")
        self.table_wrap = table_wrap

        # 顶栏：表头置顶；网格线由父级背景色露出
        self.header_bar = tk.Frame(table_wrap, bg=self.BORDER_COLOR, bd=0, highlightthickness=0)
        self.header_bar.pack(side="top", fill="x")
        self.header_bar.grid_columnconfigure(0, weight=1)
        self.header_bar.grid_columnconfigure(1, minsize=self.SCROLL_GUTTER, weight=0)

        self.header = tk.Frame(self.header_bar, bg=self.BORDER_COLOR, bd=0, highlightthickness=0)
        self.header.grid(row=0, column=0, sticky="nsew")
        self._build_header_cells()

        self.header_gutter = tk.Frame(
            self.header_bar,
            bg=self.HEADER_BG,
            width=self.SCROLL_GUTTER,
            bd=0,
            highlightthickness=0,
        )
        self.header_gutter.grid(row=0, column=1, sticky="nsew")
        self.header_gutter.grid_propagate(False)

        # 表体紧贴表头下方（零间距），外框底边由 table_wrap.highlightthickness 保证
        self.body_bar = tk.Frame(table_wrap, bg=self.BORDER_COLOR, bd=0, highlightthickness=0)
        self.body_bar.pack(side="top", fill="both", expand=True, pady=0)
        self.body_bar.grid_rowconfigure(0, weight=1)
        self.body_bar.grid_columnconfigure(0, weight=1)
        self.body_bar.grid_columnconfigure(1, minsize=self.SCROLL_GUTTER, weight=0)

        self.canvas = tk.Canvas(
            self.body_bar,
            background=self.ROW_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(
            self.body_bar, orient="vertical", command=self.canvas.yview
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # 使用 tk.Frame 作为画布窗口，避免 CTk 嵌入偏移导致与表头脱节
        self.body = tk.Frame(self.canvas, bg=self.ROW_BG, bd=0, highlightthickness=0)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw", tags=("body",)
        )
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.table_wrap.bind("<Configure>", self._on_table_configure)
        # 仅绑定本表控件，避免 CTk 禁用的 bind_all，也避免全局滚动副作用
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.body)
        self._bind_wheel(self.table_wrap)
        self._bind_wheel(self.header_bar)
        self._bind_wheel(self.header)
        self._bind_wheel(self.header_gutter)
        self._bind_wheel(self.body_bar)

        self.tree = self  # 兼容 table.tree.tag_configure

        self.pager = ctk.CTkFrame(self, fg_color="transparent")
        self.pager.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        if enable_pagination:
            self._build_pager()
        else:
            self.pager.grid_remove()

        self.after(50, self._sync_column_widths)

    def _bind_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")

    def _on_mousewheel(self, event) -> str:
        if not self.canvas.winfo_exists():
            return "break"
        if not self._can_vertically_scroll():
            return "break"
        if getattr(event, "delta", 0):
            # macOS 触控板 delta 常为小整数；Windows 多为 ±120
            if abs(event.delta) >= 120:
                delta = int(-1 * (event.delta / 120))
            else:
                delta = -1 if event.delta > 0 else 1
        else:
            delta = -1 if getattr(event, "num", 0) == 4 else 1
        self.canvas.yview_scroll(delta, "units")
        self._pin_body_window()
        return "break"

    def _can_vertically_scroll(self) -> bool:
        try:
            first, last = self.canvas.yview()
            return not (first <= 0.0 and last >= 1.0)
        except tk.TclError:
            return False

    def _pin_body_window(self) -> None:
        """将表体窗口钉在画布坐标原点，并固定 scrollregion，避免滚动后与表头脱节。"""
        if not self.canvas.winfo_exists():
            return
        self.canvas.coords(self._canvas_window, 0, 0)
        self.body.update_idletasks()
        width = max(self.canvas.winfo_width(), self.body.winfo_reqwidth(), 1)
        height = max(self.body.winfo_reqheight(), 1)
        # 强制从 (0,0) 起算，避免 bbox 漂移留下顶部空隙
        self.canvas.configure(scrollregion=(0, 0, width, height))
        # 夹紧滚动位置，防止滚出内容区
        first, _last = self.canvas.yview()
        if first < 0:
            self.canvas.yview_moveto(0)
        elif first > 0 and not self._can_vertically_scroll():
            self.canvas.yview_moveto(0)

    def _build_header_cells(self) -> None:
        for child in self.header.winfo_children():
            child.destroy()
        self._header_cells.clear()
        col_count = len(self._columns)
        for idx, (_col_id, heading, _) in enumerate(self._columns):
            self.header.grid_columnconfigure(idx, weight=1, minsize=self._col_minsizes[idx])
            # 父级背景作网格线：右侧/底部分隔 1px
            cell = tk.Frame(self.header, bg=self.HEADER_BG, bd=0, highlightthickness=0)
            cell.grid(
                row=0,
                column=idx,
                sticky="nsew",
                padx=(0, 0 if idx == col_count - 1 else 1),
                pady=(0, 1),
            )
            # 表头统一居中；表体仍按 column_anchors 左/中/右对齐
            label = tk.Label(
                cell,
                text=heading,
                bg=self.HEADER_BG,
                fg="#0f172a",
                font=self.HEADER_FONT,
                anchor="center",
                justify="center",
            )
            label.pack(
                fill="both",
                expand=True,
                padx=self.CELL_PADX,
                pady=self.CELL_PADY,
            )
            self._header_cells.append(cell)
            self._bind_wheel(cell)
            self._bind_wheel(label)

    def _apply_column_minsizes(self) -> None:
        for idx, minsize in enumerate(self._col_minsizes):
            self.header.grid_columnconfigure(idx, weight=1, minsize=minsize)
            self.body.grid_columnconfigure(idx, weight=1, minsize=minsize)

    def _sync_column_widths(self, *_args) -> None:
        """按可用宽度比例分配列宽，表头与表体使用相同 minsize。"""
        available = self.canvas.winfo_width()
        if available <= 1:
            available = self.header.winfo_width()
        if available <= 1:
            return
        weights = [max(1, w) for _, _, w in self._columns]
        total = sum(weights)
        sizes = [max(40, int(available * w / total)) for w in weights]
        diff = available - sum(sizes)
        if sizes:
            sizes[-1] = max(40, sizes[-1] + diff)
        self._col_minsizes = sizes
        self._apply_column_minsizes()
        self.canvas.itemconfigure(self._canvas_window, width=available)
        self._apply_wraplengths()
        self._pin_body_window()

    def _on_table_configure(self, _event=None) -> None:
        self._sync_column_widths()

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._canvas_window, width=max(1, event.width))
        self._pin_body_window()

    def _on_body_configure(self, _event=None) -> None:
        self._pin_body_window()

    @staticmethod
    def _to_anchor(anchor: str) -> str:
        if anchor in {"center", "c"}:
            return "center"
        if anchor in {"e", "east", "right"}:
            return "e"
        return "w"

    @staticmethod
    def _to_justify(anchor: str) -> str:
        if anchor in {"center", "c"}:
            return "center"
        if anchor in {"e", "east", "right"}:
            return "right"
        return "left"

    def tag_configure(self, tag: str, **kwargs) -> None:
        style = self._tag_styles.setdefault(tag, {})
        if "foreground" in kwargs:
            style["foreground"] = kwargs["foreground"]
        if "background" in kwargs:
            style["background"] = kwargs["background"]
        if "font" in kwargs:
            style["font"] = kwargs["font"]

    def _build_pager(self) -> None:
        self.prev_btn = ctk.CTkButton(
            self.pager, text="上一页", width=72, command=self.prev_page
        )
        self.prev_btn.pack(side="left")
        self.next_btn = ctk.CTkButton(
            self.pager, text="下一页", width=72, command=self.next_page
        )
        self.next_btn.pack(side="left", padx=(8, 0))

        self.page_info = ctk.CTkLabel(
            self.pager, text="", text_color="#4b5563", anchor="w"
        )
        self.page_info.pack(side="left", padx=(12, 0))

        right = ctk.CTkFrame(self.pager, fg_color="transparent")
        right.pack(side="right")
        ctk.CTkLabel(right, text="每页", text_color="#6b7280").pack(side="left")
        self.page_size_var = ctk.StringVar(value=str(self.page_size))
        self.page_size_menu = ctk.CTkOptionMenu(
            right,
            values=list(self.PAGE_SIZE_OPTIONS),
            variable=self.page_size_var,
            width=80,
            command=self._on_page_size_changed,
        )
        self.page_size_menu.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(right, text="条", text_color="#6b7280").pack(
            side="left", padx=(4, 0)
        )

    def _on_page_size_changed(self, value: str) -> None:
        try:
            self.page_size = max(1, int(value))
        except ValueError:
            self.page_size = 20
            self.page_size_var.set("20")
        self.page = 1
        self._render_page()

    def set_rowheight(self, rowheight: int) -> None:
        self._base_rowheight = max(28, int(rowheight))
        self._current_rowheight = self._base_rowheight

    def _col_wraplength(self, col_idx: int) -> int:
        minsize = (
            self._col_minsizes[col_idx]
            if col_idx < len(self._col_minsizes)
            else 80
        )
        return max(40, minsize - self.CELL_PADX * 2 - 2)

    def _chars_per_line(self, col_idx: int) -> int:
        # 中文字号约等于像素宽度
        char_px = max(8, int(self.CELL_FONT[1]))
        return max(4, self._col_wraplength(col_idx) // char_px)

    def _estimate_lines(self, text: str, col_idx: int) -> int:
        if not text:
            return 1
        per_line = self._chars_per_line(col_idx)
        total = 0
        for part in str(text).split("\n"):
            total += max(1, (len(part) + per_line - 1) // per_line)
        return min(self.MAX_WRAP_LINES, max(1, total))

    def _auto_rowheight_for(self, rows: Sequence[Sequence[object]]) -> int:
        max_lines = 1
        for row in rows:
            for col_idx, cell in enumerate(row):
                text = "" if cell is None else str(cell)
                max_lines = max(max_lines, self._estimate_lines(text, col_idx))
        line_h = int(self.CELL_FONT[1]) + 7
        return max(self._base_rowheight, line_h * max_lines + self.CELL_PADY * 2)

    def _font_from_style(self, style: dict[str, object]) -> tuple:
        font_spec = style.get("font")
        if isinstance(font_spec, tuple) and font_spec:
            family = str(font_spec[0]) if font_spec[0] else self.CELL_FONT[0]
            size = int(font_spec[1]) if len(font_spec) > 1 else self.CELL_FONT[1]
            # 标签字体不超过默认列表字号，避免撑列裁切
            size = min(size, self.CELL_FONT[1])
            if len(font_spec) > 2 and font_spec[2] == "bold":
                return (family, size, "bold")
            return (family, size)
        return self.CELL_FONT

    def _apply_wraplengths(self) -> None:
        """列宽变化后同步换行宽度，避免文字被裁切。"""
        for cells in self._row_widgets.values():
            for col_idx, cell in enumerate(cells):
                wrap = self._col_wraplength(col_idx)
                for child in cell.winfo_children():
                    if isinstance(child, tk.Label):
                        try:
                            child.configure(wraplength=wrap)
                        except tk.TclError:
                            pass

    def clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self._row_widgets.clear()

    def set_rows(
        self,
        rows: Sequence[Sequence[object]],
        iids: Optional[Sequence[str]] = None,
        tags: Optional[Sequence[str]] = None,
        reset_page: bool = True,
    ) -> None:
        self._all_rows = [list(row) for row in rows]
        if iids is None:
            self._all_iids = [str(i) for i in range(len(self._all_rows))]
        else:
            self._all_iids = [str(i) for i in iids]
        if tags is None:
            self._all_tags = [""] * len(self._all_rows)
        else:
            self._all_tags = list(tags)
        if reset_page:
            self.page = 1
        if self._selected_iid not in self._all_iids:
            self._selected_iid = None
        self._render_page()

    def _total_pages(self) -> int:
        if not self._enable_pagination:
            return 1
        total = len(self._all_rows)
        if total == 0:
            return 1
        return max(1, (total + self.page_size - 1) // self.page_size)

    def _set_cell_bg(self, cell: tk.Frame, bg: str) -> None:
        cell.configure(bg=bg)
        for child in cell.winfo_children():
            try:
                child.configure(bg=bg)
            except tk.TclError:
                pass

    def _select_row(self, iid: str) -> None:
        self._selected_iid = iid
        for row_iid, cells in self._row_widgets.items():
            selected = row_iid == iid
            for cell in cells:
                base_bg = cell._base_bg  # type: ignore[attr-defined]
                self._set_cell_bg(cell, self.SELECT_BG if selected else base_bg)
        if self._on_select:
            self._on_select()

    def _make_cell(
        self,
        parent: tk.Misc,
        text: str,
        *,
        bg: str,
        fg: str,
        font: tuple,
        anchor: str,
        height: int | None = None,
        wraplength: int = 0,
    ) -> tk.Frame:
        cell = tk.Frame(
            parent,
            bg=bg,
            bd=0,
            highlightthickness=0,
            height=height or self._base_rowheight,
        )
        cell._base_bg = bg  # type: ignore[attr-defined]
        if height is not None:
            cell.grid_propagate(False)
            cell.pack_propagate(False)
        label = tk.Label(
            cell,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            anchor=self._to_anchor(anchor),
            justify=self._to_justify(anchor),
            wraplength=wraplength,
        )
        label.pack(
            fill="both",
            expand=True,
            padx=self.CELL_PADX,
            pady=self.CELL_PADY,
        )
        return cell

    def _render_page(self) -> None:
        total = len(self._all_rows)
        total_pages = self._total_pages()
        self.page = min(max(1, self.page), total_pages)

        if self._enable_pagination:
            start = (self.page - 1) * self.page_size
            end = start + self.page_size
        else:
            start, end = 0, total

        page_rows = self._all_rows[start:end]
        page_iids = self._all_iids[start:end]
        page_tags = self._all_tags[start:end]
        self._current_rowheight = self._auto_rowheight_for(page_rows)

        self.clear()
        self._apply_column_minsizes()
        # 父级背景作网格线
        self.body.configure(bg=self.BORDER_COLOR)

        col_count = len(self._columns)
        if not page_rows:
            empty = tk.Frame(self.body, bg=self.ROW_BG, bd=0, highlightthickness=0)
            empty.grid(row=0, column=0, columnspan=col_count, sticky="nsew")
            tk.Label(
                empty,
                text="暂无数据",
                bg=self.ROW_BG,
                fg="#9ca3af",
                font=self.EMPTY_FONT,
            ).pack(fill="both", expand=True, pady=24)
        else:
            for row_idx, row in enumerate(page_rows):
                iid = page_iids[row_idx]
                tag = page_tags[row_idx] if row_idx < len(page_tags) else ""
                style = self._tag_styles.get(tag, {})
                default_bg = self.ROW_BG_ALT if row_idx % 2 else self.ROW_BG
                bg = str(style.get("background") or default_bg)
                fg = str(style.get("foreground") or "#111827")
                font = self._font_from_style(style)
                cells: list[tk.Frame] = []
                for col_idx, (col_id, _, _) in enumerate(self._columns):
                    # 列表 ID 由前端按当前排序从 1 起编号（跨页连续）
                    if col_id == "id":
                        value = start + row_idx + 1
                    else:
                        value = row[col_idx] if col_idx < len(row) else ""
                    anchor = self._anchors.get(col_id, "w")
                    emphasize = (
                        not self._emphasis_columns or col_id in self._emphasis_columns
                    )
                    raw = "" if value is None else str(value)
                    cell = self._make_cell(
                        self.body,
                        raw,
                        bg=bg,
                        fg=fg if emphasize else "#111827",
                        font=font if emphasize else self.CELL_FONT,
                        anchor=anchor,
                        height=self._current_rowheight,
                        wraplength=self._col_wraplength(col_idx),
                    )
                    cell.grid(
                        row=row_idx,
                        column=col_idx,
                        sticky="nsew",
                        padx=(0, 0 if col_idx == col_count - 1 else 1),
                        pady=(0, 1),
                    )
                    label = cell.winfo_children()[0]
                    for widget in (cell, label):
                        widget.bind("<Button-1>", lambda _e, i=iid: self._select_row(i))
                        self._bind_wheel(widget)
                    cells.append(cell)
                self._row_widgets[iid] = cells
                if self._selected_iid == iid:
                    self._select_row(iid)

        self.after_idle(self._sync_column_widths)
        self._pin_body_window()
        self.canvas.yview_moveto(0)


        if self._enable_pagination:
            if total == 0:
                info = "共 0 条"
            else:
                info = f"第 {self.page}/{total_pages} 页，共 {total} 条"
            self.page_info.configure(text=info)
            self.prev_btn.configure(state="normal" if self.page > 1 else "disabled")
            self.next_btn.configure(
                state="normal" if self.page < total_pages else "disabled"
            )

    def prev_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self._render_page()

    def next_page(self) -> None:
        if self.page < self._total_pages():
            self.page += 1
            self._render_page()

    def selected_iid(self) -> Optional[str]:
        return self._selected_iid


class FormDialog(ctk.CTkToplevel):
    def __init__(self, master: tk.Misc, title: str, width: int = 460, height: int = 420) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.withdraw()
        self.geometry(f"{width}x{height}")
        center_window(self, width, height)
        self.deiconify()
        self.grab_set()
        self.lift()
        self.result: Optional[dict] = None

        self.grid_columnconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=20, pady=(20, 10))
        self.body.grid_columnconfigure(1, weight=1)

        self.button_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.button_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 16))
        self.button_bar.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            self.button_bar, text="取消", width=90, fg_color="#6b7280", command=self.destroy
        ).grid(row=0, column=1, padx=(0, 8))
        self.ok_btn = ctk.CTkButton(self.button_bar, text="保存", width=90, command=self._on_ok)
        self.ok_btn.grid(row=0, column=2)

        self.after(50, self._focus)
        self.after(80, lambda: center_window(self, width, height))

    def _focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def add_field(self, row: int, label: str, widget: tk.Misc) -> None:
        ctk.CTkLabel(self.body, text=label, anchor="w", width=120).grid(
            row=row, column=0, sticky="w", pady=8, padx=(0, 8)
        )
        widget.grid(row=row, column=1, sticky="ew", pady=8)

    def _on_ok(self) -> None:
        try:
            self.result = self.collect()
        except Exception as exc:  # noqa: BLE001
            from app.ui.utils import show_error

            show_error(str(exc))
            return
        self.destroy()

    def collect(self) -> dict:
        raise NotImplementedError

    def show(self) -> Optional[dict]:
        self.wait_window()
        return self.result
