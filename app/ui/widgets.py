from __future__ import annotations

import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Optional, Sequence, Union

import customtkinter as ctk
from tkcalendar import Calendar

from app.ui.utils import center_window, is_decimal_input

# 免租期 / 折减列表可视约 3 行（单行含控件与间距约 40px）
PERIOD_LIST_VIEW_HEIGHT = 120
PERIOD_LIST_BG = "#eef2f7"
PERIOD_LIST_RADIUS = 8
PERIOD_LIST_PAD_LEFT = 10
# 表单标签列宽，与免租/折减行标签一致，使输入框与日期左缘对齐
FORM_LABEL_WIDTH = 90
FORM_LABEL_PADX = (0, 8)


def _make_hidden_popup(master: tk.Misc) -> tk.Toplevel:
    """创建已隐藏的原生 Toplevel，避免 CTkToplevel 默认 200x200 方块闪跳。"""
    win = tk.Toplevel(master)
    # 先透明再隐藏，缩短 macOS 上创建瞬间的可见窗口
    try:
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    win.withdraw()
    win.geometry("1x1+-8000+-8000")
    win.transient(master.winfo_toplevel())
    win.overrideredirect(True)
    win.configure(bg="#ffffff")
    return win


def _show_hidden_popup(
    win: tk.Toplevel,
    anchor: tk.Misc,
    *,
    width: int,
    height: int,
    modal: bool = True,
) -> None:
    """布局完成后再显示（先 alpha=0，就位后恢复）。

    modal=False 时不 grab，便于点面板外关闭（类 antd 下拉）。
    """
    try:
        win.update_idletasks()
    except tk.TclError:
        pass
    ax = anchor.winfo_rootx()
    ay = anchor.winfo_rooty() + anchor.winfo_height() + 4
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    x = min(max(0, ax), max(0, screen_w - width))
    y = ay
    if y + height > screen_h - 8:
        y = max(0, anchor.winfo_rooty() - height - 4)
    win.geometry(f"{width}x{height}+{int(x)}+{int(y)}")
    win.deiconify()
    win.lift()
    try:
        win.attributes("-alpha", 1.0)
    except tk.TclError:
        pass
    if modal:
        try:
            win.grab_set()
        except tk.TclError:
            pass
    try:
        win.focus_force()
    except tk.TclError:
        pass


def _bind_popover_dismiss(
    panel: tk.Misc,
    anchor: tk.Misc,
    on_close: Callable[[], None],
) -> Callable[[], None]:
    """点击浮层/锚点外区域，或按 Esc 关闭。返回可安全重复调用的关闭函数。"""
    root = anchor.winfo_toplevel()
    alive = {"yes": True}
    listening = {"yes": False}

    def dismiss(_event=None) -> None:
        if not alive["yes"]:
            return
        alive["yes"] = False
        on_close()

    def _inside_panel(event: tk.Event) -> bool:
        try:
            px = int(panel.winfo_rootx())
            py = int(panel.winfo_rooty())
            pw = int(panel.winfo_width())
            ph = int(panel.winfo_height())
            return px <= int(event.x_root) <= px + pw and py <= int(event.y_root) <= py + ph
        except (tk.TclError, ValueError, TypeError):
            return False

    def _inside_anchor(event: tk.Event) -> bool:
        try:
            ax = int(anchor.winfo_rootx())
            ay = int(anchor.winfo_rooty())
            aw = int(anchor.winfo_width())
            ah = int(anchor.winfo_height())
            return ax <= int(event.x_root) <= ax + aw and ay <= int(event.y_root) <= ay + ah
        except (tk.TclError, ValueError, TypeError):
            return False

    def on_press(event: tk.Event) -> str | None:
        if not alive["yes"] or not listening["yes"]:
            return None
        if _inside_panel(event) or _inside_anchor(event):
            return None
        dismiss()
        return "break"

    root.bind_all("<ButtonPress-1>", on_press, add="+")
    root.bind_all("<Escape>", dismiss, add="+")
    try:
        panel.bind("<Escape>", dismiss)
    except tk.TclError:
        pass
    # 避开打开面板的那次点击，下一拍再开始监听外部关闭
    root.after(50, lambda: listening.__setitem__("yes", True))
    return dismiss


# 缓存可用的日历 locale，避免每次创建时反复失败重试
_CALENDAR_LOCALE: str | None = None
_CALENDAR_LOCALE_READY = False
_TOOLTIP_PATCHED = False


def _patch_calendar_tooltip_flash() -> None:
    """tkcalendar 的 Tooltip 是 Toplevel，创建时会闪出小方块；先隐藏再初始化。"""
    global _TOOLTIP_PATCHED
    if _TOOLTIP_PATCHED:
        return
    from tkcalendar.tooltip import Tooltip

    def __init__(self, parent, **kwargs) -> None:  # noqa: N807
        tk.Toplevel.__init__(self, parent, padx=0, pady=0)
        # 必须先 withdraw，否则 update_idletasks 会把小窗画到主界面上
        self.withdraw()
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.transient(parent)
        self.overrideredirect(True)
        import sys as _sys

        try:
            self.attributes("-alpha", kwargs.pop("alpha", 0.8))
        except tk.TclError:
            kwargs.pop("alpha", None)
        if _sys.platform == "linux":
            try:
                self.attributes("-type", "tooltip")
            except tk.TclError:
                pass
        if not Tooltip._initialized:
            style = ttk.Style(self)
            style.configure(
                "tooltip.TLabel",
                foreground="gray90",
                background="black",
                font="TkDefaultFont 9 bold",
            )
            Tooltip._initialized = True
        kw = {"compound": "left", "style": "tooltip.TLabel", "padding": 4}
        kw.update(kwargs)
        self.label = ttk.Label(self, **kw)
        self.label.pack(fill="both")
        self.config = self.configure

    Tooltip.__init__ = __init__  # type: ignore[method-assign]
    _TOOLTIP_PATCHED = True


def _silence_calendar_tooltip(cal: Calendar) -> None:
    """范围标记不需要事件 tooltip，彻底禁止显示以免再闪方块。"""
    tw = getattr(cal, "tooltip_wrapper", None)
    if tw is None:
        return
    tip = getattr(tw, "tooltip", None)
    if tip is not None:
        try:
            tip.withdraw()
            tip.attributes("-alpha", 0.0)
            tip.geometry("1x1+-8000+-8000")
        except tk.TclError:
            pass
    tw.display_tooltip = lambda: None  # type: ignore[method-assign]
    tw._on_enter = lambda _event=None: None  # type: ignore[method-assign]


def _create_calendar(**cal_kwargs) -> Calendar:
    """创建日历；缓存可用 locale，避免重复失败重试。"""
    global _CALENDAR_LOCALE, _CALENDAR_LOCALE_READY
    _patch_calendar_tooltip_flash()
    if _CALENDAR_LOCALE_READY:
        if _CALENDAR_LOCALE:
            cal = Calendar(locale=_CALENDAR_LOCALE, **cal_kwargs)
        else:
            cal = Calendar(**cal_kwargs)
        _silence_calendar_tooltip(cal)
        return cal
    for locale_name in ("zh_CN", "zh_Hans_CN", "zh"):
        try:
            cal = Calendar(locale=locale_name, **cal_kwargs)
            _CALENDAR_LOCALE = locale_name
            _CALENDAR_LOCALE_READY = True
            _silence_calendar_tooltip(cal)
            return cal
        except Exception:
            continue
    _CALENDAR_LOCALE = None
    _CALENDAR_LOCALE_READY = True
    cal = Calendar(**cal_kwargs)
    _silence_calendar_tooltip(cal)
    return cal


