"""Хэрэглэгчийн нэвтрэлт, олон түрээслэгчийн эрхийн систем.

Загвар: User (глобал хэн бэ) ↔ Membership (аль компанид ямар эрхтэй) —
нэг нябо олон компани хөтөлдөг тул компани бүрд тусдаа эрх (даалгаврын §1, §8).

Эрх: owner (бүрэн) / accountant (бүртгэнэ+батална) / approver (батална) /
viewer (зөвхөн харна).

Токен: HMAC-SHA256, хугацаатай, зөвхөн uid агуулна — компанийн эрх нь
request бүрд Membership-ээс шалгагдана (эрх хураахад токен хүчингүй болно).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, select, DateTime, Integer
from sqlalchemy.orm import Mapped, Session, mapped_column
from datetime import datetime

from .models import Base

SECRET = os.environ.get("BAYAN_SECRET", "dev-secret-солино-уу")
if os.environ.get("ENVIRONMENT") == "production" and SECRET == "dev-secret-солино-уу":
    raise RuntimeError("CRITICAL SECURITY RISK: Default BAYAN_SECRET used in production environment!")
TOKEN_TTL = 12 * 3600
RESET_TTL = 3600
ROLES = ("owner", "chief_accountant", "accountant", "approver", "cashier", "warehouse_manager", "viewer", "auditor")
PERMISSIONS = {
    "post": ("owner", "chief_accountant", "accountant", "cashier", "warehouse_manager"),
    "approve": ("owner", "chief_accountant", "accountant", "approver"),
    "admin": ("owner", "chief_accountant"),
    "read": ROLES,
}


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    pw_hash: Mapped[str] = mapped_column(String(200))
    totp_secret: Mapped[str | None] = mapped_column(String(32), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cpa_license_no: Mapped[str | None] = mapped_column(String(50), default=None, nullable=True)
    privacy_consent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    transaction_lock_days: Mapped[int] = mapped_column(Integer, default=365)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("user_id", "company_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    role: Mapped[str] = mapped_column(String(16))


class AuthError(Exception):
    pass


# ------------------------------------------------------------- нууц үг

def _hash_pw(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def _verify_pw(password: str, stored: str) -> bool:
    salt_b64, dk_b64 = stored.split("$")
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                             base64.b64decode(salt_b64), 200_000)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


# ------------------------------------------------------------- хэрэглэгч

def create_user(session: Session, email: str, name: str, password: str) -> User:
    email = email.lower().strip()
    if len(password) < 8:
        raise AuthError("Нууц үг 8-аас доошгүй тэмдэгт")
    if session.scalar(select(User).where(User.email == email)):
        raise AuthError("Энэ имэйл бүртгэлтэй байна")
    user = User(email=email, name=name, pw_hash=_hash_pw(password))
    session.add(user)
    session.flush()
    return user


def add_membership(session: Session, user_id: str, company_id: str,
                   role: str) -> Membership:
    if role not in ROLES:
        raise AuthError(f"Буруу эрх: {role}")
    existing = session.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.company_id == company_id))
    if existing:
        existing.role = role
        session.flush()
        return existing
    m = Membership(user_id=user_id, company_id=company_id, role=role)
    session.add(m)
    session.flush()
    return m


def memberships_of(session: Session, user_id: str) -> list[Membership]:
    return list(session.scalars(select(Membership).where(
        Membership.user_id == user_id)))


def get_role(session: Session, user_id: str, company_id: str) -> str | None:
    m = session.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.company_id == company_id))
    return m.role if m else None


def change_password(session: Session, user_id: str,
                    old_password: str, new_password: str) -> None:
    user = session.get(User, user_id)
    if not user or not _verify_pw(old_password, user.pw_hash):
        raise AuthError("Хуучин нууц үг буруу")
    if len(new_password) < 8:
        raise AuthError("Нууц үг 8-аас доошгүй тэмдэгт")
    user.pw_hash = _hash_pw(new_password)
    session.flush()


# ------------------------------------------------------------- токен

def _sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _unsign(token: str) -> dict:
    try:
        body, sig = token.rsplit(".", 1)
    except (ValueError, AttributeError):
        raise AuthError("Токен буруу")
    expect = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        raise AuthError("Токены гарын үсэг буруу")
    payload = json.loads(base64.urlsafe_b64decode(body))
    if payload.get("exp", 0) < time.time():
        raise AuthError("Токены хугацаа дууссан")
    return payload


def login(session: Session, email: str, password: str) -> str:
    user = session.scalar(select(User).where(
        User.email == email.lower().strip(), User.active == True))  # noqa: E712
    if user is None or not _verify_pw(password, user.pw_hash):
        raise AuthError("Имэйл эсвэл нууц үг буруу")
    if user.totp_enabled:
        raise AuthError("2FA_REQUIRED")
    return _sign({"uid": user.id, "exp": int(time.time()) + TOKEN_TTL})


def parse_token(token: str) -> dict:
    payload = _unsign(token)
    if "uid" not in payload:
        raise AuthError("Токен буруу")
    return payload


def make_reset_token(user_id: str) -> str:
    """Нууц үг сэргээх нэг удаагийн токен (имэйлээр илгээгдэнэ — SMTP Үе 2)."""
    return _sign({"reset": user_id, "exp": int(time.time()) + RESET_TTL})


def apply_reset_token(session: Session, token: str, new_password: str) -> None:
    payload = _unsign(token)
    if "reset" not in payload:
        raise AuthError("Сэргээх токен биш")
    user = session.get(User, payload["reset"])
    if not user:
        raise AuthError("Хэрэглэгч олдсонгүй")
    if len(new_password) < 8:
        raise AuthError("Нууц үг 8-аас доошгүй тэмдэгт")
    user.pw_hash = _hash_pw(new_password)
    session.flush()


def require(role: str | None, action: str) -> None:
    if role is None or role not in PERMISSIONS.get(action, ()):
        raise AuthError(f"Энэ үйлдэлд эрх хүрэхгүй ({action})")


def generate_totp_secret() -> str:
    """Шинэ 2FA нууц түлхүүр үүсгэх."""
    import secrets
    import base64
    return base64.b32encode(secrets.token_bytes(10)).decode()[:16].upper()


def verify_totp(secret: str, code: str) -> bool:
    """TOTP (RFC 6238) баталгаажуулагч."""
    import time
    import hmac
    import hashlib
    import struct
    import base64
    
    try:
        missing_padding = len(secret) % 8
        if missing_padding:
            secret += "=" * (8 - missing_padding)
        key = base64.b32decode(secret.upper().encode())
        
        t = int(time.time() // 30)
        for step in (t - 1, t, t + 1):
            msg = struct.pack(">Q", step)
            h = hmac.new(key, msg, hashlib.sha1).digest()
            o = h[19] & 15
            token_val = (struct.unpack(">I", h[o:o+4])[0] & 0x7fffffff) % 1000000
            if f"{token_val:06d}" == code.strip():
                return True
    except Exception:
        pass
    return False
