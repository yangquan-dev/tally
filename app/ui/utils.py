from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional


def center_window(
    window,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """将窗口置于当前屏幕中央。"""
    try:
        window.update_idletasks()
        w = width if width and width > 1 else window.winfo_width()
        h = height if height and height > 1 else window.winfo_height()
        if w <= 1:
            w = window.winfo_reqwidth()
        if h <= 1:
            h = window.winfo_reqheight()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        x = max(0, (screen_w - w) // 2)
        y = max(0, (screen_h - h) // 2)
        window.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


def format_date_range(start: date | str, end: date | str) -> str:
    """格式化起止日期区间；使用不间断空格避免中途折行。"""
    start_text = start.isoformat() if isinstance(start, date) else str(start)
    end_text = end.isoformat() if isinstance(end, date) else str(end)
    return f"{start_text}\u00a0~\u00a0{end_text}"


def parse_date(text: str, field_name: str = "日期") -> date:
    text = text.strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name}格式应为 YYYY-MM-DD") from exc


def parse_optional_date(text: str, field_name: str = "日期") -> Optional[date]:
    text = text.strip()
    if not text:
        return None
    return parse_date(text, field_name)


def parse_float(text: str, field_name: str = "数值") -> float:
    text = text.strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是数字") from exc


def parse_int(text: str, field_name: str = "数值") -> int:
    text = text.strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是整数") from exc


def show_error(message: str) -> None:
    messagebox.showerror("错误", message)


def show_info(message: str) -> None:
    messagebox.showinfo("提示", message)


def ask_yes_no(message: str) -> bool:
    return bool(messagebox.askyesno("确认", message))


def _dialog_parent(widget=None):
    if widget is None:
        return None
    try:
        top = widget.winfo_toplevel()
        top.lift()
        top.focus_force()
        top.update_idletasks()
        top.update()
        return top
    except Exception:
        return None


def ask_directory(title: str = "选择文件夹", parent=None, initialdir: str | None = None) -> str:
    """弹出文件夹选择框；打包后需绑定父窗口，否则 Windows 上可能无响应。"""
    top = _dialog_parent(parent)
    start = initialdir or str(Path.home())
    try:
        selected = filedialog.askdirectory(
            parent=top,
            title=title,
            mustexist=True,
            initialdir=start,
        )
    except Exception as exc:
        show_error(f"无法打开文件夹选择窗口：{exc}")
        return ""
    return selected or ""


def ask_save_filename(
    title: str = "保存文件",
    parent=None,
    defaultextension: str = "",
    initialfile: str = "",
    filetypes: Optional[list[tuple[str, str]]] = None,
) -> str:
    top = _dialog_parent(parent)
    try:
        selected = filedialog.asksaveasfilename(
            parent=top,
            title=title,
            defaultextension=defaultextension,
            initialfile=initialfile,
            filetypes=filetypes or [("所有文件", "*.*")],
        )
    except Exception as exc:
        show_error(f"无法打开保存对话框：{exc}")
        return ""
    return selected or ""


def ask_open_filename(
    title: str = "打开文件",
    parent=None,
    filetypes: Optional[list[tuple[str, str]]] = None,
    initialdir: str | None = None,
) -> str:
    top = _dialog_parent(parent)
    start = initialdir or str(Path.home())
    try:
        selected = filedialog.askopenfilename(
            parent=top,
            title=title,
            initialdir=start,
            filetypes=filetypes or [("所有文件", "*.*")],
        )
    except Exception as exc:
        show_error(f"无法打开文件选择对话框：{exc}")
        return ""
    return selected or ""


def today_str() -> str:
    return date.today().isoformat()


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def period_end_by_months(start: date, months: int, hard_end: date | None = None) -> date:
    """按月数计算周期结束日（起始日对齐，不含下一周期首日）。"""
    end = add_months(start, months) - timedelta(days=1)
    if hard_end is not None and end > hard_end:
        return hard_end
    return end


def iter_lease_billing_months(
    lease_start: date, lease_end: date
) -> list[tuple[date, date]]:
    """按起租日对齐的租赁月周期切片（含首尾，末月裁切到到期日）。"""
    if lease_end < lease_start:
        return []
    months: list[tuple[date, date]] = []
    idx = 0
    while idx <= 600:
        slice_start = add_months(lease_start, idx)
        if slice_start > lease_end:
            break
        slice_end = period_end_by_months(lease_start, idx + 1, hard_end=lease_end)
        months.append((slice_start, slice_end))
        if slice_end >= lease_end:
            break
        idx += 1
    return months


def billing_month_gross_rent(
    monthly_rent: float,
    lease_start: date,
    slice_start: date,
    slice_end: date,
) -> float:
    """租赁月周期应缴基数：完整月为月租，非完整月按天比例折算。"""
    rent = float(monthly_rent)
    if rent <= 0 or slice_end < slice_start:
        return 0.0
    idx = 0
    while idx <= 600:
        start = add_months(lease_start, idx)
        if start == slice_start:
            full_end = add_months(lease_start, idx + 1) - timedelta(days=1)
            full_days = (full_end - slice_start).days + 1
            charge_days = (slice_end - slice_start).days + 1
            if full_days <= 0 or charge_days <= 0:
                return 0.0
            return round(rent * charge_days / full_days, 2)
        if start > slice_start:
            break
        idx += 1
    # 无法对齐时退回按区间天数相对 30 天估算，正常路径不会走到
    charge_days = (slice_end - slice_start).days + 1
    return round(rent * charge_days / max(charge_days, 1), 2)


def billing_month_count(start: date, end: date) -> float:
    """按起始日对齐估算缴费月数；整月精确，非整月按天数折算。"""
    if end < start:
        return 0.0
    total = 0.0
    n = 1
    while n <= 1200:
        segment_start = add_months(start, n - 1)
        segment_end = period_end_by_months(start, n)
        if segment_start > end:
            break
        overlap_start = max(segment_start, start)
        overlap_end = min(segment_end, end)
        if overlap_end >= overlap_start:
            full_days = (segment_end - segment_start).days + 1
            used_days = (overlap_end - overlap_start).days + 1
            if full_days > 0:
                total += used_days / full_days
        if segment_end >= end:
            break
        n += 1
    return total


def format_money(value: float) -> str:
    # 千分位使用窄不间断空格，避免表格 wraplength 从逗号/小数点处折断金额
    return f"{value:,.2f}".replace(",", "\u202f")


def format_dt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)