class DecimalEntry(ctk.CTkEntry):
    """仅允许数字与小数点的输入框（金额、折扣数值等）。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        vcmd = (self.register(self._validate), "%P")
        # CTkEntry 将 validate 交给内部 tk Entry
        self.configure(validate="key", validatecommand=vcmd)

    @staticmethod
    def _validate(proposed: str) -> bool:
        return is_decimal_input(proposed)


def bind_entry_max_length(entry: ctk.CTkEntry, max_chars: int) -> None:
    """限制输入框最多 max_chars 个字符（含粘贴）。"""

    def _validate(proposed: str) -> bool:
        return len(proposed) <= max_chars

    vcmd = (entry.register(_validate), "%P")
    entry.configure(validate="key", validatecommand=vcmd)


def _period_list_frame(master: tk.Misc, grid_row: int) -> ctk.CTkScrollableFrame:
    """固定可视高度的时段列表。

    外层用 tk.Frame + grid_propagate(False) 锁高：嵌套在 CTkScrollableFrame
    弹窗内时，CTk 自身的 height 会被内容撑开，必须用 tk 容器约束。
    内层圆角面板铺色，左侧留白 10px。
    """
    wrap = tk.Frame(
        master,
        width=520,
        height=PERIOD_LIST_VIEW_HEIGHT,
        highlightthickness=0,
        bd=0,
        bg="gray92",
    )
    wrap.grid(row=grid_row, column=0, sticky="ew")
    wrap.grid_propagate(False)

    panel = ctk.CTkFrame(
        wrap,
        fg_color=PERIOD_LIST_BG,
        corner_radius=PERIOD_LIST_RADIUS,
    )
    panel.place(x=0, y=0, relwidth=1, relheight=1)

    list_frame = ctk.CTkScrollableFrame(
        panel,
        height=PERIOD_LIST_VIEW_HEIGHT,
        corner_radius=0,
        fg_color=PERIOD_LIST_BG,
    )
    list_frame.pack(fill="both", expand=True, padx=(PERIOD_LIST_PAD_LEFT, 0), pady=0)

    def _sync_width(event: tk.Event) -> None:
        if event.widget is master and event.width > 1:
            wrap.configure(width=event.width, height=PERIOD_LIST_VIEW_HEIGHT)

    master.bind("<Configure>", _sync_width, add="+")
    return list_frame


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
    """日期回显框：点击输入框弹出日历选择。"""

    def __init__(
        self,
        master: tk.Misc,
        textvariable: Optional[tk.StringVar] = None,
        allow_empty: bool = False,
        placeholder: str = "YYYY-MM-DD",
        command: Optional[Callable[[], None]] = None,
        show_clear: Optional[bool] = None,
        entry_width: int | None = None,
        min_date_getter: Optional[Callable[[], Optional[date]]] = None,
        max_date_getter: Optional[Callable[[], Optional[date]]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.allow_empty = allow_empty
        self._command = command
        self._min_date_getter = min_date_getter
        self._max_date_getter = max_date_getter
        self._picker_open = False
        self.var = textvariable or ctk.StringVar(value="")
        self.grid_columnconfigure(0, weight=1)

        entry_kwargs: dict = {
            "textvariable": self.var,
            "placeholder_text": placeholder,
        }
        if entry_width is not None:
            entry_kwargs["width"] = entry_width
        self.entry = ctk.CTkEntry(self, **entry_kwargs)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.configure(state="readonly")
        self._bind_entry_open(self.entry)
        if show_clear is None:
            show_clear = allow_empty
        if show_clear:
            self.clear_btn = ctk.CTkButton(
                self,
                text="清空",
                width=56,
                fg_color="#6b7280",
                command=self._clear,
            )
            self.clear_btn.grid(row=0, column=1, padx=(8, 0))

    def _bind_entry_open(self, entry: ctk.CTkEntry) -> None:
        def open_picker(_event=None):
            self.after_idle(self._open_picker)
            return "break"

        entry.bind("<Button-1>", open_picker)
        try:
            entry.configure(cursor="hand2")
        except Exception:
            pass
        # CTk 内部 tk Entry 也绑定，确保点击命中
        inner = getattr(entry, "_entry", None)
        if inner is not None:
            inner.bind("<Button-1>", open_picker)
            try:
                inner.configure(cursor="hand2", state="readonly")
            except Exception:
                pass

    def _clear(self) -> None:
        self.entry.configure(state="normal")
        self.var.set("")
        self.entry.configure(state="readonly")
        self._notify()

    def _notify(self) -> None:
        if self._command is not None:
            self._command()

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: Union[str, date, None]) -> None:
        self.entry.configure(state="normal")
        if value in (None, ""):
            self.var.set("")
        elif isinstance(value, date):
            self.var.set(value.isoformat())
        else:
            self.var.set(str(value))
        self.entry.configure(state="readonly")

    @staticmethod
    def _parse_optional(text: str) -> Optional[date]:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _parse_current(self) -> date:
        parsed = self._parse_optional(self.get())
        if parsed is not None:
            return parsed
        return date.today()

    def _bound_dates(self) -> tuple[Optional[date], Optional[date]]:
        min_d = self._min_date_getter() if self._min_date_getter else None
        max_d = self._max_date_getter() if self._max_date_getter else None
        return min_d, max_d

    def _open_picker(self) -> None:
        if self._picker_open:
            return
        self._picker_open = True
        current = self._parse_current()
        min_d, max_d = self._bound_dates()
        if min_d is not None and current < min_d:
            current = min_d
        if max_d is not None and current > max_d:
            current = max_d
        original = self.get()

        # 表单弹窗若持有 grab，会挡住「点外部取消」；临时释放，关闭后再恢复
        parent_top = self.winfo_toplevel()
        prev_grab = parent_top.grab_current()
        if prev_grab is not None:
            try:
                prev_grab.grab_release()
            except tk.TclError:
                pass

        # 与双月选择器一致：原生 Toplevel + tkcalendar.Calendar（非模态，支持点外部取消）
        popup_win = _make_hidden_popup(self)
        popup = tk.Frame(popup_win, bg="#ffffff", highlightthickness=0)
        popup.pack(fill="both", expand=True)
        committed = {"yes": False}

        def _destroy() -> None:
            self._picker_open = False
            if not committed["yes"]:
                self.set(original)
            try:
                popup_win.grab_release()
            except Exception:
                pass
            try:
                popup_win.destroy()
            except Exception:
                pass
            if prev_grab is not None:
                try:
                    if prev_grab.winfo_exists():
                        prev_grab.grab_set()
                except tk.TclError:
                    pass

        dismiss = _bind_popover_dismiss(popup_win, self, _destroy)

        cal_kwargs = dict(
            master=popup,
            selectmode="day",
            year=current.year,
            month=current.month,
            day=current.day,
            date_pattern="yyyy-mm-dd",
            showweeknumbers=False,
            font=("PingFang SC", 12),
            background="white",
            foreground="#1f2937",
            headersbackground="#fafafa",
            headersforeground="#6b7280",
            selectbackground="#1677ff",
            selectforeground="white",
            normalbackground="white",
            weekendbackground="#fafafa",
            weekendforeground="#ef4444",
            bordercolor="#f0f0f0",
            borderwidth=0,
            cursor="hand2",
        )
        if min_d is not None:
            cal_kwargs["mindate"] = min_d
        if max_d is not None:
            cal_kwargs["maxdate"] = max_d
        cal = _create_calendar(**cal_kwargs)
        cal.pack(fill="both", expand=True, padx=12, pady=12)

        def apply_selected(_event=None) -> None:
            selected = cal.selection_get()
            if selected is None:
                return
            if isinstance(selected, datetime):
                selected = selected.date()
            if min_d is not None and selected < min_d:
                selected = min_d
            if max_d is not None and selected > max_d:
                selected = max_d
            committed["yes"] = True
            self.set(selected.isoformat())
            dismiss()
            self._notify()

        cal.bind("<<CalendarSelected>>", apply_selected)
        _show_hidden_popup(popup_win, self, width=300, height=280, modal=False)
        try:
            cal.focus_set()
        except tk.TclError:
            pass


class DateRangeField(ctk.CTkFrame):
    """类 Ant Design RangePicker：点击起/止回显框，双月面板点选范围。"""

    def __init__(
        self,
        master: tk.Misc,
        startvariable: Optional[tk.StringVar] = None,
        endvariable: Optional[tk.StringVar] = None,
        start_placeholder: str = "开始日期",
        end_placeholder: str = "结束日期",
        command: Optional[Callable[[], None]] = None,
        entry_width: int = 74,
        allow_empty: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=("gray92", "gray20"), corner_radius=6, **kwargs)
        self._command = command
        self.allow_empty = allow_empty
        self._picker_open = False
        self._start_placeholder = start_placeholder
        self._end_placeholder = end_placeholder
        self.start_var = startvariable or ctk.StringVar(value="")
        self.end_var = endvariable or ctk.StringVar(value="")

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=4, pady=2)

        # 固定槽宽：占位与 YYYY-MM-DD 切换时外框宽度不变
        self.start_label = self._make_fixed_date_label(
            inner, start_placeholder, entry_width
        )
        ctk.CTkLabel(inner, text="~", text_color="#9ca3af", width=16).pack(side="left")
        self.end_label = self._make_fixed_date_label(
            inner, end_placeholder, entry_width
        )
        self._bind_label_open(self.start_label, "start")
        self._bind_label_open(self.end_label, "end")
        self._refresh_display()
        if allow_empty:
            ctk.CTkButton(
                inner,
                text="×",
                width=28,
                height=24,
                fg_color="transparent",
                text_color="#6b7280",
                hover_color=("gray85", "gray30"),
                command=self._clear,
            ).pack(side="left", padx=(2, 0))

    @staticmethod
    def _make_fixed_date_label(
        master: tk.Misc, placeholder: str, width: int
    ) -> ctk.CTkLabel:
        slot = tk.Frame(master, width=width, height=28, highlightthickness=0, bd=0)
        slot.pack(side="left")
        slot.pack_propagate(False)
        label = ctk.CTkLabel(
            slot,
            text=placeholder,
            anchor="center",
            text_color="#9ca3af",
            font=ctk.CTkFont(size=13),
            cursor="hand2",
            fg_color="transparent",
        )
        label.pack(fill="both", expand=True)
        return label

    def _bind_label_open(self, label: ctk.CTkLabel, side: str) -> None:
        def open_picker(_event=None, which: str = side):
            self.after_idle(lambda: self._open_range_picker(active=which))
            return "break"

        label.bind("<Button-1>", open_picker)

    def _refresh_display(self) -> None:
        start = self.start_var.get().strip()
        end = self.end_var.get().strip()
        if start:
            self.start_label.configure(text=start, text_color=("#111827", "#e5e7eb"))
        else:
            self.start_label.configure(
                text=self._start_placeholder, text_color="#9ca3af"
            )
        if end:
            self.end_label.configure(text=end, text_color=("#111827", "#e5e7eb"))
        else:
            self.end_label.configure(text=self._end_placeholder, text_color="#9ca3af")

    def _set_readonly_values(self, start: str, end: str) -> None:
        self.start_var.set(start)
        self.end_var.set(end)
        self._refresh_display()

    def _notify(self) -> None:
        if self._command is not None:
            self._command()

    def _clear(self) -> None:
        self._set_readonly_values("", "")
        self._notify()

    @staticmethod
    def _parse_optional(text: str) -> Optional[date]:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _shift_month(d: date, months: int) -> date:
        from calendar import monthrange

        month = d.month - 1 + months
        year = d.year + month // 12
        month = month % 12 + 1
        day = min(d.day, monthrange(year, month)[1])
        return date(year, month, day)

    def get_range(self) -> tuple[Optional[date], Optional[date]]:
        start = self._parse_optional(self.start_var.get())
        end = self._parse_optional(self.end_var.get())
        if start is not None and end is not None and start > end:
            start, end = end, start
        return start, end

    def _make_calendar(self, master: tk.Misc, current: date) -> Calendar:
        cal_kwargs = dict(
            master=master,
            selectmode="day",
            year=current.year,
            month=current.month,
            date_pattern="yyyy-mm-dd",
            showweeknumbers=False,
            showothermonthdays=False,
            font=("PingFang SC", 12),
            background="white",
            foreground="#1f2937",
            headersbackground="#fafafa",
            headersforeground="#6b7280",
            selectbackground="#1677ff",
            selectforeground="white",
            normalbackground="white",
            weekendbackground="#fafafa",
            weekendforeground="#ef4444",
            othermonthforeground="#d1d5db",
            othermonthbackground="white",
            bordercolor="#f0f0f0",
            borderwidth=0,
            cursor="hand2",
        )
        return _create_calendar(**cal_kwargs)

    def _open_range_picker(self, active: str = "start") -> None:
        del active  # 打开即清空重选，不再区分起/止入口
        if self._picker_open:
            return
        self._picker_open = True
        start0 = self._parse_optional(self.start_var.get())
        end0 = self._parse_optional(self.end_var.get())
        if start0 is not None and end0 is not None and start0 > end0:
            start0, end0 = end0, start0
        # 打开即清空原选择，重新点选；取消时再还原
        original = (
            start0.isoformat() if start0 else "",
            end0.isoformat() if end0 else "",
        )
        self._set_readonly_values("", "")
        focus = start0 or end0 or date.today()
        left_month = date(focus.year, focus.month, 1)
        right_month = self._shift_month(left_month, 1)

        parent_top = self.winfo_toplevel()
        prev_grab = parent_top.grab_current()
        if prev_grab is not None:
            try:
                prev_grab.grab_release()
            except tk.TclError:
                pass

        popup_win = _make_hidden_popup(self)
        popup = tk.Frame(popup_win, bg="#ffffff", highlightthickness=0)
        popup.pack(fill="both", expand=True)
        committed = {"yes": False}

        def _destroy() -> None:
            self._picker_open = False
            if not committed["yes"]:
                self._set_readonly_values(original[0], original[1])
            try:
                popup_win.grab_release()
            except Exception:
                pass
            try:
                popup_win.destroy()
            except Exception:
                pass
            if prev_grab is not None:
                try:
                    if prev_grab.winfo_exists():
                        prev_grab.grab_set()
                except tk.TclError:
                    pass

        dismiss = _bind_popover_dismiss(popup_win, self, _destroy)

        # 主体：双月（全部用原生 tk，避免 CTk 控件在 Toplevel 上画出主页面小方块）
        body = tk.Frame(popup, bg="#ffffff")
        body.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left_card = tk.Frame(
            body,
            width=320,
            height=300,
            bg="#ffffff",
            highlightbackground="#e5e7eb",
            highlightthickness=1,
            bd=0,
        )
        left_card.grid_propagate(False)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(4, 4), pady=4)
        divider = tk.Frame(body, width=1, bg="#f0f0f0")
        divider.grid(row=0, column=1, sticky="ns", pady=12)
        right_card = tk.Frame(
            body,
            width=320,
            height=300,
            bg="#ffffff",
            highlightbackground="#e5e7eb",
            highlightthickness=1,
            bd=0,
        )
        right_card.grid_propagate(False)
        right_card.grid(row=0, column=2, sticky="nsew", padx=(4, 4), pady=4)

        title_font = ("PingFang SC", 14, "bold")
        left_title = tk.Label(
            left_card,
            text=f"{left_month.year}年{left_month.month}月",
            bg="#ffffff",
            fg="#111827",
            font=title_font,
        )
        left_title.pack(pady=(10, 2))
        right_title = tk.Label(
            right_card,
            text=f"{right_month.year}年{right_month.month}月",
            bg="#ffffff",
            fg="#111827",
            font=title_font,
        )
        right_title.pack(pady=(10, 2))

        cal_left = self._make_calendar(left_card, left_month)
        cal_right = self._make_calendar(right_card, right_month)
        cal_left.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        cal_right.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        for cal in (cal_left, cal_right):
            cal.tag_config("range", background="#91caff", foreground="#003eb3")
            cal.tag_config("range_edge", background="#1677ff", foreground="#ffffff")
            try:
                cal.selection_clear()
            except Exception:
                pass

        syncing = {"lock": False}
        months = {"left": left_month, "right": right_month}
        # 每次打开都从空开始重新选起止
        state: dict[str, object] = {
            "start": None,
            "end": None,
            "picking_start": True,
            "anchor_side": None,
        }

        def any_date_selected() -> bool:
            return isinstance(state["start"], date) or isinstance(state["end"], date)

        def selected_month() -> date | None:
            picked = state["start"] if isinstance(state["start"], date) else state["end"]
            if not isinstance(picked, date):
                return None
            return date(picked.year, picked.month, 1)

        def refresh_titles() -> None:
            lm, ly = cal_left.get_displayed_month()
            rm, ry = cal_right.get_displayed_month()
            # get_displayed_month -> (month, year)
            left_title.configure(text=f"{ly}年{lm}月")
            right_title.configure(text=f"{ry}年{rm}月")
            months["left"] = date(ly, lm, 1)
            months["right"] = date(ry, rm, 1)

        def update_nav_enabled() -> None:
            sel_m = selected_month()
            side = state["anchor_side"]
            left_next_ok = True
            right_prev_ok = True
            if sel_m is not None and side == "left":
                right_prev_ok = months["right"] > sel_m
            elif sel_m is not None and side == "right":
                left_next_ok = months["left"] < sel_m
            left_next_btn.configure(state=("normal" if left_next_ok else "disabled"))
            right_prev_btn.configure(state=("normal" if right_prev_ok else "disabled"))

        def show_linked(left: date) -> None:
            """未选日期时左右保持相邻月份并一起翻。"""
            syncing["lock"] = True
            try:
                cal_left.see(left)
                cal_right.see(self._shift_month(left, 1))
                refresh_titles()
                mark_range()
                update_nav_enabled()
            finally:
                syncing["lock"] = False

        def show_side(side: str, month: date) -> None:
            """已有选中日期时只切换一侧。"""
            sel_m = selected_month()
            anchor = state["anchor_side"]
            if sel_m is not None:
                if side == "right" and anchor == "left" and month < sel_m:
                    month = sel_m
                if side == "left" and anchor == "right" and month > sel_m:
                    month = sel_m
            syncing["lock"] = True
            try:
                if side == "left":
                    cal_left.see(month)
                else:
                    cal_right.see(month)
                refresh_titles()
                mark_range()
                update_nav_enabled()
            finally:
                syncing["lock"] = False

        def shift_side(side: str, delta: int) -> None:
            if any_date_selected():
                show_side(side, self._shift_month(months[side], delta))
            else:
                show_linked(self._shift_month(months["left"], delta))

        nav_kwargs = dict(
            relief="flat",
            bg="#ffffff",
            fg="#6b7280",
            activebackground="#f3f4f6",
            activeforeground="#111827",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=("PingFang SC", 14),
            padx=4,
            pady=0,
        )
        left_prev_btn = tk.Button(
            left_card, text="‹", command=lambda: shift_side("left", -1), **nav_kwargs
        )
        left_prev_btn.place(relx=0.02, y=4, anchor="nw")
        left_next_btn = tk.Button(
            left_card, text="›", command=lambda: shift_side("left", 1), **nav_kwargs
        )
        left_next_btn.place(relx=0.98, y=4, anchor="ne")
        right_prev_btn = tk.Button(
            right_card, text="‹", command=lambda: shift_side("right", -1), **nav_kwargs
        )
        right_prev_btn.place(relx=0.02, y=4, anchor="nw")
        right_next_btn = tk.Button(
            right_card, text="›", command=lambda: shift_side("right", 1), **nav_kwargs
        )
        right_next_btn.place(relx=0.98, y=4, anchor="ne")

        def apply_date_bounds() -> None:
            picked = state["start"] if isinstance(state["start"], date) else state["end"]
            side = state["anchor_side"]
            try:
                if side == "left" and isinstance(picked, date):
                    cal_right.configure(mindate=picked, maxdate=None)
                    cal_left.configure(mindate=None, maxdate=None)
                elif side == "right" and isinstance(picked, date):
                    cal_left.configure(mindate=None, maxdate=picked)
                    cal_right.configure(mindate=None, maxdate=None)
                else:
                    cal_left.configure(mindate=None, maxdate=None)
                    cal_right.configure(mindate=None, maxdate=None)
            except Exception:
                pass

        def on_month_changed(_event=None, source: str = "left") -> None:
            if syncing["lock"]:
                return
            if any_date_selected():
                refresh_titles()
                sel_m = selected_month()
                side = state["anchor_side"]
                if sel_m is not None:
                    if source == "right" and side == "left" and months["right"] < sel_m:
                        show_side("right", sel_m)
                        return
                    if source == "left" and side == "right" and months["left"] > sel_m:
                        show_side("left", sel_m)
                        return
                mark_range()
                update_nav_enabled()
                return
            syncing["lock"] = True
            try:
                if source == "left":
                    lm, ly = cal_left.get_displayed_month()
                    cal_right.see(self._shift_month(date(ly, lm, 1), 1))
                else:
                    rm, ry = cal_right.get_displayed_month()
                    cal_left.see(self._shift_month(date(ry, rm, 1), -1))
                refresh_titles()
                mark_range()
                update_nav_enabled()
            finally:
                syncing["lock"] = False

        cal_left.bind("<<CalendarMonthChanged>>", lambda e: on_month_changed(e, "left"))
        cal_right.bind("<<CalendarMonthChanged>>", lambda e: on_month_changed(e, "right"))

        def sync_external() -> None:
            start = state["start"]
            end = state["end"]
            self._set_readonly_values(
                start.isoformat() if isinstance(start, date) else "",
                end.isoformat() if isinstance(end, date) else "",
            )

        def clear_marks() -> None:
            for cal in (cal_left, cal_right):
                try:
                    for eid in list(cal.get_calevents()):
                        cal.calevent_remove(eid)
                except Exception:
                    pass

        def mark_range() -> None:
            clear_marks()
            start = state["start"]
            end = state["end"]
            assert start is None or isinstance(start, date)
            assert end is None or isinstance(end, date)
            sync_external()
            if start is None and end is None:
                return
            if start is None and isinstance(end, date):
                for cal in (cal_left, cal_right):
                    cal.calevent_create(end, "end", "range_edge")
                return
            if end is None and isinstance(start, date):
                for cal in (cal_left, cal_right):
                    cal.calevent_create(start, "start", "range_edge")
                return
            if start is None or end is None:
                return
            if start > end:
                start, end = end, start
                state["start"], state["end"] = start, end
                sync_external()
            day = start
            while day <= end:
                tag = "range_edge" if day in (start, end) else "range"
                for cal in (cal_left, cal_right):
                    cal.calevent_create(day, "range", tag)
                day += timedelta(days=1)

        def apply_and_close() -> None:
            start = state["start"]
            end = state["end"]
            if not isinstance(start, date):
                return
            if not isinstance(end, date):
                end = start
            if start > end:
                start, end = end, start
            committed["yes"] = True
            self._set_readonly_values(start.isoformat(), end.isoformat())
            dismiss()
            self._notify()

        def on_day_selected(_event=None, source: Calendar | None = None) -> None:
            cal = source or cal_left
            selected = cal.selection_get()
            if selected is None:
                return
            if isinstance(selected, datetime):
                selected = selected.date()
            try:
                cal.selection_clear()
            except Exception:
                pass
            clicked = "left" if source is cal_left else "right"
            if state["picking_start"] or (
                state["start"] is None and state["end"] is None
            ):
                state["anchor_side"] = clicked
                if clicked == "left":
                    state["start"] = selected
                    state["end"] = None
                else:
                    state["start"] = None
                    state["end"] = selected
                state["picking_start"] = False
                apply_date_bounds()
                mark_range()
                update_nav_enabled()
                return
            if state["anchor_side"] == "left":
                start = state["start"]
                assert isinstance(start, date)
                if selected < start:
                    return
                state["end"] = selected
            else:
                end = state["end"]
                assert isinstance(end, date)
                if selected > end:
                    return
                state["start"] = selected
            mark_range()
            popup.after(150, apply_and_close)

        cal_left.bind("<<CalendarSelected>>", lambda e: on_day_selected(e, cal_left))
        cal_right.bind("<<CalendarSelected>>", lambda e: on_day_selected(e, cal_right))
        refresh_titles()
        mark_range()
        update_nav_enabled()
        _show_hidden_popup(popup_win, self, width=680, height=340, modal=False)


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
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="免租期（可多段）", anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(header, text="添加时段", width=90, command=self.add_row).grid(
            row=0, column=1, sticky="e"
        )
        ctk.CTkLabel(
            self,
            text="免租金额无需填写：按免租起止与计费月重叠天数比例自动计算（月租×重叠天数÷计费月天数）",
            text_color="#6b7280",
            anchor="w",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        self.list_frame = _period_list_frame(self, grid_row=2)
        self.list_frame.grid_columnconfigure(1, weight=1)

        if initial:
            for start, end, *_rest in initial:
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
            fg_color="transparent",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=8)

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
            label = ctk.CTkLabel(
                self.list_frame,
                text=f"免租时段{idx + 1}",
                width=FORM_LABEL_WIDTH,
                anchor="w",
                fg_color="transparent",
            )
            range_picker = DateRangeField(
                self.list_frame,
                startvariable=start_var,
                endvariable=end_var,
                start_placeholder="免租开始",
                end_placeholder="免租截止",
                entry_width=74,
                allow_empty=True,
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
            label.grid(row=idx, column=0, padx=FORM_LABEL_PADX, pady=4, sticky="w")
            range_picker.grid(row=idx, column=1, padx=0, pady=4, sticky="w")
            remove_btn.grid(row=idx, column=2, padx=(6, 0), pady=4, sticky="e")
            rebuilt.append(
                {
                    "start_var": start_var,
                    "end_var": end_var,
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


class DiscountAddDialog(ctk.CTkToplevel):
    """按租赁月周期添加折/减：统一选折扣或立减，支持一次添加连续多个月周期。"""

    KIND_RATE_LABEL = "折扣"
    KIND_AMOUNT_LABEL = "立减"
    KIND_LABELS = (KIND_RATE_LABEL, KIND_AMOUNT_LABEL)

    def __init__(
        self,
        master: tk.Misc,
        *,
        lease_start: date,
        lease_end: date,
        existing_months: set[tuple[int, int]],
        monthly_rent: float,
    ) -> None:
        super().__init__(master)
        # 先隐藏再布局，避免默认位置闪一下后跳到居中
        self.withdraw()
        self.title("添加折/减")
        self.resizable(False, False)
        parent = master.winfo_toplevel()
        self.transient(parent)
        self.result: list[tuple[date, date, str, str]] | None = None
        self.lease_start = lease_start
        self.lease_end = lease_end
        self.existing_months = existing_months
        self.monthly_rent = float(monthly_rent)
        self._parent_toplevel = parent

        from app.ui.utils import iter_lease_billing_months

        self.billing_months = iter_lease_billing_months(lease_start, lease_end)

        width, height = 420, 360
        self.geometry(f"{width}x{height}")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(18, 10))
        body.grid_columnconfigure(1, weight=1)

        month_choices = [
            f"{s.year:04d}-{s.month:02d}" for s, _e in self.billing_months
        ]
        if not month_choices:
            month_choices = [f"{lease_start.year:04d}-{lease_start.month:02d}"]
        default_month = month_choices[0]

        self.kind_var = ctk.StringVar(value=self.KIND_RATE_LABEL)
        self.value_var = ctk.StringVar(value="")
        self.start_month_var = ctk.StringVar(value=default_month)
        self.end_month_var = ctk.StringVar(value=default_month)

        def add_row(row: int, label: str, widget: tk.Misc) -> None:
            ctk.CTkLabel(body, text=label, anchor="w", width=90).grid(
                row=row, column=0, sticky="w", pady=8, padx=(0, 8)
            )
            widget.grid(row=row, column=1, sticky="ew", pady=8)

        add_row(
            0,
            "类型",
            ctk.CTkOptionMenu(
                body, values=list(self.KIND_LABELS), variable=self.kind_var, width=200
            ),
        )
        add_row(
            1,
            "数值",
            DecimalEntry(
                body,
                textvariable=self.value_var,
                placeholder_text="折扣如 0.85；立减如 200",
            ),
        )
        add_row(
            2,
            "起始月份",
            ctk.CTkOptionMenu(
                body, values=month_choices, variable=self.start_month_var, width=200
            ),
        )
        add_row(
            3,
            "结束月份",
            ctk.CTkOptionMenu(
                body, values=month_choices, variable=self.end_month_var, width=200
            ),
        )
        example_end = (
            self.billing_months[0][1].isoformat()
            if self.billing_months
            else lease_end.isoformat()
        )
        ctk.CTkLabel(
            body,
            text=(
                f"按租赁月周期添加（起租日对齐）；"
                f"如选 {lease_start.year:04d}-{lease_start.month:02d} "
                f"对应 {lease_start.isoformat()} ~ {example_end}；"
                f"立减不得超过该月周期应缴（非完整月按天折算，完整月上限 {self.monthly_rent:.2f}）"
            ),
            text_color="#6b7280",
            anchor="w",
            font=ctk.CTkFont(size=12),
            wraplength=360,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(
            bar, text="取消", width=90, fg_color="#6b7280", command=self.destroy
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bar, text="添加", width=90, command=self._on_ok).pack(side="right")

        center_window(self, width, height)
        self.deiconify()
        self.grab_set()
        self.lift()
        self.focus_force()

    @staticmethod
    def _parse_month(text: str, field_name: str) -> tuple[int, int]:
        raw = (text or "").strip()
        try:
            year_s, month_s = raw.split("-", 1)
            year, month = int(year_s), int(month_s)
            if not (1 <= month <= 12):
                raise ValueError
            return year, month
        except ValueError as exc:
            raise ValueError(f"{field_name}格式应为 YYYY-MM") from exc

    def _on_ok(self) -> None:
        from app.models import DISCOUNT_KIND_AMOUNT, DISCOUNT_KIND_RATE
        from app.ui.utils import parse_float, show_error

        try:
            value_text = self.value_var.get().strip()
            if not value_text:
                raise ValueError("折/减数值不能为空")
            kind_label = self.kind_var.get().strip()
            if kind_label == self.KIND_AMOUNT_LABEL:
                kind = DISCOUNT_KIND_AMOUNT
            else:
                kind = DISCOUNT_KIND_RATE
            numeric = parse_float(value_text, "折/减数值")
            if kind == DISCOUNT_KIND_RATE and not (0 < numeric < 1):
                raise ValueError("折扣须大于 0 且小于 1（如 0.85 表示实付 85%）")
            if kind == DISCOUNT_KIND_AMOUNT and numeric <= 0:
                raise ValueError("立减金额须大于 0")

            start_y, start_m = self._parse_month(self.start_month_var.get(), "起始月份")
            end_y, end_m = self._parse_month(self.end_month_var.get(), "结束月份")
            if (end_y, end_m) < (start_y, start_m):
                raise ValueError("结束月份不能早于起始月份")

            from app.ui.utils import billing_month_gross_rent

            rows: list[tuple[date, date, str, str]] = []
            for slice_start, slice_end in self.billing_months:
                key = (slice_start.year, slice_start.month)
                if key < (start_y, start_m) or key > (end_y, end_m):
                    continue
                if key in self.existing_months:
                    continue
                if kind == DISCOUNT_KIND_AMOUNT:
                    cap = billing_month_gross_rent(
                        self.monthly_rent,
                        self.lease_start,
                        slice_start,
                        slice_end,
                    )
                    if numeric > cap + 1e-9:
                        raise ValueError(
                            f"立减金额不能超过该月周期应缴"
                            f"（{slice_start} ~ {slice_end} 应缴 {cap:.2f}）"
                        )
                rows.append((slice_start, slice_end, kind, f"{numeric:.2f}"))
            if not rows:
                raise ValueError("所选月份均已添加或不在租赁期内")
            self.result = rows
            self.destroy()
        except Exception as exc:  # noqa: BLE001
            show_error(str(exc), parent=self)
            try:
                if self.winfo_exists():
                    self.grab_set()
                    self.lift()
            except tk.TclError:
                pass

    def show(self) -> list[tuple[date, date, str, str]] | None:
        self.wait_window()
        # 关闭后把模态焦点还给新建/编辑租赁窗，避免父窗 grab 丢失
        parent = getattr(self, "_parent_toplevel", None)
        if parent is not None:
            try:
                if parent.winfo_exists():
                    parent.grab_set()
                    parent.lift()
            except tk.TclError:
                pass
        return self.result


class DiscountPeriodsEditor(ctk.CTkFrame):
    """按租赁月周期添加折/减：统一选择折扣或立减，支持一次添加多个月周期。"""

    KIND_RATE_LABEL = "折扣"
    KIND_AMOUNT_LABEL = "立减"
    KIND_LABELS = (KIND_RATE_LABEL, KIND_AMOUNT_LABEL)

    def __init__(
        self,
        master: tk.Misc,
        initial: Optional[Sequence[tuple[str, str, str, str]]] = None,
        *,
        lease_start_var: Optional[tk.StringVar] = None,
        lease_end_var: Optional[tk.StringVar] = None,
        monthly_rent_var: Optional[tk.StringVar] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._rows: list[dict[str, object]] = []
        self.lease_start_var = lease_start_var
        self.lease_end_var = lease_end_var
        self.monthly_rent_var = monthly_rent_var
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="折/减（月周期）", anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(header, text="添加月份", width=90, command=self.open_add_dialog).grid(
            row=0, column=1, sticky="e"
        )
        ctk.CTkLabel(
            self,
            text="按起租日对齐的租赁月周期添加；立减不得超过该月周期应缴（非完整月按天折算）",
            text_color="#6b7280",
            anchor="w",
            justify="left",
            wraplength=500,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))

        self.list_frame = _period_list_frame(self, grid_row=2)
        self.list_frame.grid_columnconfigure(2, weight=1)

        if initial:
            for start, end, kind, value in initial:
                self._append_row(
                    str(start)[:10],
                    str(end)[:10],
                    self._kind_label(kind),
                    str(value),
                )
            self._rebuild()
        else:
            self._render_empty()

    def _kind_label(self, kind: str) -> str:
        if kind in self.KIND_LABELS:
            return kind
        if kind == "rate" or kind == self.KIND_RATE_LABEL:
            return self.KIND_RATE_LABEL
        if kind == "amount" or kind == self.KIND_AMOUNT_LABEL:
            return self.KIND_AMOUNT_LABEL
        # 兼容旧文案「折扣率」
        if kind == "折扣率":
            return self.KIND_RATE_LABEL
        return self.KIND_RATE_LABEL

    def _append_row(
        self, start: str, end: str, kind_label: str, value: str
    ) -> None:
        self._rows.append(
            {
                "start_var": ctk.StringVar(value=start),
                "end_var": ctk.StringVar(value=end),
                "kind_var": ctk.StringVar(value=kind_label),
                "value_var": ctk.StringVar(value=value),
            }
        )

    def _existing_months(self) -> set[tuple[int, int]]:
        months: set[tuple[int, int]] = set()
        for item in self._rows:
            start_var = item["start_var"]
            assert isinstance(start_var, ctk.StringVar)
            text = start_var.get().strip()
            if len(text) >= 7:
                try:
                    year, month = int(text[:4]), int(text[5:7])
                    months.add((year, month))
                except ValueError:
                    continue
        return months

    def _lease_bounds(self) -> tuple[date, date]:
        from app.ui.utils import parse_date

        if self.lease_start_var is None or self.lease_end_var is None:
            raise ValueError("请先填写起租时间与到期时间")
        start = parse_date(self.lease_start_var.get(), "起租时间")
        end = parse_date(self.lease_end_var.get(), "到期时间")
        if end < start:
            raise ValueError("到期时间不能早于起租时间")
        return start, end

    def _monthly_rent(self) -> float:
        from app.ui.utils import parse_float

        if self.monthly_rent_var is None:
            raise ValueError("请先填写月租金")
        rent = parse_float(self.monthly_rent_var.get(), "月租金")
        if rent < 0:
            raise ValueError("月租金不能为负数")
        return rent

    def open_add_dialog(self) -> None:
        from app.ui.utils import show_error

        try:
            lease_start, lease_end = self._lease_bounds()
            monthly_rent = self._monthly_rent()
        except ValueError as exc:
            show_error(str(exc))
            return
        dialog = DiscountAddDialog(
            self,
            lease_start=lease_start,
            lease_end=lease_end,
            existing_months=self._existing_months(),
            monthly_rent=monthly_rent,
        )
        rows = dialog.show()
        if not rows:
            return
        for start, end, kind, value in rows:
            self._append_row(
                start.isoformat(),
                end.isoformat(),
                self._kind_label(kind),
                value,
            )
        self._rows.sort(
            key=lambda item: str(item["start_var"].get())  # type: ignore[union-attr]
        )
        self._rebuild()

    def _render_empty(self) -> None:
        if self._rows:
            return
        for child in self.list_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.list_frame,
            text="暂无折/减，可点击「添加月份」",
            text_color="#9ca3af",
            fg_color="transparent",
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=8)

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
            kind_var = item["kind_var"]
            value_var = item["value_var"]
            assert isinstance(start_var, ctk.StringVar)
            assert isinstance(end_var, ctk.StringVar)
            assert isinstance(kind_var, ctk.StringVar)
            assert isinstance(value_var, ctk.StringVar)

            start_text = start_var.get().strip()
            end_text = end_var.get().strip()
            if start_text and end_text:
                period_text = f"{start_text}\u00a0~\u00a0{end_text}"
            else:
                period_text = start_text[:7] if start_text else "—"
            label = ctk.CTkLabel(
                self.list_frame,
                text=f"折/减时段{idx + 1}",
                width=FORM_LABEL_WIDTH,
                anchor="w",
                fg_color="transparent",
            )
            month_label = ctk.CTkLabel(
                self.list_frame,
                text=period_text,
                width=168,
                anchor="w",
                fg_color="transparent",
            )
            kind_label = ctk.CTkLabel(
                self.list_frame,
                text=kind_var.get(),
                width=84,
                anchor="w",
                fg_color="transparent",
            )
            value_label = ctk.CTkLabel(
                self.list_frame,
                text=value_var.get().strip() or "—",
                width=100,
                anchor="w",
                fg_color="transparent",
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
            label.grid(row=idx, column=0, padx=FORM_LABEL_PADX, pady=4, sticky="w")
            month_label.grid(row=idx, column=1, padx=0, pady=4, sticky="w")
            kind_label.grid(row=idx, column=2, padx=(8, 0), pady=4, sticky="w")
            value_label.grid(row=idx, column=3, padx=(8, 0), pady=4, sticky="ew")
            remove_btn.grid(row=idx, column=4, padx=(8, 0), pady=4, sticky="e")
            rebuilt.append(
                {
                    "start_var": start_var,
                    "end_var": end_var,
                    "kind_var": kind_var,
                    "value_var": value_var,
                }
            )
        self._rows = rebuilt

    def get_discounts(self) -> list[tuple[date, date, str, float]]:
        from app.models import DISCOUNT_KIND_AMOUNT, DISCOUNT_KIND_RATE
        from app.ui.utils import billing_month_gross_rent, parse_date, parse_float

        monthly_rent = self._monthly_rent()
        lease_bounds: tuple[date, date] | None = None
        discounts: list[tuple[date, date, str, float]] = []
        for idx, item in enumerate(self._rows, start=1):
            start_var = item["start_var"]
            end_var = item["end_var"]
            kind_var = item["kind_var"]
            value_var = item["value_var"]
            assert isinstance(start_var, ctk.StringVar)
            assert isinstance(end_var, ctk.StringVar)
            assert isinstance(kind_var, ctk.StringVar)
            assert isinstance(value_var, ctk.StringVar)
            start_text = start_var.get().strip()
            end_text = end_var.get().strip()
            value_text = value_var.get().strip()
            if not start_text and not end_text and not value_text:
                continue
            if not start_text or not end_text:
                raise ValueError(f"第 {idx} 条折/减月份不完整")
            if not value_text:
                raise ValueError(f"第 {idx} 条折/减数值不能为空")
            kind_label = kind_var.get().strip()
            if kind_label == self.KIND_AMOUNT_LABEL:
                kind = DISCOUNT_KIND_AMOUNT
            else:
                kind = DISCOUNT_KIND_RATE
            numeric = parse_float(value_text, f"第 {idx} 条折/减数值")
            if kind == DISCOUNT_KIND_RATE and not (0 < numeric < 1):
                raise ValueError(
                    f"第 {idx} 条折扣须大于 0 且小于 1（如 0.85 表示实付 85%）"
                )
            if kind == DISCOUNT_KIND_AMOUNT and numeric <= 0:
                raise ValueError(f"第 {idx} 条立减金额须大于 0")
            d_start = parse_date(start_text, f"第 {idx} 条折/减起")
            d_end = parse_date(end_text, f"第 {idx} 条折/减止")
            if kind == DISCOUNT_KIND_AMOUNT:
                if lease_bounds is None:
                    lease_bounds = self._lease_bounds()
                cap = billing_month_gross_rent(
                    monthly_rent, lease_bounds[0], d_start, d_end
                )
                if numeric > cap + 1e-9:
                    raise ValueError(
                        f"第 {idx} 条立减金额不能超过该月周期应缴"
                        f"（{d_start} ~ {d_end} 应缴 {cap:.2f}）"
                    )
            discounts.append((d_start, d_end, kind, numeric))
        return discounts


class DataTable(ctk.CTkFrame):
    """带单元格边框的数据表格（表头/表体同宽对齐）+ 分页。

    超长文本按列宽自动换行；时间区间使用不间断空格尽量保持整段。
    列总宽超出可视区时底部提供横向滚动。
    """

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
    MAX_WRAP_LINES = 6

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
        fit_content_columns: Optional[Sequence[str]] = None,
        cell_pady: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._columns = list(columns)
        self._anchors = column_anchors or {}
        self._on_select = on_select
        self._base_rowheight = max(22, int(rowheight))
        self._current_rowheight = self._base_rowheight
        self._enable_pagination = enable_pagination
        # 若指定，标签前景色/加粗仅作用于这些列；背景色仍整行生效
        self._emphasis_columns = set(emphasis_columns or [])
        # 按当前页正文单行宽度撑开列宽（不换行；超出可视区时横向滚动）
        self._fit_content_columns = set(fit_content_columns or [])
        self._cell_pady = (
            self.CELL_PADY if cell_pady is None else max(0, int(cell_pady))
        )
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
        self._declared_widths = [max(40, int(width)) for _, _, width in self._columns]
        self._col_minsizes = list(self._declared_widths)
        self._table_content_width = sum(self._col_minsizes)
        self._scroll_gutter = self.SCROLL_GUTTER
        self._cell_font = tkfont.Font(font=self.CELL_FONT)
        self._header_font = tkfont.Font(font=self.HEADER_FONT)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 外层边框：表头 / 表体 / 底栏横向滚动
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
        table_wrap.grid_rowconfigure(1, weight=1)
        table_wrap.grid_columnconfigure(0, weight=1)

        self.header_canvas = tk.Canvas(
            table_wrap,
            background=self.HEADER_BG,
            highlightthickness=0,
            bd=0,
            height=self._header_min_height(),
        )
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        self.header_gutter = tk.Frame(
            table_wrap,
            bg=self.HEADER_BG,
            width=self._scroll_gutter,
            bd=0,
            highlightthickness=0,
        )
        self.header_gutter.grid(row=0, column=1, sticky="ns")
        self.header_gutter.grid_propagate(False)
        table_wrap.grid_columnconfigure(1, minsize=self._scroll_gutter, weight=0)

        self.header = tk.Frame(
            self.header_canvas, bg=self.BORDER_COLOR, bd=0, highlightthickness=0
        )
        self._header_window = self.header_canvas.create_window(
            (0, 0), window=self.header, anchor="nw", tags=("header",)
        )
        self._build_header_cells()

        self.canvas = tk.Canvas(
            table_wrap,
            background=self.ROW_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(
            table_wrap, orient="vertical", command=self.canvas.yview
        )
        self.scrollbar.grid(row=1, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.h_scrollbar = ttk.Scrollbar(
            table_wrap, orient="horizontal", command=self._xview_both
        )
        self.h_scrollbar.grid(row=2, column=0, sticky="ew")
        self._hscroll_corner = tk.Frame(
            table_wrap,
            bg=self.HEADER_BG,
            width=self._scroll_gutter,
            bd=0,
            highlightthickness=0,
        )
        self._hscroll_corner.grid(row=2, column=1, sticky="nsew")
        self._hscroll_corner.grid_propagate(False)

        # 使用 tk.Frame 作为画布窗口，避免 CTk 嵌入偏移导致与表头脱节
        self.body = tk.Frame(self.canvas, bg=self.ROW_BG, bd=0, highlightthickness=0)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw", tags=("body",)
        )
        self.body.bind("<Configure>", self._on_body_configure)
        self.header.bind("<Configure>", self._on_header_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.header_canvas.bind("<Configure>", self._on_header_canvas_configure)
        self.table_wrap.bind("<Configure>", self._on_table_configure)
        self.canvas.configure(xscrollcommand=self._on_body_xscroll)
        self.header_canvas.configure(xscrollcommand=self._on_header_xscroll)

        # 仅绑定本表控件，避免 CTk 禁用的 bind_all，也避免全局滚动副作用
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.body)
        self._bind_wheel(self.table_wrap)
        self._bind_wheel(self.header_canvas)
        self._bind_wheel(self.header)
        self._bind_wheel(self.header_gutter)

        self.tree = self  # 兼容 table.tree.tag_configure
        self._sync_after_id: str | None = None
        self._sync_follow_id: str | None = None
        self._last_viewport_width = 0
        self._syncing_widths = False

        self.pager = ctk.CTkFrame(self, fg_color="transparent")
        self.pager.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        if enable_pagination:
            self._build_pager()
        else:
            self.pager.grid_remove()

        self.after(50, self._sync_column_widths)
        # 根窗口尺寸变化（含全屏）时补一次同步，避免仅切换菜单才恢复
        self.bind("<Configure>", self._on_self_configure, add="+")
        # Windows 滚动条宽度常与固定 gutter 不一致，映射后再实测对齐
        self.after(80, self._sync_scroll_gutter)
        self.after(200, self._sync_scroll_gutter)

    def _bind_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")

    def _scroll_delta(self, event) -> int:
        if getattr(event, "delta", 0):
            # macOS 触控板 delta 常为小整数；Windows 多为 ±120
            if abs(event.delta) >= 120:
                return int(-1 * (event.delta / 120))
            return -1 if event.delta > 0 else 1
        return -1 if getattr(event, "num", 0) == 4 else 1

    def _on_mousewheel(self, event) -> str:
        if not self.canvas.winfo_exists():
            return "break"
        if not self._can_vertically_scroll():
            return "break"
        self.canvas.yview_scroll(self._scroll_delta(event), "units")
        self._pin_body_window()
        return "break"

    def _on_shift_mousewheel(self, event) -> str:
        if not self.canvas.winfo_exists():
            return "break"
        if not self._can_horizontally_scroll():
            return "break"
        self._xview_both("scroll", self._scroll_delta(event), "units")
        return "break"

    def _can_vertically_scroll(self) -> bool:
        try:
            first, last = self.canvas.yview()
            return not (first <= 0.0 and last >= 1.0)
        except tk.TclError:
            return False

    def _can_horizontally_scroll(self) -> bool:
        try:
            first, last = self.canvas.xview()
            return not (first <= 0.0 and last >= 1.0)
        except tk.TclError:
            return False

    def _xview_both(self, *args) -> None:
        self.canvas.xview(*args)
        self.header_canvas.xview(*args)

    def _on_body_xscroll(self, first: str, last: str) -> None:
        self.h_scrollbar.set(first, last)
        if self.header_canvas.xview() != (float(first), float(last)):
            self.header_canvas.xview_moveto(float(first))

    def _on_header_xscroll(self, first: str, last: str) -> None:
        self.h_scrollbar.set(first, last)
        if self.canvas.xview() != (float(first), float(last)):
            self.canvas.xview_moveto(float(first))

    def _sync_scroll_gutter(self) -> None:
        """表头右侧占位与纵向滚动条同宽，避免 Windows 下边框错位。"""
        if not self.winfo_exists():
            return
        gutter = self.SCROLL_GUTTER
        try:
            self.scrollbar.update_idletasks()
            measured = int(self.scrollbar.winfo_width())
            if measured <= 1:
                measured = int(self.scrollbar.winfo_reqwidth())
            if measured > 1:
                gutter = measured
        except tk.TclError:
            pass
        if gutter == self._scroll_gutter:
            return
        self._scroll_gutter = gutter
        try:
            self.header_gutter.configure(width=gutter)
            self._hscroll_corner.configure(width=gutter)
            self.table_wrap.grid_columnconfigure(1, minsize=gutter, weight=0)
        except tk.TclError:
            return
        # 占位宽度变化后重新分摊列宽，保证表头/表体列线对齐
        self._sync_column_widths()

    def _frame_content_width(self) -> int:
        """表头与表体共用同一内容宽度，避免 Windows 下各自 reqwidth 不一致导致边框错位。"""
        body_w = 1
        header_w = 1
        try:
            if self.body.winfo_exists():
                self.body.update_idletasks()
                body_w = max(1, int(self.body.winfo_reqwidth()))
            if self.header.winfo_exists():
                self.header.update_idletasks()
                header_w = max(1, int(self.header.winfo_reqwidth()))
        except tk.TclError:
            pass
        return max(int(self._table_content_width), body_w, header_w, 1)

    def _pin_body_window(self) -> None:
        """将表体窗口钉在画布坐标原点，并固定 scrollregion，避免滚动后与表头脱节。"""
        if not self.canvas.winfo_exists():
            return
        self.canvas.coords(self._canvas_window, 0, 0)
        width = self._frame_content_width()
        height = 1
        try:
            height = max(self.body.winfo_reqheight(), 1)
        except tk.TclError:
            pass
        self.canvas.itemconfigure(self._canvas_window, width=width)
        # 强制从 (0,0) 起算，避免 bbox 漂移留下顶部空隙
        self.canvas.configure(scrollregion=(0, 0, width, height))
        # 夹紧滚动位置，防止滚出内容区
        first, _last = self.canvas.yview()
        if first < 0:
            self.canvas.yview_moveto(0)
        elif first > 0 and not self._can_vertically_scroll():
            self.canvas.yview_moveto(0)
        self._pin_header_window()

    def _header_min_height(self) -> int:
        """表头最小高度：收紧内边距时跟正文走，避免表头区被托高后与表体脱节。"""
        if self._cell_pady < self.CELL_PADY:
            return max(18, self._line_height_px() + self._cell_pady * 2)
        return max(28, self._base_rowheight)

    def _pin_header_window(self) -> None:
        if not self.header_canvas.winfo_exists():
            return
        self.header_canvas.coords(self._header_window, 0, 0)
        width = self._frame_content_width()
        content_h = 1
        try:
            content_h = max(self.header.winfo_reqheight(), 1)
        except tk.TclError:
            pass
        height = max(content_h, self._header_min_height())
        # 同步窗口高度，避免画布高于表头内容时底部露出空隙、与表体分离
        self.header_canvas.itemconfigure(
            self._header_window, width=width, height=height
        )
        self.header_canvas.configure(height=height, scrollregion=(0, 0, width, height))

    def _build_header_cells(self) -> None:
        for child in self.header.winfo_children():
            child.destroy()
        self._header_cells.clear()
        col_count = len(self._columns)
        self.header.grid_rowconfigure(0, weight=1)
        for idx, (_col_id, heading, _) in enumerate(self._columns):
            self.header.grid_columnconfigure(
                idx, weight=0, minsize=self._col_minsizes[idx]
            )
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
                wraplength=0,
            )
            label.pack(
                fill="both",
                expand=True,
                padx=self.CELL_PADX,
                pady=self._cell_pady,
            )
            self._header_cells.append(cell)
            self._bind_wheel(cell)
            self._bind_wheel(label)

    def _apply_column_minsizes(self) -> None:
        for idx, minsize in enumerate(self._col_minsizes):
            self.header.grid_columnconfigure(idx, weight=0, minsize=minsize)
            self.body.grid_columnconfigure(idx, weight=0, minsize=minsize)

    def _measure_text_width(self, text: str, *, header: bool = False) -> int:
        font = self._header_font if header else self._cell_font
        if not text:
            return 0
        return max(font.measure(line) for line in str(text).split("\n"))

    def _is_fit_content_col(self, col_idx: int) -> bool:
        if col_idx < 0 or col_idx >= len(self._columns):
            return False
        return self._columns[col_idx][0] in self._fit_content_columns

    def _base_col_widths(self) -> list[int]:
        """列宽下限：声明宽度与表头取大；fit_content 列再按当前页正文单行宽度撑开。"""
        widths = list(self._declared_widths)
        page_rows = self._page_rows()
        for idx, (col_id, heading, _) in enumerate(self._columns):
            needed = (
                self._measure_text_width(heading, header=True) + self.CELL_PADX * 2 + 8
            )
            widths[idx] = max(widths[idx], needed)
            if col_id not in self._fit_content_columns:
                continue
            for row in page_rows:
                text = self._cell_plain_text(row[idx] if idx < len(row) else "")
                # Windows 字体实测常略宽于 measure，多留几像素避免贴边触发折行
                content = self._measure_text_width(text) + self.CELL_PADX * 2 + 8
                widths[idx] = max(widths[idx], content)
        return widths

    def _col_wraplength(self, col_idx: int) -> int:
        if self._is_fit_content_col(col_idx):
            return 0
        minsize = (
            self._col_minsizes[col_idx]
            if col_idx < len(self._col_minsizes)
            else 80
        )
        return max(40, minsize - self.CELL_PADX * 2 - 2)

    def _chars_per_line(self, col_idx: int) -> int:
        if self._is_fit_content_col(col_idx):
            return 10_000
        char_px = max(8, int(self.CELL_FONT[1]))
        return max(4, self._col_wraplength(col_idx) // char_px)

    def _estimate_lines(self, text: str, col_idx: int) -> int:
        if not text:
            return 1
        if self._is_fit_content_col(col_idx):
            # fit_content 不按列宽折行，但仍按显式换行增高（如多缴费周期）
            return min(self.MAX_WRAP_LINES, max(1, str(text).count("\n") + 1))
        per_line = self._chars_per_line(col_idx)
        total = 0
        for part in str(text).split("\n"):
            total += max(1, (len(part) + per_line - 1) // per_line)
        return min(self.MAX_WRAP_LINES, max(1, total))

    @staticmethod
    def _cell_plain_text(value: object) -> str:
        """将单元格值转为纯文本（支持富文本分段）。"""
        if value is None:
            return ""
        if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
            parts: list[str] = []
            for seg in value:
                if isinstance(seg, tuple):
                    parts.append("" if seg[0] is None else str(seg[0]))
                else:
                    parts.append("" if seg is None else str(seg))
            return "".join(parts)
        return str(value)

    @staticmethod
    def _cell_segments(value: object) -> list[tuple[str, str | None]]:
        """解析单元格富文本：[(text, fg|None), ...]。"""
        if value is None:
            return [("", None)]
        if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
            segs: list[tuple[str, str | None]] = []
            for seg in value:
                if isinstance(seg, tuple):
                    text = "" if seg[0] is None else str(seg[0])
                    color = seg[1] if len(seg) > 1 else None
                    segs.append((text, None if color is None else str(color)))
                else:
                    segs.append(("" if seg is None else str(seg), None))
            return segs or [("", None)]
        return [(str(value), None)]

    def _sync_column_widths(self, *_args) -> None:
        """按声明比例分配列宽；fit_content 列按正文收紧，不参与多余宽度分摊。"""
        if self._syncing_widths:
            return
        self._syncing_widths = True
        try:
            available = self._available_viewport_width()
            if available <= 1:
                return
            self._apply_redistributed_widths(available)
            self._apply_wraplengths()
            self._refresh_row_heights()
            self._pin_body_window()
            self._update_hscroll_visibility()
        finally:
            self._syncing_widths = False

    def _available_viewport_width(self) -> int:
        available = self.canvas.winfo_width()
        if available <= 1:
            available = max(
                1, self.table_wrap.winfo_width() - self._scroll_gutter - 2
            )
        return int(available)

    def _apply_redistributed_widths(self, available: int) -> None:
        """立即重算并应用列宽（resize 过程中与表头同步过渡）。"""
        preferred = self._base_col_widths()
        content_width = sum(preferred)
        if content_width <= available and preferred:
            extra = available - content_width
            weights = [
                0 if self._is_fit_content_col(idx) else decl
                for idx, decl in enumerate(self._declared_widths)
            ]
            weight_total = sum(weights)
            if weight_total > 0 and extra > 0:
                grown = [
                    w + int(extra * wt / weight_total)
                    for w, wt in zip(preferred, weights)
                ]
                for idx in range(len(grown) - 1, -1, -1):
                    if weights[idx] > 0:
                        grown[idx] += available - sum(grown)
                        break
                preferred = [max(40, w) for w in grown]
                content_width = available
            else:
                content_width = sum(preferred)

        self._col_minsizes = preferred
        self._table_content_width = max(content_width, 1)
        self._last_viewport_width = available
        self._apply_column_minsizes()

    def _cancel_after(self, attr: str) -> None:
        after_id = getattr(self, attr, None)
        if after_id is None:
            return
        try:
            self.after_cancel(after_id)
        except Exception:
            pass
        setattr(self, attr, None)

    def _sync_columns_for_viewport(self, width: int) -> None:
        """视口变化时立刻分摊列宽，换行/行高稍后补齐，避免表体滞后于表头。"""
        if width <= 1 or self._syncing_widths:
            return
        if not self._viewport_changed(width):
            return
        self._syncing_widths = True
        try:
            self._apply_redistributed_widths(width)
            self._pin_body_window()
            self._update_hscroll_visibility()
        finally:
            self._syncing_widths = False
        # 换行宽度与行高稍后再算，避免拖拽/全屏过程卡顿
        self._schedule_layout_polish()

    def _schedule_layout_polish(self) -> None:
        self._cancel_after("_sync_after_id")
        self._sync_after_id = self.after_idle(self._run_layout_polish)
        # 全屏动画可能仍在变宽，收尾再完整同步一次
        self._cancel_after("_sync_follow_id")
        self._sync_follow_id = self.after(120, self._run_follow_up_column_sync)

    def _run_layout_polish(self) -> None:
        self._sync_after_id = None
        if not self.winfo_exists() or self._syncing_widths:
            return
        self._syncing_widths = True
        try:
            self._apply_wraplengths()
            self._refresh_row_heights()
            self._pin_body_window()
        finally:
            self._syncing_widths = False

    def _run_follow_up_column_sync(self) -> None:
        self._sync_follow_id = None
        if not self.winfo_exists():
            return
        self._sync_column_widths()

    def _viewport_changed(self, width: int) -> bool:
        return abs(int(width) - int(self._last_viewport_width)) >= 2

    def _apply_wraplengths(self) -> None:
        """列宽变化后同步换行宽度，超长文本可折行。表头始终单行，避免单位被折到下一行裁掉。"""
        for cell in self._header_cells:
            for child in cell.winfo_children():
                if isinstance(child, tk.Label):
                    try:
                        child.configure(wraplength=0)
                    except tk.TclError:
                        pass
        for cells in self._row_widgets.values():
            for col_idx, cell in enumerate(cells):
                wrap = self._col_wraplength(col_idx)
                labels = [
                    child
                    for child in cell.winfo_children()
                    if isinstance(child, tk.Label)
                ]
                if not labels:
                    # 富文本：内层 frame 中的标签，仅最后一段参与换行
                    for child in cell.winfo_children():
                        nested = [
                            n
                            for n in child.winfo_children()
                            if isinstance(n, tk.Label)
                        ]
                        if nested:
                            for label in nested[:-1]:
                                try:
                                    label.configure(wraplength=0)
                                except tk.TclError:
                                    pass
                            try:
                                nested[-1].configure(wraplength=wrap)
                            except tk.TclError:
                                pass
                    continue
                for label in labels:
                    try:
                        label.configure(wraplength=wrap)
                    except tk.TclError:
                        pass

    def _page_rows(self) -> list[list[object]]:
        if self._enable_pagination:
            start = (self.page - 1) * self.page_size
            end = start + self.page_size
            return self._all_rows[start:end]
        return self._all_rows

    def _refresh_row_heights(self) -> None:
        if not self._row_widgets:
            return
        height = self._auto_rowheight_for(self._page_rows())
        self._current_rowheight = height
        for cells in self._row_widgets.values():
            for cell in cells:
                try:
                    cell.configure(height=height)
                except tk.TclError:
                    pass

    def _update_hscroll_visibility(self) -> None:
        viewport = max(1, self.canvas.winfo_width())
        if self._table_content_width > viewport + 1:
            self.h_scrollbar.grid()
            self._hscroll_corner.grid()
        else:
            self.h_scrollbar.grid_remove()
            self._hscroll_corner.grid_remove()
            self.canvas.xview_moveto(0)
            self.header_canvas.xview_moveto(0)

    def _on_table_configure(self, event=None) -> None:
        self._sync_scroll_gutter()
        width = 0
        if event is not None and getattr(event, "width", 0):
            width = int(event.width) - self._scroll_gutter - 2
        if width <= 1:
            width = self._available_viewport_width()
        self._sync_columns_for_viewport(width)

    def _on_self_configure(self, event) -> None:
        # 仅响应本控件自身尺寸变化，忽略子控件冒泡
        if event.widget is not self:
            return
        self._sync_columns_for_viewport(int(event.width))

    def _on_canvas_configure(self, event) -> None:
        # 视口变宽时立刻分摊列宽，使表体与表头同步过渡（不再只拉大空白画布）
        if self._viewport_changed(event.width):
            self._sync_columns_for_viewport(event.width)
            return
        width = max(self._table_content_width, event.width, 1)
        self.canvas.itemconfigure(self._canvas_window, width=width)
        self._pin_body_window()
        self._update_hscroll_visibility()

    def _on_header_canvas_configure(self, event) -> None:
        if self._viewport_changed(event.width):
            self._sync_columns_for_viewport(event.width)
            return
        width = max(self._table_content_width, event.width, 1)
        self.header_canvas.itemconfigure(self._header_window, width=width)
        self._pin_header_window()

    def _on_body_configure(self, _event=None) -> None:
        self._pin_body_window()

    def _on_header_configure(self, _event=None) -> None:
        self._pin_header_window()

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
        self._base_rowheight = max(22, int(rowheight))
        self._current_rowheight = self._base_rowheight

    def _line_height_px(self) -> int:
        """正文字号对应行高；用字体 metrics，避免全屏/窗口切换时估算跳动。"""
        try:
            return max(14, int(self._cell_font.metrics("linespace")) + 2)
        except Exception:
            return int(self.CELL_FONT[1]) + 6

    def _auto_rowheight_for(self, rows: Sequence[Sequence[object]]) -> int:
        max_lines = 1
        for row in rows:
            for col_idx, cell in enumerate(row):
                text = self._cell_plain_text(cell)
                max_lines = max(max_lines, self._estimate_lines(text, col_idx))
        line_h = self._line_height_px()
        pad = self._cell_pady * 2
        content_h = line_h * max_lines + pad
        # 收紧内边距时仍略留上下空白，且不以过大的 base rowheight 托高
        if self._cell_pady < self.CELL_PADY:
            return max(content_h, line_h + pad)
        return max(self._base_rowheight, content_h)

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

        def _paint(widget: tk.Misc) -> None:
            try:
                widget.configure(bg=bg)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                _paint(child)

        for child in cell.winfo_children():
            _paint(child)

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
        value: object,
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

        segments = self._cell_segments(value)
        rich = len(segments) > 1 or any(color for _text, color in segments)
        if not rich:
            text = segments[0][0] if segments else ""
            color = segments[0][1] if segments else None
            label = tk.Label(
                cell,
                text=text,
                bg=bg,
                fg=color or fg,
                font=font,
                anchor=self._to_anchor(anchor),
                justify=self._to_justify(anchor),
                wraplength=wraplength,
            )
            label.pack(
                fill="both",
                expand=True,
                padx=self.CELL_PADX,
                pady=self._cell_pady,
            )
            return cell

        inner = tk.Frame(cell, bg=bg, bd=0, highlightthickness=0)
        inner.pack(
            fill="both",
            expand=True,
            padx=self.CELL_PADX,
            pady=self._cell_pady,
        )
        for idx, (text, color) in enumerate(segments):
            is_last = idx == len(segments) - 1
            label = tk.Label(
                inner,
                text=text,
                bg=bg,
                fg=color or fg,
                font=font,
                anchor=self._to_anchor(anchor) if idx == 0 else "w",
                justify="left",
                wraplength=wraplength if is_last else 0,
                bd=0,
                highlightthickness=0,
                padx=0,
                pady=0,
            )
            label.pack(side="left", fill="y", padx=0, pady=0)
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
        # 先按当前可视宽度算列宽，再估行高，保证超长文本换行后不被裁切
        self._sync_column_widths()
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
                    cell = self._make_cell(
                        self.body,
                        value,
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

                    def _bind_tree(widget: tk.Misc, row_iid: str = iid) -> None:
                        widget.bind(
                            "<Button-1>", lambda _e, i=row_iid: self._select_row(i)
                        )
                        self._bind_wheel(widget)
                        for child in widget.winfo_children():
                            _bind_tree(child, row_iid)

                    _bind_tree(cell)
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
    def __init__(
        self,
        master: tk.Misc,
        title: str,
        width: int = 460,
        height: int = 420,
        *,
        scrollable: bool = False,
        label_width: int = 120,
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, bool(scrollable))
        self.transient(master)
        # 先隐藏，等子类填完字段后再在 show() 中显示，避免空窗闪烁与回填时序问题
        self.withdraw()
        try:
            screen_h = int(self.winfo_screenheight())
            height = min(height, max(420, screen_h - 100))
        except tk.TclError:
            pass
        self._dialog_width = width
        self._dialog_height = height
        self._label_width = label_width
        self._on_submit: Optional[Callable[[dict], None]] = None
        self.geometry(f"{width}x{height}")
        self.result: Optional[dict] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        if scrollable:
            self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        else:
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

    def _focus(self) -> None:
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def add_field(self, row: int, label: str, widget: tk.Misc) -> None:
        ctk.CTkLabel(self.body, text=label, anchor="w", width=self._label_width).grid(
            row=row, column=0, sticky="w", pady=8, padx=FORM_LABEL_PADX
        )
        widget.grid(row=row, column=1, sticky="ew", pady=8)

    def _restore_modal(self) -> None:
        try:
            if self.winfo_exists():
                self.grab_set()
                self.lift()
                self.after(50, self._focus)
        except tk.TclError:
            pass

    def _on_ok(self) -> None:
        try:
            data = self.collect()
            if self._on_submit is not None:
                self._on_submit(data)
            self.result = data
        except Exception as exc:  # noqa: BLE001
            from app.ui.utils import show_error

            show_error(str(exc), parent=self)
            self._restore_modal()
            return
        self.destroy()

    def collect(self) -> dict:
        raise NotImplementedError

    def show(
        self, on_submit: Optional[Callable[[dict], None]] = None
    ) -> Optional[dict]:
        self._on_submit = on_submit
        center_window(self, self._dialog_width, self._dialog_height)
        self.deiconify()
        self.grab_set()
        self.lift()
        self.after(50, self._focus)
        self.after(
            80,
            lambda: center_window(self, self._dialog_width, self._dialog_height),
        )
        self.wait_window()
        return self.result
