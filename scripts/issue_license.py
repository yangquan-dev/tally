#!/usr/bin/env python3
"""本地签发离线 License（私钥勿提交、勿打包）。

快捷用法（交互式，推荐）:
  python scripts/issue_license.py
  # 或
  ./scripts/签发授权.sh

命令行用法:
  # 生成密钥对到 keys/
  python scripts/issue_license.py gen-keys

  # 签发（私钥默认 keys/ed25519_private.key，输出默认桌面）
  python scripts/issue_license.py issue --customer "某某物业" --days 365

  # 指定到期日与输出路径
  python scripts/issue_license.py issue \\
    --customer "某某物业" \\
    --expires 2027-12-31 \\
    --out ~/Desktop/某某物业.lic

生成新密钥后，请将公钥 Base64 同步写入 app/license.py 的 PUBLIC_KEY_B64。

注意：签发产物仅作分发用；用户必须在安装后于应用内「导入授权」。
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nacl.signing import SigningKey

from app.license import PUBLIC_KEY_B64, build_signed_license

DEFAULT_PRIVATE_KEY = ROOT / "keys" / "ed25519_private.key"
DEFAULT_KEYS_DIR = ROOT / "keys"


def _desktop_dir() -> Path:
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return desktop
    return Path.home()


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return cleaned.strip("._") or "customer"


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or default


def _prompt_int(text: str, default: int) -> int:
    raw = _prompt(text, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"请输入整数: {raw}") from exc


def cmd_gen_keys(out_dir: Path, *, force: bool = False) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    priv_path = out_dir / "ed25519_private.key"
    pub_path = out_dir / "ed25519_public.key"
    if (priv_path.exists() or pub_path.exists()) and not force:
        raise SystemExit(
            f"密钥已存在于 {out_dir}，如需覆盖请加 --force\n"
            f"  {priv_path}\n  {pub_path}"
        )
    sk = SigningKey.generate()
    vk = sk.verify_key
    priv = base64.b64encode(bytes(sk)).decode("ascii")
    pub = base64.b64encode(bytes(vk)).decode("ascii")
    priv_path.write_text(priv + "\n", encoding="utf-8")
    pub_path.write_text(pub + "\n", encoding="utf-8")
    print(f"私钥: {priv_path}")
    print(f"公钥: {pub_path}")
    print()
    print("请将下列常量写入 app/license.py：")
    print(f'PUBLIC_KEY_B64 = "{pub}"')
    print()
    print("当前应用内嵌公钥为：")
    print(f'PUBLIC_KEY_B64 = "{PUBLIC_KEY_B64}"')
    if pub != PUBLIC_KEY_B64:
        print()
        print("⚠ 新公钥与应用内嵌公钥不一致，签发的授权将无法被当前应用验证。")


def cmd_issue(
    *,
    private_key: Path,
    customer: str,
    days: int | None,
    expires: str,
    issued: str,
    out: Path | None,
) -> Path:
    key_path = private_key.expanduser()
    if not key_path.is_file():
        raise SystemExit(
            f"私钥不存在: {key_path}\n"
            f"请先运行: python scripts/issue_license.py gen-keys"
        )
    customer = customer.strip()
    if not customer:
        raise SystemExit("客户名称不能为空")

    private_key_b64 = key_path.read_text(encoding="utf-8").strip()
    if expires:
        expires_at = date.fromisoformat(expires)
    else:
        expires_at = date.today() + timedelta(days=int(days if days is not None else 365))

    text = build_signed_license(
        private_key_b64=private_key_b64,
        customer=customer,
        expires_at=expires_at,
        issued_at=date.fromisoformat(issued) if issued else None,
    )

    if out is None:
        out = _desktop_dir() / f"{_safe_filename(customer)}-{expires_at.isoformat()}.lic"
    else:
        out = out.expanduser()
        if out.is_dir() or str(out).endswith(("/", "\\")):
            out = out / f"{_safe_filename(customer)}-{expires_at.isoformat()}.lic"
        elif out.suffix.lower() != ".lic":
            out = out.with_suffix(out.suffix + ".lic") if out.suffix else out.with_suffix(".lic")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    print()
    print("—— 签发成功 ——")
    print(f"文件: {out}")
    print(f"客户: {customer}")
    print(f"到期: {expires_at.isoformat()}")
    print(f"私钥: {key_path}")
    print()
    print("请将 .lic 发给客户，在应用「通用配置 → 软件授权 → 导入授权」中导入。")
    return out


def cmd_interactive() -> None:
    print("==============================")
    print("  本地记账 · 授权签发工具")
    print("==============================")
    print()
    print("1) 签发授权（常用）")
    print("2) 生成密钥对")
    print("0) 退出")
    print()
    choice = _prompt("请选择", "1")

    if choice in {"0", "q", "Q"}:
        return

    if choice == "2":
        out = Path(_prompt("密钥输出目录", str(DEFAULT_KEYS_DIR))).expanduser()
        force = _prompt("若已存在是否覆盖？(y/N)", "N").lower() in {"y", "yes"}
        cmd_gen_keys(out, force=force)
        return

    if choice != "1":
        raise SystemExit(f"未知选项: {choice}")

    if not DEFAULT_PRIVATE_KEY.is_file():
        print(f"未找到默认私钥: {DEFAULT_PRIVATE_KEY}")
        if _prompt("是否现在生成密钥对？(Y/n)", "Y").lower() not in {"n", "no"}:
            cmd_gen_keys(DEFAULT_KEYS_DIR)
            print()
        else:
            raise SystemExit("无私钥，已取消")

    customer = _prompt("客户名称")
    while not customer.strip():
        customer = _prompt("客户名称不能为空，请重新输入")

    mode = _prompt("有效期方式：1=天数  2=到期日", "1")
    days: int | None = 365
    expires = ""
    if mode == "2":
        expires = _prompt("到期日 YYYY-MM-DD")
        days = None
    else:
        days = _prompt_int("有效天数", 365)

    default_out = str(
        _desktop_dir()
        / f"{_safe_filename(customer)}-{(date.fromisoformat(expires) if expires else date.today() + timedelta(days=days or 365)).isoformat()}.lic"
    )
    out_raw = _prompt("输出路径（回车用默认）", default_out)
    private_key = Path(
        _prompt("私钥路径", str(DEFAULT_PRIVATE_KEY))
    ).expanduser()

    cmd_issue(
        private_key=private_key,
        customer=customer,
        days=days,
        expires=expires,
        issued="",
        out=Path(out_raw) if out_raw else None,
    )


def main() -> None:
    # 无参数 → 交互式
    if len(sys.argv) == 1:
        try:
            cmd_interactive()
        except KeyboardInterrupt:
            print("\n已取消")
        return

    parser = argparse.ArgumentParser(
        description="本地记账 · 离线 License 签发工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不带参数运行将进入交互式菜单。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("gen-keys", help="生成 Ed25519 密钥对")
    p_gen.add_argument(
        "--out",
        default=str(DEFAULT_KEYS_DIR),
        help="输出目录（默认 keys/）",
    )
    p_gen.add_argument(
        "--force",
        action="store_true",
        help="覆盖已有密钥",
    )

    p_issue = sub.add_parser("issue", help="签发 License 文件")
    p_issue.add_argument(
        "--private-key",
        default=str(DEFAULT_PRIVATE_KEY),
        help=f"私钥路径（默认 {DEFAULT_PRIVATE_KEY}）",
    )
    p_issue.add_argument("--customer", required=True, help="客户名称")
    p_issue.add_argument("--days", type=int, default=365, help="有效天数（默认 365）")
    p_issue.add_argument(
        "--expires", default="", help="到期日 YYYY-MM-DD（优先于 --days）"
    )
    p_issue.add_argument("--issued", default="", help="签发日 YYYY-MM-DD（默认今天）")
    p_issue.add_argument(
        "--out",
        default="",
        help="输出 .lic 路径（默认桌面：客户名-到期日.lic）",
    )

    p_ui = sub.add_parser("interactive", help="交互式菜单")

    args = parser.parse_args()
    if args.command == "gen-keys":
        cmd_gen_keys(Path(args.out).expanduser(), force=args.force)
    elif args.command == "issue":
        cmd_issue(
            private_key=Path(args.private_key),
            customer=args.customer,
            days=None if args.expires else args.days,
            expires=args.expires,
            issued=args.issued,
            out=Path(args.out) if args.out else None,
        )
    elif args.command == "interactive":
        try:
            cmd_interactive()
        except KeyboardInterrupt:
            print("\n已取消")
    else:
        parser.error(f"未知命令: {args.command}")


if __name__ == "__main__":
    main()
