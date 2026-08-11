from __future__ import annotations

import unittest
from datetime import date

from app.dingtalk import (
    build_signed_webhook,
    format_reminders_markdown,
    parse_push_time,
    should_clear_last_push_on_save,
    should_push_today,
)
from app.models import ReminderItem


class DingTalkHelpersTests(unittest.TestCase):
    def test_parse_push_time(self) -> None:
        self.assertEqual(parse_push_time("09:00"), (9, 0))
        self.assertEqual(parse_push_time("23:59"), (23, 59))
        with self.assertRaises(ValueError):
            parse_push_time("9")
        with self.assertRaises(ValueError):
            parse_push_time("25:00")

    def test_should_push_once_per_day(self) -> None:
        today = date(2026, 8, 6)
        self.assertTrue(
            should_push_today(
                enabled=True,
                push_time="09:00",
                last_push_date="",
                now_date=today,
                now_hour=9,
                now_minute=0,
            )
        )
        self.assertFalse(
            should_push_today(
                enabled=True,
                push_time="09:00",
                last_push_date="",
                now_date=today,
                now_hour=8,
                now_minute=59,
            )
        )
        self.assertFalse(
            should_push_today(
                enabled=True,
                push_time="09:00",
                last_push_date="2026-08-06 09:00:01",
                now_date=today,
                now_hour=10,
                now_minute=0,
            )
        )
        self.assertTrue(
            should_push_today(
                enabled=True,
                push_time="09:00",
                last_push_date="2026-08-05 23:59:59",
                now_date=today,
                now_hour=9,
                now_minute=0,
            )
        )
        self.assertFalse(
            should_push_today(
                enabled=False,
                push_time="09:00",
                last_push_date="",
                now_date=today,
                now_hour=10,
                now_minute=0,
            )
        )

    def test_clear_last_push_when_time_in_future(self) -> None:
        self.assertTrue(
            should_clear_last_push_on_save(
                enabled=True, push_time="18:30", now_hour=10, now_minute=0
            )
        )
        self.assertFalse(
            should_clear_last_push_on_save(
                enabled=True, push_time="09:00", now_hour=10, now_minute=0
            )
        )
        self.assertFalse(
            should_clear_last_push_on_save(
                enabled=False, push_time="18:30", now_hour=10, now_minute=0
            )
        )

    def test_build_signed_webhook_without_secret(self) -> None:
        url = "https://oapi.dingtalk.com/robot/send?access_token=abc"
        self.assertEqual(build_signed_webhook(url, ""), url)

    def test_build_signed_webhook_with_secret(self) -> None:
        url = "https://oapi.dingtalk.com/robot/send?access_token=abc"
        signed = build_signed_webhook(url, "SECdemo")
        self.assertIn("timestamp=", signed)
        self.assertIn("sign=", signed)
        self.assertTrue(signed.startswith(url + "&"))

    def test_format_reminders_markdown(self) -> None:
        items = [
            ReminderItem(
                kind="已逾期",
                project_id=1,
                project_name="一号院",
                room_id=1,
                room_no="101",
                lease_id=1,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 3, 31),
                amount=3000,
                days_delta=-2,
                detail="",
                due_amount=6000,
                paid_amount=2000,
                discount_amount=500,
                free_amount=500,
            ),
            ReminderItem(
                kind="应收提醒",
                project_id=1,
                project_name="一号院",
                room_id=2,
                room_no="102",
                lease_id=2,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 6, 30),
                amount=2800,
                days_delta=3,
                detail="",
                due_amount=2800,
                paid_amount=0,
                discount_amount=0,
                free_amount=0,
            ),
        ]
        title, text = format_reminders_markdown(
            items, app_name="本地记账", today=date(2026, 8, 6)
        )
        self.assertIn("提醒看板", title)
        self.assertIn("汇总", text)
        self.assertIn("已逾期", text)
        self.assertIn("一号院 · 101", text)
        self.assertIn("逾期 2 天", text)
        self.assertIn(
            "剩余应缴(3000)=应缴(6000)-已缴(2000)-免租(500)-折/减(500)",
            text,
        )
        self.assertIn("应收提醒", text)

    def test_format_empty(self) -> None:
        title, text = format_reminders_markdown([], today=date(2026, 8, 6))
        self.assertIn("暂无提醒", text)
        self.assertIn("汇总", text)


if __name__ == "__main__":
    unittest.main()
