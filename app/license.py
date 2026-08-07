"""离线 License：Ed25519 验签 + 到期日 + 7 天宽限期。

私钥仅用于签发端（scripts/issue_license.py），勿打包进应用。
客户端只内嵌公钥，可防普通篡改到期日，无法彻底防逆向破解。

安装包不附带授权文件；每次安装后须由用户在应用内手动导入 .lic。
应用启动时不会自动写入或激活任何授权。
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.config import get_license_path

PRODUCT_ID = "tally"
GRACE_DAYS = 7

# 与 keys/ed25519_public.key 对应；重新 gen-keys 后需同步更新
PUBLIC_KEY_B64 = "mz7tL8VdDwq5TDHhnj9tQ0/EovviFdJVy0fougJiCBo="


class LicenseStatus(str, Enum):
    MISSING = "missing"
    VALID = "valid"
    GRACE = "grace"
    EXPIRED = "expired"
    INVALID = "invalid"


@dataclass
class LicenseInfo:
    status: LicenseStatus
    message: str
    customer: str = ""
    license_id: str = ""
    issued_at: Optional[date] = None
    expires_at: Optional[date] = None
    grace_end: Optional[date] = None
    days_remaining: Optional[int] = None

    @property
    def allow_business(self) -> bool:
        return self.status in {LicenseStatus.VALID, LicenseStatus.GRACE}

    @property
    def status_label(self) -> str:
        return {
            LicenseStatus.MISSING: "未激活",
            LicenseStatus.VALID: "有效",
            LicenseStatus.GRACE: "宽限期",
            LicenseStatus.EXPIRED: "已过期",
            LicenseStatus.INVALID: "无效",
        }.get(self.status, self.status.value)


def _parse_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} 缺失")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} 格式无效") from exc


def _verify_key() -> VerifyKey:
    raw = base64.b64decode(PUBLIC_KEY_B64.encode("ascii"))
    return VerifyKey(raw)


def decode_and_verify(raw_text: str) -> dict[str, Any]:
    """校验签名并返回 payload 字典；失败抛 ValueError。"""
    try:
        envelope = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError("授权文件无效或已损坏。") from exc
    if not isinstance(envelope, dict):
        raise ValueError("授权文件无效或已损坏。")
    payload_b64 = envelope.get("payload")
    signature_b64 = envelope.get("signature")
    if not payload_b64 or not signature_b64:
        raise ValueError("授权文件无效或已损坏。")
    try:
        payload_bytes = base64.b64decode(str(payload_b64).encode("ascii"), validate=True)
        signature = base64.b64decode(str(signature_b64).encode("ascii"), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("授权文件无效或已损坏。") from exc
    try:
        _verify_key().verify(payload_bytes, signature)
    except BadSignatureError as exc:
        raise ValueError("授权文件无效或已损坏。") from exc
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("授权文件无效或已损坏。") from exc
    if not isinstance(payload, dict):
        raise ValueError("授权文件无效或已损坏。")
    if str(payload.get("product") or "").strip() != PRODUCT_ID:
        raise ValueError("授权文件无效或已损坏。")
    return payload


def evaluate_payload(payload: dict[str, Any], today: Optional[date] = None) -> LicenseInfo:
    today = today or date.today()
    try:
        issued_at = _parse_date(payload.get("issued_at"), "issued_at")
        expires_at = _parse_date(payload.get("expires_at"), "expires_at")
    except ValueError:
        return LicenseInfo(
            status=LicenseStatus.INVALID,
            message="授权文件无效或已损坏。",
        )
    if expires_at < issued_at:
        return LicenseInfo(
            status=LicenseStatus.INVALID,
            message="授权文件无效或已损坏。",
        )
    # 轻量时钟回拨：系统日期早于签发日超过 1 天视为无效
    if today < issued_at - timedelta(days=1):
        return LicenseInfo(
            status=LicenseStatus.INVALID,
            message="授权文件无效或已损坏。",
        )

    customer = str(payload.get("customer") or "").strip()
    license_id = str(payload.get("license_id") or "").strip()
    grace_end = expires_at + timedelta(days=GRACE_DAYS)

    if today <= expires_at:
        return LicenseInfo(
            status=LicenseStatus.VALID,
            message=f"授权有效，到期日 {expires_at.isoformat()}。",
            customer=customer,
            license_id=license_id,
            issued_at=issued_at,
            expires_at=expires_at,
            grace_end=grace_end,
            days_remaining=(expires_at - today).days,
        )
    if today <= grace_end:
        return LicenseInfo(
            status=LicenseStatus.GRACE,
            message=(
                f"授权已于 {expires_at.isoformat()} 到期，"
                f"宽限至 {grace_end.isoformat()}。请尽快更换新的授权文件。"
            ),
            customer=customer,
            license_id=license_id,
            issued_at=issued_at,
            expires_at=expires_at,
            grace_end=grace_end,
            days_remaining=0,
        )
    return LicenseInfo(
        status=LicenseStatus.EXPIRED,
        message="授权已过期。请更换新的授权文件后继续使用。",
        customer=customer,
        license_id=license_id,
        issued_at=issued_at,
        expires_at=expires_at,
        grace_end=grace_end,
        days_remaining=0,
    )


def check_license_text(raw_text: str, today: Optional[date] = None) -> LicenseInfo:
    try:
        payload = decode_and_verify(raw_text)
    except ValueError as exc:
        return LicenseInfo(status=LicenseStatus.INVALID, message=str(exc))
    return evaluate_payload(payload, today=today)


def check_license_file(
    path: Optional[Path] = None, today: Optional[date] = None
) -> LicenseInfo:
    license_path = Path(path) if path is not None else get_license_path()
    if not license_path.is_file():
        return LicenseInfo(
            status=LicenseStatus.MISSING,
            message="尚未激活。请导入授权文件后使用。",
        )
    try:
        raw = license_path.read_text(encoding="utf-8")
    except OSError:
        return LicenseInfo(
            status=LicenseStatus.MISSING,
            message="尚未激活。请导入授权文件后使用。",
        )
    return check_license_text(raw, today=today)


def import_license_file(source: Path, dest: Optional[Path] = None) -> LicenseInfo:
    """校验并安装授权文件到本地路径。"""
    src = Path(source)
    if not src.is_file():
        raise ValueError("所选授权文件不存在")
    try:
        raw = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取授权文件：{exc}") from exc
    info = check_license_text(raw)
    if info.status in {LicenseStatus.INVALID, LicenseStatus.MISSING}:
        raise ValueError(info.message)
    if info.status == LicenseStatus.EXPIRED:
        raise ValueError(info.message)
    target = Path(dest) if dest is not None else get_license_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
    tmp.replace(target)
    return check_license_file(target)


def remove_license_file(path: Optional[Path] = None) -> LicenseInfo:
    """删除本地授权文件，恢复为未激活状态。"""
    target = Path(path) if path is not None else get_license_path()
    if target.is_file():
        try:
            target.unlink()
        except OSError as exc:
            raise ValueError(f"无法移除授权文件：{exc}") from exc
    return check_license_file(target)


def build_signed_license(
    *,
    private_key_b64: str,
    customer: str,
    expires_at: date,
    issued_at: Optional[date] = None,
    license_id: Optional[str] = None,
) -> str:
    """签发 License JSON 文本（供脚本使用）。"""
    from nacl.signing import SigningKey

    issued = issued_at or date.today()
    payload = {
        "product": PRODUCT_ID,
        "customer": (customer or "").strip() or "未命名客户",
        "issued_at": issued.isoformat(),
        "expires_at": expires_at.isoformat(),
        "license_id": license_id or str(uuid.uuid4()),
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    seed = base64.b64decode(private_key_b64.encode("ascii"))
    signing_key = SigningKey(seed)
    signature = signing_key.sign(payload_bytes).signature
    envelope = {
        "payload": base64.b64encode(payload_bytes).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2) + "\n"
