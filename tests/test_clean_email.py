"""Tests for policydb.utils.clean_email.

Covers the common input shapes the function must support (Outlook paste,
angle-bracket wrapping, mailto: prefix, trailing punctuation, plus-tags,
subdomains) and pins the ReDoS mitigation so pathological input returns
quickly without hanging on polynomial backtracking.
"""

from __future__ import annotations

import time

import pytest

from policydb.utils import clean_email


class TestCleanEmailRealInputs:
    """Common pasted formats — behavior must be stable."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Jane Doe <jane@example.com>", "jane@example.com"),
            ("<jane@example.com>", "jane@example.com"),
            ("mailto:jane@example.com", "jane@example.com"),
            ("MAILTO:jane@example.com", "jane@example.com"),
            ("jane@example.com;", "jane@example.com"),
            ("jane@example.com,", "jane@example.com"),
            (" jane@example.com ", "jane@example.com"),
            ('"jane@example.com"', "jane@example.com"),
            ("(e) user@domain.com", "user@domain.com"),
            ("JANE@EXAMPLE.COM", "jane@example.com"),
        ],
    )
    def test_basic_formats(self, raw: str, expected: str) -> None:
        assert clean_email(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("John <john.smith+tag@sub.example.com>", "john.smith+tag@sub.example.com"),
            ("jane.q.doe@mail.gov", "jane.q.doe@mail.gov"),
            ("x+y.z@foo.bar.baz", "x+y.z@foo.bar.baz"),
            ("first-last@marsh.com", "first-last@marsh.com"),
            ("num123@agency.co.uk", "num123@agency.co.uk"),
        ],
    )
    def test_dots_plus_tags_and_subdomains(self, raw: str, expected: str) -> None:
        assert clean_email(raw) == expected

    def test_empty_and_none_inputs(self) -> None:
        assert clean_email("") == ""
        assert clean_email("   ") == ""
        assert clean_email(None) == ""  # type: ignore[arg-type]

    def test_non_email_input_normalised(self) -> None:
        # Non-email strings fall through to lowercase-normalised fallback
        assert clean_email("not an email") == "not an email"


class TestCleanEmailExtractionFromContext:
    """Bare-email extraction from surrounding text."""

    def test_extracts_from_sentence(self) -> None:
        assert clean_email("Contact us at support@acme.com for help") == "support@acme.com"

    def test_extracts_first_of_multiple(self) -> None:
        assert clean_email("first@x.com or second@y.com") == "first@x.com"

    def test_handles_leading_angle_bracket_display_name(self) -> None:
        raw = 'From: "O\'Brien, Pat" <pat.obrien@carrier.com>'
        assert clean_email(raw) == "pat.obrien@carrier.com"


class TestCleanEmailLongInputBounding:
    """Long pasted content (email threads, disclaimers) should still extract
    the address even when the input exceeds the bounding window, as long as
    the address is recoverable from the window around the last '@'.
    """

    def test_address_early_in_very_long_paste(self) -> None:
        # Simulate a forwarded email where the address is early but lots of
        # disclaimer text follows.
        disclaimer = "CONFIDENTIAL: " + ("x" * 2000)
        raw = f"From: jane@example.com\n{disclaimer}"
        assert clean_email(raw) == "jane@example.com"

    def test_address_late_in_very_long_paste(self) -> None:
        # Long signature block above the signer's address
        lead = "A" * 2000
        raw = f"{lead}\nSincerely,\nJane Doe <jane@example.com>"
        assert clean_email(raw) == "jane@example.com"


class TestCleanEmailReDoSMitigation:
    """Guards against polynomial-backtracking on pathological input.

    Each case must complete in well under one second — we budget 500ms as a
    safety margin on CI runners while the real measured times are sub-10ms.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "+" * 5000,
            "." * 5000,
            ("+." * 2500),
            (".+" * 2500),
            "<" + ("=" * 5000),
            ("a@b" + "." * 5000 + "c"),
            ("<" + ("<=" * 2000) + ">"),
        ],
    )
    def test_pathological_input_returns_quickly(self, payload: str) -> None:
        start = time.monotonic()
        _ = clean_email(payload)  # result shape isn't what we're asserting
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, f"clean_email took {elapsed*1000:.1f}ms on payload of len {len(payload)}"

    def test_long_input_still_extracts_when_addressable(self) -> None:
        # Confirm ReDoS mitigation didn't break the "I pasted a whole email
        # into the field and there's a real address in there" case. Realistic
        # paste content has word separators around the address.
        noise = "lorem ipsum " * 200  # ~2400 chars, well past 512
        raw = f"{noise} jane@example.com {noise}"
        assert clean_email(raw) == "jane@example.com"
