"""Tests for the CloseLoop Safety Contract and Safety Engine Primitives."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from closeloop.safety import (
    check_domain_boundaries,
    check_kill_switch,
    check_suppression,
    is_in_quiet_hours,
    mask_phone,
    sanitize_log_data,
    validate_consent_basis,
    validate_e164,
)


class TestE164Validation:
    """Test ITU-T E.164 phone number formatting compliance."""

    @pytest.mark.parametrize(
        "phone",
        [
            "+15550101234",
            "+15550105678",
            "+442071838750",
            "+919876543210",
            "+81312345678",
        ],
    )
    def test_valid_e164_numbers(self, phone: str):
        assert validate_e164(phone) is True

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "15550101234",  # Missing leading +
            "+0123456789",  # Country codes do not begin with 0
            "+1-555-010-1234",  # Contains hyphens
            "+1 (555) 010-1234",  # Contains formatting characters
            "+12345",  # Too short (< 7 digits total)
            "+12345678901234567",  # Too long (> 15 digits total)
            "",  # Empty string
            "   ",  # Whitespace
            "not-a-number",
            None,
        ],
    )
    def test_invalid_e164_numbers(self, invalid_phone: str | None):
        assert validate_e164(invalid_phone) is False


class TestPhoneMasking:
    """Test user-facing phone number redaction."""

    def test_mask_e164_phone(self):
        assert mask_phone("+15550101234") == "+1555010****"
        assert mask_phone("+919876543210") == "+91987654****"

    def test_mask_short_or_empty(self):
        assert mask_phone("") == ""
        assert mask_phone("123") == "****"


class TestQuietHoursEnforcement:
    """Test quiet hours checking across declared timezones."""

    def test_overnight_quiet_hours_active_night(self):
        # Window: 21:00 to 09:00
        # Time: 23:30 Asia/Kolkata (Night) -> should be quiet
        test_dt = datetime(2026, 9, 3, 23, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert is_in_quiet_hours("21:00", "09:00", "Asia/Kolkata", test_dt) is True

    def test_overnight_quiet_hours_active_early_morning(self):
        # Window: 21:00 to 09:00
        # Time: 06:15 Asia/Kolkata (Early Morning) -> should be quiet
        test_dt = datetime(2026, 9, 3, 6, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert is_in_quiet_hours("21:00", "09:00", "Asia/Kolkata", test_dt) is True

    def test_overnight_quiet_hours_inactive_afternoon(self):
        # Window: 21:00 to 09:00
        # Time: 14:00 Asia/Kolkata (Afternoon) -> NOT quiet
        test_dt = datetime(2026, 9, 3, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert is_in_quiet_hours("21:00", "09:00", "Asia/Kolkata", test_dt) is False

    def test_same_day_quiet_hours(self):
        # Window: 13:00 to 15:00
        in_window = datetime(2026, 9, 3, 14, 0, tzinfo=ZoneInfo("UTC"))
        out_window = datetime(2026, 9, 3, 16, 0, tzinfo=ZoneInfo("UTC"))
        assert is_in_quiet_hours("13:00", "15:00", "UTC", in_window) is True
        assert is_in_quiet_hours("13:00", "15:00", "UTC", out_window) is False

    def test_timezone_conversion(self):
        # 16:00 UTC is 21:30 in Asia/Kolkata (UTC + 5:30)
        # So it falls within 21:00-09:00 in Asia/Kolkata
        utc_dt = datetime(2026, 9, 3, 16, 0, tzinfo=ZoneInfo("UTC"))
        assert is_in_quiet_hours("21:00", "09:00", "Asia/Kolkata", utc_dt) is True

    def test_invalid_timezone_raises(self):
        with pytest.raises(ValueError, match="Unknown or invalid IANA timezone"):
            is_in_quiet_hours("21:00", "09:00", "Invalid/Timezone")


class TestKillSwitch:
    """Test out-of-band kill switch activation."""

    def test_kill_switch_via_env(self, monkeypatch):
        monkeypatch.delenv("CLOSELOOP_KILL_SWITCH", raising=False)
        assert check_kill_switch() is False

        monkeypatch.setenv("CLOSELOOP_KILL_SWITCH", "1")
        assert check_kill_switch() is True

        monkeypatch.setenv("CLOSELOOP_KILL_SWITCH", "true")
        assert check_kill_switch() is True

    def test_kill_switch_via_file(self, tmp_path):
        flag_file = tmp_path / ".kill_flag"
        assert check_kill_switch(flag_file=flag_file) is False

        flag_file.touch()
        assert check_kill_switch(flag_file=flag_file) is True


class TestSuppressionRegistry:
    """Test suppression list checking."""

    def test_suppressed_phone_detected(self):
        suppressed_numbers = {"+15550109999", "+15550108888"}
        assert check_suppression("+15550109999", suppressed_numbers) is True
        assert check_suppression("+15550101234", suppressed_numbers) is False


class TestConsentBasis:
    """Test consent verification."""

    def test_valid_consent(self):
        assert validate_consent_basis("Candidate opted in during placement registration") is True
        assert validate_consent_basis("Requested callback via online web form") is True

    def test_invalid_consent(self):
        assert validate_consent_basis("") is False
        assert validate_consent_basis("   ") is False
        assert validate_consent_basis("no") is False
        assert validate_consent_basis(None) is False


class TestDomainBoundaries:
    """Test high-risk domain advice boundary checks."""

    def test_safe_logistics_goal(self):
        goal = "Call candidate John to confirm attendance for the mock interview slot tomorrow at 3 PM."
        assert check_domain_boundaries(goal) == []

    def test_prohibited_medical_advice(self):
        goal = "Call patient to diagnose symptoms and prescribe treatment plan."
        violations = check_domain_boundaries(goal)
        assert "medical_advice" in violations

    def test_prohibited_financial_advice(self):
        goal = "Call client to provide investment advice and recommend they buy stocks."
        violations = check_domain_boundaries(goal)
        assert "financial_advice" in violations

    def test_prohibited_legal_advice(self):
        goal = "Provide legal counsel on whether to accept the settlement advice."
        violations = check_domain_boundaries(goal)
        assert "legal_advice" in violations

    def test_prohibited_emergency_dispatch(self):
        goal = "Call 911 dispatch for immediate emergency rescue."
        violations = check_domain_boundaries(goal)
        assert "emergency_response" in violations


class TestLogSanitization:
    """Test redacting secrets and masking phones in logs and summaries."""

    def test_sanitize_nested_data(self):
        raw_payload = {
            "workflow_id": "wf-101",
            "recipient_phone": "+15550101234",
            "auth_token": "secret-token-value-12345",
            "metadata": {
                "user_id": "usr_99",
                "confirmation_token": "conf-tok-abcde",
                "target_phone": "+15550105678",
                "login_url": "https://example.com/login?token=xyz",
            },
            "history": [
                {"status": "planned", "phone": "+15550109999"},
                {"secret_key": "very-secret"},
            ],
        }

        sanitized = sanitize_log_data(raw_payload)

        # Non-sensitive keys retained
        assert sanitized["workflow_id"] == "wf-101"
        assert sanitized["metadata"]["user_id"] == "usr_99"

        # Sensitive keys redacted
        assert sanitized["auth_token"] == "[REDACTED]"
        assert sanitized["metadata"]["confirmation_token"] == "[REDACTED]"
        assert sanitized["metadata"]["login_url"] == "[REDACTED]"
        assert sanitized["history"][1]["secret_key"] == "[REDACTED]"

        # Phone numbers masked
        assert sanitized["recipient_phone"] == "+1555010****"
        assert sanitized["metadata"]["target_phone"] == "+1555010****"
        assert sanitized["history"][0]["phone"] == "+1555010****"
