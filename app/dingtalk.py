"""钉钉自定义机器人推送（Webhook）。

仅使用标准库，无需额外依赖。
支持加签（SEC）机器人；联网失败或未配置时静默跳过由调用方决定。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from app.models import ReminderItem

# 钉钉 markdown 正文不宜过长
_MAX_ITEMS = 40
_LAST_PUSH_FMT = "%Y-%m-%d %H:%M:%S"


def is_online(timeout: float = 2.5) -> bool:
    """粗略检测是否联网。"""
    hosts = (("oapi.dingtalk.com", 443), ("223.5.5.5", 53), ("1.1.1.1", 53))
    for host, port in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    # 兜底：尝试 HTTPS 握手级访问钉钉 API 域名
    try:
        req = urllib.request.Request(
            "https://oapi.dingtalk.com/",
            method="GET",
            headers={"User-Agent": "TallyDingTalk/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def format_last_push_at(when: Optional[datetime] = None) -> str:
    """格式化为 yyyy-MM-dd HH:mm:ss。"""
    return (when or datetime.now()).strftime(_LAST_PUSH_FMT)


def last_push_calendar_day(last_push: str) -> str:
    """从上次推送时间戳取出日历日 yyyy-MM-dd（兼容旧的仅日期格式）。"""
    raw = (last_push or "").strip()
    return raw[:10] if raw else ""


def build_signed_webhook(webhook: str, secret: str = "") -> str:
    """若配置了加签密钥，为 Webhook URL 附加 timestamp 与 sign。"""
    url = (webhook or "").strip()
    if not url:
        raise ValueError("请填写钉钉 Webhook 地址")
    secret = (secret or "").strip()
    if not secret:
        return url
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={timestamp}&sign={sign}"


def _days_text(days_delta: int) -> str:
    if days_delta < 0:
        return f"逾期 {abs(days_delta)} 天"
    if days_delta == 0:
        return "今天到期"
    return f"剩余 {days_delta} 天"


def _money_text(amount: float) -> str:
    return f"¥{amount:,.2f}"


def _kind_emoji(kind: str) -> str:
    return {
        "已逾期": "🔴",
        "应收提醒": "🟠",
        "合同已到期": "🟣",
        "合同即将到期": "🔵",
    }.get(kind, "▪️")


def format_reminders_markdown(
    items: Sequence[ReminderItem],
    *,
    app_name: str = "本地记账",
    today: Optional[date] = None,
) -> tuple[str, str]:
    """组装钉钉 markdown 消息，返回 (title, text)。"""
    today = today or date.today()
    overdue = sum(1 for i in items if i.kind == "已逾期")
    due = sum(1 for i in items if i.kind == "应收提醒")
    expire = sum(1 for i in items if i.kind in {"合同即将到期", "合同已到期"})

    title = f"{app_name}·提醒看板"
    heading = f"{app_name} · 提醒看板"

    lines: list[str] = [
        f"### {heading}",
        f"**日期** {today.isoformat()}",
        "",
        f"**汇总** 共 {len(items)} 条",
        f"- 🔴 已逾期：**{overdue}**",
        f"- 🟠 应收提醒：**{due}**",
        f"- 🔵 合同到期相关：**{expire}**",
        "",
    ]

    if not items:
        lines.append("---")
        lines.append("")
        lines.append("今日暂无提醒事项，一切正常。")
        return title, "\n".join(lines) + "\n"

    order = ("已逾期", "应收提醒", "合同已到期", "合同即将到期")
    grouped: dict[str, list[ReminderItem]] = {k: [] for k in order}
    for item in items:
        grouped.setdefault(item.kind, []).append(item)

    shown = 0
    truncated = False
    for kind in order:
        bucket = list(grouped.get(kind) or [])
        if not bucket:
            continue
        # 同类内按紧急程度排序：逾期多的 / 剩余少的在前
        bucket.sort(key=lambda x: (x.days_delta, x.project_name, x.room_no))
        lines.append("---")
        lines.append("")
        lines.append(f"#### {_kind_emoji(kind)} {kind}（{len(bucket)}）")
        lines.append("")
        for item in bucket:
            if shown >= _MAX_ITEMS:
                truncated = True
                break
            period = ""
            if item.period_start and item.period_end:
                period = (
                    f"{item.period_start.isoformat()} ~ {item.period_end.isoformat()}"
                )
            lines.append(f"**{item.project_name} · {item.room_no}**")
            lines.append(f"- {_days_text(item.days_delta)}")
            if kind in {"合同即将到期", "合同已到期"}:
                lines.append(f"- 月租 {_money_text(item.amount)}")
            else:
                lines.append(
                    f"- 剩余应缴({item.amount:g})=应缴({item.due_amount:g})-"
                    f"已缴({item.paid_amount:g})-免租({item.free_amount:g})-"
                    f"折/减({item.discount_amount:g})"
                )
            if period:
                lines.append(f"- 周期 {period}")
            lines.append("")
            shown += 1
        if truncated:
            break

    if truncated:
        lines.append(
            f"> 仅展示前 {_MAX_ITEMS} 条，完整列表请打开应用「提醒看板」。"
        )

    return title, "\n".join(lines).rstrip() + "\n"

def send_markdown(
    webhook: str,
    title: str,
    text: str,
    *,
    secret: str = "",
    timeout: float = 12.0,
) -> None:
    """向钉钉机器人发送 markdown 消息；失败抛出 ValueError。"""
    url = build_signed_webhook(webhook, secret)
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise ValueError(f"钉钉推送失败（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"钉钉推送失败：网络错误（{exc.reason}）") from exc
    except TimeoutError as exc:
        raise ValueError("钉钉推送失败：请求超时") from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"钉钉返回无法解析：{body[:200]}") from exc
    errcode = result.get("errcode", 0)
    if errcode not in (0, "0", None):
        raise ValueError(
            f"钉钉推送失败：{result.get('errmsg', body)}（errcode={errcode}）"
        )


def parse_push_time(value: str) -> tuple[int, int]:
    """解析 HH:MM，返回 (hour, minute)。"""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("请填写推送时间")
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("推送时间格式应为 HH:MM，例如 09:00")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("推送时间格式应为 HH:MM，例如 09:00") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("推送时间超出有效范围（00:00–23:59）")
    return hour, minute


def should_push_today(
    *,
    enabled: bool,
    push_time: str,
    last_push_date: str,
    now_date: date,
    now_hour: int,
    now_minute: int,
) -> bool:
    """是否应在今天执行推送（每天最多一次，到达设定时刻后触发）。

    到点时若推送失败/未联网，不写入成功时间；恢复后只要时刻已过且今日未成功，仍会触发一次。
    last_push_date 支持 `yyyy-MM-dd` 或 `yyyy-MM-dd HH:mm:ss`。
    """
    if not enabled:
        return False
    if last_push_calendar_day(last_push_date) == now_date.isoformat():
        return False
    hour, minute = parse_push_time(push_time)
    return (now_hour, now_minute) >= (hour, minute)


def should_clear_last_push_on_save(
    *,
    enabled: bool,
    push_time: str,
    now_hour: int,
    now_minute: int,
) -> bool:
    """保存配置时：若推送时刻仍在今日未来，清除今日已推送标记以便到点再推。"""
    if not enabled:
        return False
    hour, minute = parse_push_time(push_time)
    return (now_hour, now_minute) < (hour, minute)


def iter_unique_webhooks(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        u = (url or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        result.append(u)
    return result
