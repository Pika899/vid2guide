"""API keys live in the operating system's credential store, never in project files.

macOS: Keychain. Windows: Credential Manager. If no store is available the key is
kept in memory for this session only, and the page says so.
"""
from __future__ import annotations

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "vid2guide"
PROVIDERS = ("anthropic", "openai")
log = logging.getLogger("vid2guide")

_session_only: dict[str, str] = {}


def get(provider: str) -> str | None:
    if provider in _session_only:
        return _session_only[provider]
    try:
        return keyring.get_password(SERVICE, provider)
    except KeyringError as exc:
        log.warning("Credential store unavailable: %s", exc)
        return None


def set(provider: str, key: str) -> bool:
    """Store the key. Returns False if it could only be kept for this session."""
    key = key.strip()
    try:
        keyring.set_password(SERVICE, provider, key)
        _session_only.pop(provider, None)
        return True
    except KeyringError as exc:
        log.warning("Credential store unavailable, keeping key in memory: %s", exc)
        _session_only[provider] = key
        return False


def delete(provider: str) -> None:
    _session_only.pop(provider, None)
    try:
        keyring.delete_password(SERVICE, provider)
    except (PasswordDeleteError, KeyringError):
        pass


def masked(provider: str) -> str | None:
    key = get(provider)
    if not key:
        return None
    return f"…{key[-4:]}" if len(key) > 8 else "…"
