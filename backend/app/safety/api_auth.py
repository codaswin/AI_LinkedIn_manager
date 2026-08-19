from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import structlog
from app.database import get_session_factory
from app.models.auth import DashboardSessionRecord, DashboardUserRecord, LoginAuditRecord
from app.shared_state import get_client
from fastapi import HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

SESSION_COOKIE_NAME = "dashboard_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
_PUBLIC_PATH_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/auth/login")
Role = Literal["viewer", "operator", "admin"]
_ROLE_LEVEL = {"viewer": 10, "operator": 20, "admin": 30}


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    username: str
    role: Role


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Dashboard passwords must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False


def is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_PATH_PREFIXES)


def session_cookie_secure() -> bool:
    return os.environ.get("SESSION_COOKIE_SECURE", "true").lower() not in {"0", "false", "no"}


def _login_attempt_key(username: str, remote_address: str | None) -> str:
    identity = f"{username}|{remote_address or 'unknown'}"
    return f"auth:login-attempts:{_token_hash(identity)}"


def _enforce_login_limit(username: str, remote_address: str | None) -> str:
    key = _login_attempt_key(username, remote_address)
    maximum = max(1, int(os.environ.get("DASHBOARD_LOGIN_MAX_ATTEMPTS", "5")))
    if int(get_client().get(key) or 0) >= maximum:
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later")
    return key


def session_ttl() -> timedelta:
    hours = int(os.environ.get("SESSION_TTL_HOURS", "12"))
    return timedelta(hours=max(1, min(hours, 168)))


async def list_dashboard_users(db: AsyncSession) -> list[DashboardUserRecord]:
    result = await db.execute(select(DashboardUserRecord).order_by(DashboardUserRecord.created_at))
    return list(result.scalars().all())


