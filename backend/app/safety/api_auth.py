from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

_AUTH_HEADER = "X-Dashboard-API-Key"
_PUBLIC_PATH_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


def dashboard_auth_enabled() -> bool:
    return bool(os.environ.get("DASHBOARD_API_KEY", "").strip())


def is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_PATH_PREFIXES)


def require_dashboard_api_key(request: Request) -> None:
    expected = os.environ.get("DASHBOARD_API_KEY", "").strip()
    if not expected:
        return
    supplied = request.headers.get(_AUTH_HEADER, "")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail=f"Missing or invalid {_AUTH_HEADER} header")
