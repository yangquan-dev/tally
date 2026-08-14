from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.bootstrap import BootstrapConfig
from app.database import Database
from app.services import AppServices, ValidationError


class ReminderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bootstrap = BootstrapConfig(root / "bootstrap.json")
        self.bootstrap.set_app_name("测试应用")
        data_dir = root / "data"
        self.bootstrap.set_data_storage_path(data_dir)
        self.db = Database(self.bootstrap.get_db_path())  # type: ignore[arg-type]
        self.services = AppServices(self.bootstrap, self.db)
        self.services.settings.update(
            app_name="测试应用",
            data_storage_path=None,
            lease_expire_remind_days=7,
            rent_due_remind_days=7,
        )
        self.project_id = self.services.projects.create("测试项目")  # type: ignore[union-attr]
        self.room_id = self.services.rooms.create(self.project_id, "A101", 50)  # type: ignore[union-attr]
        self.lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=2000,
            monthly_rent=3000,
            start_date=date(2026, 1, 15),
            end_date=date(2026, 8, 14),
            free_periods=[
                (date(2026, 1, 15), date(2026, 2, 14)),
                (date(2026, 5, 15), date(2026, 6, 14)),
            ],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_generate_periods_and_multiple_free_months(self) -> None:
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        self.assertEqual(lease.payment_period, "季度")
        self.assertEqual(len(lease.free_periods or []), 2)
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        # 默认按季度：2026-01-15~04-14 / 04-15~07-14 / 07-15~08-14
        self.assertEqual(
            [(p.period_start, p.period_end) for p in periods],
            [
                (date(2026, 1, 15), date(2026, 4, 14)),
                (date(2026, 4, 15), date(2026, 7, 14)),
                (date(2026, 7, 15), date(2026, 8, 14)),
            ],
        )
        first = periods[0]
        self.assertFalse(first.fully_free)
        self.assertTrue(first.partial_free)
        # 首季含整月免租 1.15~2.14，另两月应收 → 6000
        self.assertEqual(first.amount, 6000)
        second = periods[1]
        self.assertTrue(second.partial_free)
        # 次季含整月免租 5.15~6.14 → 6000
        self.assertEqual(second.amount, 6000)
        self.assertEqual(periods[2].amount, 3000)

    def test_partial_month_prorates_gross_by_days(self) -> None:
        """租期未覆盖完整租赁月时，应缴按天比例折算。"""
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=3100,
            start_date=date(2042, 1, 10),
            end_date=date(2042, 3, 5),
            free_periods=[],
            payment_period="季度",
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        self.assertEqual(len(periods), 1)
        # 1/10~2/9、2/10~3/9(完整31天)、3/10~3/5 不存在
        # 完整月：1/10~2/9(31天)、2/10~3/9(28天)；末月裁切为 2/10~3/5
        # 实际：1/10~2/9 全月 3100；2/10~3/5 共 24 天，完整月 2/10~3/9=28 天
        # → 3100 + 3100*24/28
        due = self.services.reminders.period_gross_amount(lease, periods[0])  # type: ignore[union-attr]
        expected = round(3100 + 3100 * 24 / 28, 2)
        self.assertEqual(due, expected)
        self.assertEqual(periods[0].amount, expected)

    def test_reject_overlapping_free_periods(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=2000,
                start_date=date(2027, 1, 1),
                end_date=date(2027, 12, 31),
                free_periods=[
                    (date(2027, 1, 1), date(2027, 2, 28)),
                    (date(2027, 2, 15), date(2027, 3, 15)),
                ],
            )

    def test_payment_clears_reminder(self) -> None:
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        unpaid = self.services.reminders.unpaid_periods(lease, today=date(2026, 3, 1))  # type: ignore[union-attr]
        target = next(p for p in unpaid if p.period_start == date(2026, 1, 15))
        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=target.period_start,
            period_end=target.period_end,
            amount=target.amount,
            paid_at=date(2026, 2, 16),
        )
        unpaid_after = self.services.reminders.unpaid_periods(  # type: ignore[union-attr]
            lease, today=date(2026, 3, 1)
        )
        self.assertFalse(
            any(p.period_start == date(2026, 1, 15) for p in unpaid_after)
        )

    def test_partial_payment_reduces_reminder_amount(self) -> None:
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        unpaid = self.services.reminders.unpaid_periods(lease, today=date(2026, 3, 1))  # type: ignore[union-attr]
        target = next(p for p in unpaid if p.period_start == date(2026, 1, 15))
        full_amount = target.amount
        self.assertEqual(full_amount, 6000)
        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=target.period_start,
            period_end=target.period_end,
            amount=2000,
            paid_at=date(2026, 2, 16),
        )
        unpaid_after = self.services.reminders.unpaid_periods(  # type: ignore[union-attr]
            lease, today=date(2026, 3, 1)
        )
        remaining = next(
            p for p in unpaid_after if p.period_start == date(2026, 1, 15)
        )
        self.assertEqual(remaining.amount, 4000)
        reminders = self.services.reminders.list_reminders(today=date(2026, 3, 1))  # type: ignore[union-attr]
        hit = next(
            r
            for r in reminders
            if r.lease_id == self.lease_id
            and r.period_start == date(2026, 1, 15)
            and r.kind in {"租金逾期", "租金应收"}
        )
        self.assertEqual(hit.amount, 4000)
        self.assertEqual(hit.due_amount, 9000)
        self.assertEqual(hit.free_amount, 3000)
        self.assertEqual(hit.paid_amount, 2000)
        self.assertEqual(hit.discount_amount, 0)
        self.assertIn("已部分缴费", hit.detail)

    def test_multiple_payments_within_period_cap(self) -> None:
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        unpaid = self.services.reminders.unpaid_periods(lease, today=date(2026, 3, 1))  # type: ignore[union-attr]
        target = next(p for p in unpaid if p.period_start == date(2026, 1, 15))
        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=target.period_start,
            period_end=target.period_end,
            amount=2500,
            paid_at=date(2026, 2, 1),
        )
        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=target.period_start,
            period_end=target.period_end,
            amount=3500,
            paid_at=date(2026, 2, 15),
        )
        with self.assertRaises(ValidationError):
            self.services.payments.create(  # type: ignore[union-attr]
                lease_id=self.lease_id,
                period_start=target.period_start,
                period_end=target.period_end,
                amount=0.01,
                paid_at=date(2026, 2, 20),
            )

    def test_one_payment_covers_consecutive_periods(self) -> None:
        """一笔缴费可覆盖连续多个应收期，按时间顺序填满各期应缴。"""
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        unpaid = self.services.reminders.unpaid_periods(lease, today=date(2026, 5, 1))  # type: ignore[union-attr]
        first = next(p for p in unpaid if p.period_start == date(2026, 1, 15))
        second = next(p for p in unpaid if p.period_start == date(2026, 4, 15))
        total = round(first.amount + second.amount, 2)
        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=first.period_start,
            period_end=second.period_end,
            amount=total,
            paid_at=date(2026, 4, 20),
        )
        unpaid_after = self.services.reminders.unpaid_periods(  # type: ignore[union-attr]
            lease, today=date(2026, 5, 1)
        )
        self.assertFalse(
            any(p.period_start == date(2026, 1, 15) for p in unpaid_after)
        )
        self.assertFalse(
            any(p.period_start == date(2026, 4, 15) for p in unpaid_after)
        )
        self.assertEqual(
            self.services.reminders.paid_amount_for_period(self.lease_id, first),  # type: ignore[union-attr]
            first.amount,
        )
        self.assertEqual(
            self.services.reminders.paid_amount_for_period(self.lease_id, second),  # type: ignore[union-attr]
            second.amount,
        )

    def test_multi_period_payment_fifo_partial(self) -> None:
        """跨期缴费先填满首期，剩余再进入下一期。"""
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        unpaid = self.services.reminders.unpaid_periods(lease, today=date(2026, 5, 1))  # type: ignore[union-attr]
        first = next(p for p in unpaid if p.period_start == date(2026, 1, 15))
        second = next(p for p in unpaid if p.period_start == date(2026, 4, 15))
        # 首期剩余 6000，多缴 1000 进入第二期
        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=first.period_start,
            period_end=second.period_end,
            amount=first.amount + 1000,
            paid_at=date(2026, 4, 20),
        )
        self.assertEqual(
            self.services.reminders.paid_amount_for_period(self.lease_id, first),  # type: ignore[union-attr]
            first.amount,
        )
        self.assertEqual(
            self.services.reminders.paid_amount_for_period(self.lease_id, second),  # type: ignore[union-attr]
            1000,
        )
        unpaid_after = self.services.reminders.unpaid_periods(  # type: ignore[union-attr]
            lease, today=date(2026, 5, 1)
        )
        remaining_second = next(
            p for p in unpaid_after if p.period_start == date(2026, 4, 15)
        )
        self.assertEqual(remaining_second.amount, round(second.amount - 1000, 2))

    def test_multi_period_payment_cap(self) -> None:
        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        unpaid = self.services.reminders.unpaid_periods(lease, today=date(2026, 5, 1))  # type: ignore[union-attr]
        first = next(p for p in unpaid if p.period_start == date(2026, 1, 15))
        second = next(p for p in unpaid if p.period_start == date(2026, 4, 15))
        total = round(first.amount + second.amount, 2)
        with self.assertRaises(ValidationError):
            self.services.payments.create(  # type: ignore[union-attr]
                lease_id=self.lease_id,
                period_start=first.period_start,
                period_end=second.period_end,
                amount=total + 0.01,
                paid_at=date(2026, 4, 20),
            )

    def test_due_reminder_appears(self) -> None:
        # 首个季度起日 2026-01-15，提前 7 天提醒窗口内
        reminders = self.services.reminders.list_reminders(today=date(2026, 1, 10))  # type: ignore[union-attr]
        kinds = {r.kind for r in reminders}
        self.assertIn("租金应收", kinds)
        rent_items = [r for r in reminders if r.kind == "租金应收"]
        self.assertTrue(
            any(
                r.period_start == date(2026, 1, 15)
                and r.period_end == date(2026, 4, 14)
                for r in rent_items
            )
        )
        self.assertTrue(all(r.tenant == "测试租户" for r in reminders))

    def test_half_year_payment_period(self) -> None:
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=2000,
            start_date=date(2028, 1, 1),
            end_date=date(2028, 12, 31),
            free_periods=[],
            payment_period="半年",
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        self.assertEqual(
            [(p.period_start, p.period_end, p.amount) for p in periods],
            [
                (date(2028, 1, 1), date(2028, 6, 30), 12000.0),
                (date(2028, 7, 1), date(2028, 12, 31), 12000.0),
            ],
        )

    def test_discount_rate_and_amount_per_month(self) -> None:
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=3000,
            start_date=date(2029, 1, 1),
            end_date=date(2029, 3, 31),
            free_periods=[],
            payment_period="季度",
            discounts=[
                (date(2029, 1, 1), date(2029, 1, 31), "rate", 0.8),
                (date(2029, 2, 1), date(2029, 2, 28), "amount", 500),
            ],
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        self.assertEqual(len(lease.discounts or []), 2)
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        self.assertEqual(len(periods), 1)
        # 1 月 3000*0.8=2400；2 月 3000-500=2500；3 月全额 3000 → 7900
        self.assertEqual(periods[0].amount, 7900.0)

    def test_discounts_label_merges_contiguous_months(self) -> None:
        from app.models import Lease, LeaseDiscount

        lease = Lease(
            id=1,
            room_id=1,
            deposit=1,
            monthly_rent=3000,
            start_date=date(2035, 1, 1),
            end_date=date(2035, 6, 30),
            status="生效",
            payment_period="季度",
            discounts=[
                LeaseDiscount(date(2035, 1, 1), date(2035, 1, 31), "rate", 0.8),
                LeaseDiscount(date(2035, 2, 1), date(2035, 2, 28), "rate", 0.8),
                LeaseDiscount(date(2035, 3, 1), date(2035, 3, 31), "rate", 0.8),
                LeaseDiscount(date(2035, 5, 1), date(2035, 5, 31), "amount", 200),
            ],
        )
        label = lease.discounts_label()
        self.assertIn("2035-01-01\u00a0~\u00a02035-03-31 折扣 0.8", label)
        self.assertIn("2035-05-01\u00a0~\u00a02035-05-31 立减 200", label)

    def test_discount_uses_lease_billing_month(self) -> None:
        """中旬起租：选首月折/减对应起租日对齐的月周期。"""
        from app.ui.utils import iter_lease_billing_months

        lease_start = date(2036, 8, 10)
        lease_end = date(2037, 8, 9)
        months = iter_lease_billing_months(lease_start, lease_end)
        self.assertEqual(months[0], (date(2036, 8, 10), date(2036, 9, 9)))

        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=3000,
            start_date=lease_start,
            end_date=lease_end,
            free_periods=[],
            payment_period="季度",
            discounts=[(date(2036, 8, 10), date(2036, 9, 9), "rate", 0.8)],
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        self.assertEqual(len(lease.discounts or []), 1)
        d0 = (lease.discounts or [])[0]
        self.assertEqual(d0.start_date, date(2036, 8, 10))
        self.assertEqual(d0.end_date, date(2036, 9, 9))
        self.assertIn("2036-08-10\u00a0~\u00a02036-09-09", lease.discounts_label())

        # 自然月起止不符合租赁月周期，应拒绝
        with self.assertRaises(ValidationError):
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=3000,
                start_date=date(2038, 8, 10),
                end_date=date(2039, 8, 9),
                free_periods=[],
                payment_period="季度",
                discounts=[(date(2038, 8, 1), date(2038, 8, 31), "rate", 0.8)],
            )

    def test_free_period_takes_priority_over_discount(self) -> None:
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=3000,
            start_date=date(2030, 1, 1),
            end_date=date(2030, 2, 28),
            free_periods=[(date(2030, 1, 1), date(2030, 1, 31))],
            payment_period="季度",
            discounts=[(date(2030, 1, 1), date(2030, 1, 31), "rate", 0.5)],
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        self.assertEqual(len(periods), 1)
        # 1 月免租优先为 0；2 月全额 3000
        self.assertEqual(periods[0].amount, 3000.0)
        free_amt = self.services.reminders.period_free_amount(lease, periods[0])  # type: ignore[union-attr]
        self.assertEqual(free_amt, 3000.0)

    def test_partial_free_days_prorate_amount(self) -> None:
        """免租费用按计费月与免租期重叠天数比例计算。"""
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=3000,
            start_date=date(2034, 1, 1),
            end_date=date(2034, 1, 31),
            free_periods=[(date(2034, 1, 1), date(2034, 1, 10))],
            payment_period="季度",
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        self.assertEqual(len(periods), 1)
        # 1 月共 31 天，免租 10 天 → 免租 3000*10/31，应收 3000*21/31
        free_amt = self.services.reminders.period_free_amount(lease, periods[0])  # type: ignore[union-attr]
        due = self.services.reminders.period_gross_amount(lease, periods[0])  # type: ignore[union-attr]
        self.assertEqual(due, 3000.0)
        self.assertAlmostEqual(free_amt, round(3000 * 10 / 31, 2), places=2)
        self.assertAlmostEqual(periods[0].amount, round(3000 * 21 / 31, 2), places=2)

    def test_discount_amount_based_on_monthly_rent(self) -> None:
        """立减按月租计算；部分免租时免租作用在折后金额上。"""
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=2000,
            start_date=date(2040, 8, 12),
            end_date=date(2040, 10, 11),
            free_periods=[(date(2040, 8, 12), date(2040, 8, 18))],
            payment_period="季度",
            discounts=[
                (date(2040, 8, 12), date(2040, 9, 11), "amount", 2000),
                (date(2040, 9, 12), date(2040, 10, 11), "amount", 123),
            ],
        )
        lease = self.services.leases.get(lease_id)  # type: ignore[union-attr]
        assert lease is not None
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        self.assertEqual(len(periods), 1)
        due = self.services.reminders.period_gross_amount(lease, periods[0])  # type: ignore[union-attr]
        free = self.services.reminders.period_free_amount(lease, periods[0])  # type: ignore[union-attr]
        disc = self.services.reminders.period_discount_amount(lease, periods[0])  # type: ignore[union-attr]
        # 两月：首月立减 2000、折后为 0 → 免租 0；次月立减 123 → 净 1877
        self.assertEqual(due, 4000.0)
        self.assertEqual(free, 0.0)
        self.assertEqual(disc, 2123.0)
        self.assertEqual(periods[0].amount, 1877.0)
        self.assertEqual(round(due - free - disc, 2), periods[0].amount)

    def test_reject_discount_amount_over_monthly_rent(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=2000,
                start_date=date(2041, 1, 1),
                end_date=date(2041, 12, 31),
                free_periods=[],
                discounts=[(date(2041, 1, 1), date(2041, 1, 31), "amount", 2000.01)],
            )
        self.assertIn("不能超过该月周期应缴", str(ctx.exception))

    def test_reject_discount_amount_over_partial_month_rent(self) -> None:
        """非完整租赁月：立减上限为按天折算后的应缴。"""
        lease_start = date(2043, 8, 12)
        lease_end = date(2043, 8, 20)  # 首月完整应为 8/12~9/11，共 31 天；实际 9 天
        cap = round(2000 * 9 / 31, 2)
        with self.assertRaises(ValidationError) as ctx:
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=2000,
                start_date=lease_start,
                end_date=lease_end,
                free_periods=[],
                discounts=[(lease_start, lease_end, "amount", round(cap + 0.01, 2))],
            )
        self.assertIn("不能超过该月周期应缴", str(ctx.exception))
        self.assertIn(f"{cap:g}", str(ctx.exception))

        # 等于折算应缴可通过
        lease_id = self.services.leases.create(  # type: ignore[union-attr]
            tenant="测试租户",
            room_id=self.room_id,
            deposit=1000,
            monthly_rent=2000,
            start_date=lease_start,
            end_date=lease_end,
            free_periods=[],
            discounts=[(lease_start, lease_end, "amount", cap)],
        )
        self.assertGreater(lease_id, 0)

    def test_reject_invalid_discount_rate(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=2000,
                start_date=date(2031, 1, 1),
                end_date=date(2031, 12, 31),
                free_periods=[],
                discounts=[(date(2031, 1, 1), date(2031, 1, 31), "rate", 1.0)],
            )

    def test_reject_overlapping_discounts(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=2000,
                start_date=date(2032, 1, 1),
                end_date=date(2032, 12, 31),
                free_periods=[],
                discounts=[
                    (date(2032, 2, 1), date(2032, 2, 29), "rate", 0.9),
                    (date(2032, 2, 1), date(2032, 2, 29), "amount", 200),
                ],
            )

    def test_reject_cross_month_discount(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.leases.create(  # type: ignore[union-attr]
                tenant="测试租户",
                room_id=self.room_id,
                deposit=1000,
                monthly_rent=2000,
                start_date=date(2033, 1, 1),
                end_date=date(2033, 12, 31),
                free_periods=[],
                discounts=[
                    (date(2033, 1, 1), date(2033, 2, 28), "rate", 0.9),
                ],
            )

    def test_deposit_payment_does_not_affect_rent(self) -> None:
        from app.models import FEE_TYPE_DEPOSIT

        lease = self.services.leases.get(self.lease_id)  # type: ignore[union-attr]
        assert lease is not None
        # setUp 租赁押金 2000
        pay_id = self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=date(2026, 1, 15),
            period_end=date(2026, 1, 15),
            amount=800,
            paid_at=date(2026, 1, 20),
            note="首笔押金",
            fee_type=FEE_TYPE_DEPOSIT,
        )
        self.assertGreater(pay_id, 0)
        self.assertEqual(
            self.services.payments.deposit_paid(self.lease_id),  # type: ignore[union-attr]
            800,
        )
        self.assertEqual(
            self.services.payments.deposit_remaining(self.lease_id),  # type: ignore[union-attr]
            1200,
        )
        # 押金不计入租金已缴
        periods = self.services.reminders.generate_rent_periods(lease)  # type: ignore[union-attr]
        chargeable = [p for p in periods if float(p.amount) > 0]
        self.assertTrue(chargeable)
        paid_map = self.services.reminders.paid_map_for_lease(lease)  # type: ignore[union-attr]
        for period in chargeable:
            key = (period.period_start, period.period_end)
            self.assertEqual(paid_map.get(key, 0.0), 0.0)

        with self.assertRaises(ValidationError):
            self.services.payments.create(  # type: ignore[union-attr]
                lease_id=self.lease_id,
                period_start=date(2026, 1, 21),
                period_end=date(2026, 1, 21),
                amount=1200.01,
                paid_at=date(2026, 1, 21),
                fee_type=FEE_TYPE_DEPOSIT,
            )

    def test_deposit_unpaid_appears_in_reminders(self) -> None:
        from app.models import FEE_TYPE_DEPOSIT

        reminders = self.services.reminders.list_reminders(  # type: ignore[union-attr]
            today=date(2026, 2, 1)
        )
        deposit_items = [
            r for r in reminders if r.kind == "押金应收" and r.lease_id == self.lease_id
        ]
        self.assertEqual(len(deposit_items), 1)
        self.assertEqual(deposit_items[0].amount, 2000)
        self.assertEqual(deposit_items[0].due_amount, 2000)
        self.assertEqual(deposit_items[0].paid_amount, 0)

        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=date(2026, 1, 20),
            period_end=date(2026, 1, 20),
            amount=500,
            paid_at=date(2026, 1, 20),
            fee_type=FEE_TYPE_DEPOSIT,
        )
        reminders = self.services.reminders.list_reminders(  # type: ignore[union-attr]
            today=date(2026, 2, 1)
        )
        deposit_items = [
            r for r in reminders if r.kind == "押金应收" and r.lease_id == self.lease_id
        ]
        self.assertEqual(len(deposit_items), 1)
        self.assertEqual(deposit_items[0].amount, 1500)
        self.assertEqual(deposit_items[0].paid_amount, 500)

        self.services.payments.create(  # type: ignore[union-attr]
            lease_id=self.lease_id,
            period_start=date(2026, 1, 21),
            period_end=date(2026, 1, 21),
            amount=1500,
            paid_at=date(2026, 1, 21),
            fee_type=FEE_TYPE_DEPOSIT,
        )
        reminders = self.services.reminders.list_reminders(  # type: ignore[union-attr]
            today=date(2026, 2, 1)
        )
        deposit_items = [
            r for r in reminders if r.kind == "押金应收" and r.lease_id == self.lease_id
        ]
        self.assertEqual(deposit_items, [])


class BootstrapConfigTests(unittest.TestCase):
    def test_storage_locked_after_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = BootstrapConfig(root / "bootstrap.json")
            self.assertFalse(cfg.is_storage_configured())
            cfg.set_data_storage_path(root / "data")
            self.assertTrue(cfg.is_storage_configured())
            with self.assertRaises(ValueError):
                cfg.set_data_storage_path(root / "other")

    def test_frozen_data_dir_platform_default(self) -> None:
        import sys

        from app.config import (
            get_frozen_data_dir,
            get_install_dir,
            get_platform_app_support_dir,
        )

        if sys.platform == "darwin":
            self.assertEqual(
                get_frozen_data_dir(),
                get_platform_app_support_dir() / "Tally" / "data",
            )
        else:
            self.assertEqual(get_frozen_data_dir(), get_install_dir() / "data")


if __name__ == "__main__":
    unittest.main()