async def create_user(db: AsyncSession, username: str, password: str, role: Role = "viewer") -> DashboardUserRecord:
    normalized = username.strip().lower()
    if not normalized:
        raise ValueError("username is required")
    if role not in _ROLE_LEVEL:
        raise ValueError(f"Unknown dashboard role: {role}")
    if (
        await db.execute(select(DashboardUserRecord).where(DashboardUserRecord.username == normalized))
    ).scalar_one_or_none():
        raise ValueError(f"Dashboard user {normalized!r} already exists")
    user = DashboardUserRecord(
        id=str(uuid.uuid4()), username=normalized, password_hash=hash_password(password), role=role, active=True
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def ensure_bootstrap_admin(db: AsyncSession) -> None:
    username = os.environ.get("DASHBOARD_ADMIN_USERNAME", "").strip().lower()
    password = os.environ.get("DASHBOARD_ADMIN_PASSWORD", "")
    if not username or not password:
        logger.warning("dashboard_admin_not_bootstrapped")
        return
    existing = (
        await db.execute(select(DashboardUserRecord).where(DashboardUserRecord.username == username))
    ).scalar_one_or_none()
    if existing is None:
        await create_user(db, username, password, role="admin")
        logger.warning("dashboard_admin_bootstrapped", username=username)
    elif not verify_password(password, existing.password_hash):
        now = _utcnow()
        existing.password_hash = hash_password(password)
        existing.role = "admin"
        existing.active = True
        await db.execute(
            update(DashboardSessionRecord)
            .where(
                DashboardSessionRecord.user_id == existing.id,
                DashboardSessionRecord.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await db.commit()
        logger.warning("dashboard_admin_password_rotated", username=username)


async def authenticate_credentials(
    db: AsyncSession, username: str, password: str, remote_address: str | None
) -> tuple[AuthenticatedUser, str, str]:
    normalized = username.strip().lower()
    attempt_key = _enforce_login_limit(normalized, remote_address)
    user = (
        await db.execute(select(DashboardUserRecord).where(DashboardUserRecord.username == normalized))
    ).scalar_one_or_none()
    succeeded = bool(user and user.active and verify_password(password, user.password_hash))
    db.add(
        LoginAuditRecord(id=str(uuid.uuid4()), username=normalized, succeeded=succeeded, remote_address=remote_address)
    )
    if not succeeded or user is None:
        await db.commit()
        client = get_client()
        attempts = client.incr(attempt_key)
        if attempts == 1:
            client.expire(attempt_key, int(os.environ.get("DASHBOARD_LOGIN_WINDOW_SECONDS", "900")))
        raise HTTPException(status_code=401, detail="Invalid username or password")

    get_client().delete(attempt_key)
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = _utcnow()
    session = DashboardSessionRecord(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=_token_hash(session_token),
        csrf_hash=_token_hash(csrf_token),
        created_at=now,
        expires_at=now + session_ttl(),
        last_seen_at=now,
    )
    user.last_login_at = now
    db.add(session)
    await db.commit()
    return AuthenticatedUser(user.id, user.username, user.role), session_token, csrf_token


async def _session_and_user(
    db: AsyncSession, session_token: str
) -> tuple[DashboardSessionRecord, DashboardUserRecord] | None:
    result = await db.execute(
        select(DashboardSessionRecord, DashboardUserRecord)
        .join(DashboardUserRecord, DashboardUserRecord.id == DashboardSessionRecord.user_id)
        .where(DashboardSessionRecord.token_hash == _token_hash(session_token))
    )
    pair = result.one_or_none()
    if pair is None:
        return None
    session, user = pair
    if session.revoked_at is not None or _as_utc(session.expires_at) <= _utcnow() or not user.active:
        return None
    return session, user


async def authenticate_request(request: Request) -> AuthenticatedUser:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    async with get_session_factory()() as db:
        pair = await _session_and_user(db, token)
        if pair is None:
            raise HTTPException(status_code=401, detail="Session is invalid or expired")
        session, user = pair
        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            csrf = request.headers.get(CSRF_HEADER_NAME, "")
            if not csrf or not hmac.compare_digest(_token_hash(csrf), session.csrf_hash):
                raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
        request.state.auth_user = AuthenticatedUser(user.id, user.username, user.role)
        request.state.auth_session_id = session.id
        return request.state.auth_user


def current_user(request: Request) -> AuthenticatedUser:
    user = getattr(request.state, "auth_user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_role(request: Request, minimum: Role = "viewer") -> AuthenticatedUser:
    user = current_user(request)
    if _ROLE_LEVEL[user.role] < _ROLE_LEVEL[minimum]:
        raise HTTPException(status_code=403, detail=f"{minimum} role required")
    return user


def authorize_request(request: Request, user: AuthenticatedUser) -> None:
    # Credentials, approvals, brand voice, etc. are no longer a single
    # shared admin-only resource — every table they touch is scoped to
    # request.state's current user (see app.tenancy.context), so a
    # "viewer" role can safely read/manage their OWN data here. `admin`
    # is checked separately, per-endpoint (see /admin/users in main.py),
    # for the one thing that's still cross-tenant: inviting new users.
    method = request.method.upper()
    minimum: Role = "viewer" if method in {"GET", "HEAD", "OPTIONS"} else "operator"
    if _ROLE_LEVEL[user.role] < _ROLE_LEVEL[minimum]:
        raise HTTPException(status_code=403, detail=f"{minimum} role required")


async def rotate_csrf_token(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    async with get_session_factory()() as db:
        pair = await _session_and_user(db, token)
        if pair is None:
            raise HTTPException(status_code=401, detail="Session is invalid or expired")
        session, _ = pair
        csrf = secrets.token_urlsafe(32)
        session.csrf_hash = _token_hash(csrf)
        session.last_seen_at = _utcnow()
        await db.commit()
        return csrf


async def revoke_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    async with get_session_factory()() as db:
        pair = await _session_and_user(db, token)
        if pair is not None:
            pair[0].revoked_at = _utcnow()
            await db.commit()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(session_ttl().total_seconds()),
        httponly=True,
        secure=session_cookie_secure(),
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="strict")
