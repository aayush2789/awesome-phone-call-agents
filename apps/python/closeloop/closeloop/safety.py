"""CloseLoop Safety Engine Primitives and Invariants.

Enforces the twelve safety invariants defined in the CloseLoop Safety Contract.
All safety checks are fail-closed.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

E164_PATTERN = re.compile(r"^\+[1-9]\d{6,14}$")
PHONE_LIKE_PATTERN = re.compile(r"(\+[1-9]\d{2,3})(\d{3,4})(\d{4})")

SENSITIVE_KEY_NAMES = {
    "token",
    "auth",
    "authorization",
    "cookie",
    "secret",
    "api_key",
    "apikey",
    "confirmation_token",
    "access_token",
    "refresh_token",
    "bearer",
    "login_url",
    "password",
}

PROHIBITED_DOMAIN_PATTERNS = [
    (re.compile(r"\b(diagnos(?:e|is|ing)|prescrib(?:e|ing)|treatment plan|emergency triage)\b", re.IGNORECASE), "medical_advice"),
    (re.compile(r"\b(investment advice|buy stocks?|sell securities|financial counsel)\b", re.IGNORECASE), "financial_advice"),
    (re.compile(r"\b(legal counsel|file lawsuit|plead guilty|settlement advice)\b", re.IGNORECASE), "legal_advice"),
    (re.compile(r"\b(call 911|emergency rescue|dispatch ambulance|active life threat)\b", re.IGNORECASE), "emergency_response"),
]


class SafetyViolationError(Exception):
    """Base exception for all CloseLoop safety invariant violations."""


class E164ValidationError(SafetyViolationError):
    """Raised when a phone number violates ITU-T E.164 format."""


class QuietHoursViolationError(SafetyViolationError):
    """Raised when an attempt is scheduled or initiated within quiet hours."""


class SuppressionViolationError(SafetyViolationError):
    """Raised when attempting to contact a number on the suppression list."""


class KillSwitchActiveError(SafetyViolationError):
    """Raised when the out-of-band kill switch is engaged."""


class ConsentMissingError(SafetyViolationError):
    """Raised when a contact rung lacks an explicit, documented consent basis."""


class SensitiveDomainViolationError(SafetyViolationError):
    """Raised when a rendered call goal violates prohibited domain boundaries."""


def validate_e164(phone: str) -> bool:
    """Validate phone number strictly against ITU-T E.164.

    Must start with '+' followed by country code and subscriber digits (7 to 15 digits total).
    """
    if not isinstance(phone, str):
        return False
    return bool(E164_PATTERN.match(phone.strip()))


def mask_phone(phone: str) -> str:
    """Mask a phone number for privacy in user-facing outputs and logs.

    Retains the country code and first few digits, replacing the terminal 4 digits with ****.
    Example: +15550101234 -> +1555010****
    """
    if not phone:
        return ""
    stripped = phone.strip()
    if len(stripped) < 7:
        return "****"
    return stripped[:-4] + "****"


def parse_time_str(time_str: str) -> time:
    """Parse a 24-hour time string in HH:MM format."""
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{time_str}', expected 'HH:MM'")
    hour, minute = int(parts[0]), int(parts[1])
    return time(hour=hour, minute=minute)


def is_in_quiet_hours(
    quiet_start: str,
    quiet_end: str,
    timezone_str: str,
    current_dt: datetime | None = None,
) -> bool:
    """Check if the current time in the target timezone falls within quiet hours.

    Handles both same-day windows (e.g. 13:00 to 15:00) and overnight windows
    crossing midnight (e.g. 21:00 to 09:00).
    """
    try:
        tz = ZoneInfo(timezone_str.strip())
    except ZoneInfoNotFoundError as err:
        raise ValueError(f"Unknown or invalid IANA timezone: '{timezone_str}'") from err

    start_time = parse_time_str(quiet_start)
    end_time = parse_time_str(quiet_end)

    if current_dt is None:
        target_dt = datetime.now(tz)
    else:
        if current_dt.tzinfo is None:
            target_dt = current_dt.replace(tzinfo=tz)
        else:
            target_dt = current_dt.astimezone(tz)

    target_time = target_dt.time()

    if start_time < end_time:
        # Same-day window (e.g., 12:00 to 14:00)
        return start_time <= target_time < end_time
    elif start_time > end_time:
        # Overnight window crossing midnight (e.g., 21:00 to 09:00)
        return target_time >= start_time or target_time < end_time
    else:
        # start == end indicates a 24-hour quiet window
        return True


def check_kill_switch(
    flag_env: str = "CLOSELOOP_KILL_SWITCH",
    flag_file: str | Path = ".closeloop_kill_switch",
) -> bool:
    """Check whether the kill switch is currently engaged via env or file.

    Returns True if either condition is met.
    """
    env_val = os.environ.get(flag_env, "").strip().lower()
    if env_val in {"1", "true", "yes", "on", "active"}:
        return True

    file_path = Path(flag_file)
    return file_path.is_file()


def check_suppression(
    phone: str,
    suppression_registry: set[str] | list[str] | tuple[str, ...],
) -> bool:
    """Check if a phone number is present in the suppression registry."""
    normalized = phone.strip()
    return normalized in set(suppression_registry)


def validate_consent_basis(consent_basis: str | None) -> bool:
    """Verify that a contact rung specifies a valid, non-empty consent basis."""
    if not consent_basis or not isinstance(consent_basis, str):
        return False
    # Must be meaningful text, not a placeholder
    return len(consent_basis.strip()) >= 5


def check_domain_boundaries(goal_text: str) -> list[str]:
    """Inspect rendered goal text for prohibited advice or emergency claims.

    Returns a list of violation codes if any prohibited domain pattern is found.
    """
    violations: list[str] = []
    for pattern, violation_code in PROHIBITED_DOMAIN_PATTERNS:
        if pattern.search(goal_text):
            violations.append(violation_code)
    return violations


def sanitize_log_data(data: Any) -> Any:
    """Recursively sanitize data structures to prevent sensitive data leakage.

    Redacts keys associated with tokens, secrets, credentials, and passwords.
    Masks phone numbers in string values.
    """
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(sensitive in key_lower for sensitive in SENSITIVE_KEY_NAMES):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = sanitize_log_data(v)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_log_data(item) for item in data]
    elif isinstance(data, str):
        # Mask phone numbers in text
        if validate_e164(data):
            return mask_phone(data)
        return data
    return data
