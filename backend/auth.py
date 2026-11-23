"""Signed-token authentication, role-based access control, and session ownership binding.

Uses stdlib HMAC-SHA256 signed tokens (base64url(payload).base64url(signature)) so offline
verification stays zero-dependency and zero-network. In production on GCP, this layer maps
directly to OIDC / Identity-Aware Proxy (IAP) or API Gateway JWT verification.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import Depends, Header, HTTPException

Role = Literal["customer", "reviewer"]

_DEFAULT_DEV_SECRET = "dev-local-auth-secret-do-not-use-in-prod"


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: Role


def _secret(override: str | None = None) -> bytes:
    val = override if override is not None else os.getenv("AUTH_SECRET", _DEFAULT_DEV_SECRET)
    return val.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(encoded: str) -> bytes:
    pad = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + pad)


def issue_token(
    user_id: str,
    role: Role = "customer",
    ttl_s: int = 3600,
    secret: str | None = None,
) -> str:
    """Mint a signed bearer token carrying subject, role, and expiry."""
    if not user_id or not user_id.strip():
        raise ValueError("user_id must be non-empty")
    if role not in ("customer", "reviewer"):
        raise ValueError(f"unsupported role: {role}")
    payload = {
        "sub": user_id.strip(),
        "role": role,
        "exp": int(time.time()) + int(ttl_s),
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(_secret(secret), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64e(sig)}"


def verify_token(token: str, secret: str | None = None) -> Principal:
    """Validate token signature, structure, role, and expiry. Raises HTTP 401 on failure."""
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="invalid authentication token")
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="invalid authentication token")
    payload_b64, sig_b64 = parts
    expected_sig = hmac.new(_secret(secret), payload_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        actual_sig = _b64d(sig_b64)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid token encoding") from exc
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise HTTPException(status_code=401, detail="invalid token signature")
    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid token payload") from exc
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        raise HTTPException(status_code=401, detail="token expired")
    sub = payload.get("sub")
    role = payload.get("role")
    if not isinstance(sub, str) or not sub.strip() or role not in ("customer", "reviewer"):
        raise HTTPException(status_code=401, detail="invalid token claims")
    return Principal(user_id=sub.strip(), role=role)


def require_authenticated(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency requiring a valid Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    return verify_token(token)


def require_reviewer(principal: Principal = Depends(require_authenticated)) -> Principal:
    """FastAPI dependency requiring an authenticated reviewer role."""
    if principal.role != "reviewer":
        raise HTTPException(status_code=403, detail="reviewer role required")
    return principal


class SessionOwnershipStore:
    """Tracks thread_id -> owner_id bindings to block cross-customer session takeover."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mem: dict[str, str] = {}

    def _use_memory(self) -> bool:
        return os.getenv("SESSIONS", "sqlite") == "memory"

    def _db_path(self) -> Path:
        data_dir = Path(os.getenv("BACKEND_DATA_DIR", ".data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "session_owners.db"

    def bind_or_verify(self, thread_id: str, user_id: str) -> None:
        """Bind an unbound thread_id to user_id, or raise HTTP 403 if owned by someone else."""
        with self._lock:
            if self._use_memory():
                existing = self._mem.get(thread_id)
                if existing is None:
                    self._mem[thread_id] = user_id
                    return
                if existing != user_id:
                    raise HTTPException(status_code=403, detail="session belongs to another user")
                return

            conn = sqlite3.connect(str(self._db_path()), check_same_thread=False)
            try:
                with conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS session_owners ("
                        "thread_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"
                    )
                    row = conn.execute(
                        "SELECT owner_id FROM session_owners WHERE thread_id = ?",
                        (thread_id,),
                    ).fetchone()
                    if row is None:
                        conn.execute(
                            "INSERT INTO session_owners (thread_id, owner_id) VALUES (?, ?)",
                            (thread_id, user_id),
                        )
                    elif row[0] != user_id:
                        raise HTTPException(status_code=403, detail="session belongs to another user")
            finally:
                conn.close()

    def clear_memory(self) -> None:
        with self._lock:
            self._mem.clear()


session_owners = SessionOwnershipStore()
