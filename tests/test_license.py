from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from nacl.signing import SigningKey

import app.license as license_mod
from app.license import (
    LicenseStatus,
    build_signed_license,
    check_license_file,
    check_license_text,
    evaluate_payload,
    import_license_file,
    remove_license_file,
)


class LicenseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sk = SigningKey.generate()
        self.vk = self.sk.verify_key
        self.priv_b64 = base64.b64encode(bytes(self.sk)).decode("ascii")
        self.pub_b64 = base64.b64encode(bytes(self.vk)).decode("ascii")
        self._orig_pub = license_mod.PUBLIC_KEY_B64
        license_mod.PUBLIC_KEY_B64 = self.pub_b64

    def tearDown(self) -> None:
        license_mod.PUBLIC_KEY_B64 = self._orig_pub

    def _issue(self, *, days: int = 30, customer: str = "测试客户") -> str:
        return build_signed_license(
            private_key_b64=self.priv_b64,
            customer=customer,
            expires_at=date.today() + timedelta(days=days),
            issued_at=date.today(),
        )

    def test_valid_license(self) -> None:
        text = self._issue(days=10)
        info = check_license_text(text, today=date.today())
        self.assertEqual(info.status, LicenseStatus.VALID)
        self.assertTrue(info.allow_business)
        self.assertEqual(info.customer, "测试客户")
        self.assertEqual(info.days_remaining, 10)

    def test_grace_period(self) -> None:
        text = build_signed_license(
            private_key_b64=self.priv_b64,
            customer="宽限客户",
            expires_at=date.today() - timedelta(days=2),
            issued_at=date.today() - timedelta(days=40),
        )
        info = check_license_text(text, today=date.today())
        self.assertEqual(info.status, LicenseStatus.GRACE)
        self.assertTrue(info.allow_business)

    def test_expired_after_grace(self) -> None:
        text = build_signed_license(
            private_key_b64=self.priv_b64,
            customer="过期客户",
            expires_at=date.today() - timedelta(days=10),
            issued_at=date.today() - timedelta(days=100),
        )
        info = check_license_text(text, today=date.today())
        self.assertEqual(info.status, LicenseStatus.EXPIRED)
        self.assertFalse(info.allow_business)

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = check_license_file(Path(tmp) / "no.lic")
            self.assertEqual(info.status, LicenseStatus.MISSING)
            self.assertFalse(info.allow_business)

    def test_tampered_payload_rejected(self) -> None:
        text = self._issue(days=30)
        bad = text.replace("测试客户", "篡改客户")
        # base64 payload 改不了中文，直接破坏 signature
        import json

        envelope = json.loads(text)
        envelope["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
        info = check_license_text(json.dumps(envelope))
        self.assertEqual(info.status, LicenseStatus.INVALID)

    def test_import_and_reload(self) -> None:
        text = self._issue(days=20)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.lic"
            dest = Path(tmp) / "installed.lic"
            src.write_text(text, encoding="utf-8")
            info = import_license_file(src, dest=dest)
            self.assertEqual(info.status, LicenseStatus.VALID)
            loaded = check_license_file(dest)
            self.assertEqual(loaded.status, LicenseStatus.VALID)
            removed = remove_license_file(dest)
            self.assertEqual(removed.status, LicenseStatus.MISSING)
            self.assertFalse(dest.exists())

    def test_clock_rollback_invalid(self) -> None:
        payload = {
            "product": "tally",
            "customer": "x",
            "issued_at": "2026-08-01",
            "expires_at": "2027-08-01",
            "license_id": "id",
        }
        info = evaluate_payload(payload, today=date(2026, 7, 20))
        self.assertEqual(info.status, LicenseStatus.INVALID)


if __name__ == "__main__":
    unittest.main()
