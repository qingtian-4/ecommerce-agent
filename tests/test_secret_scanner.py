"""Tests for the secret scanner.

The scanner exists because of a specific near-miss: the one-line version of this
check flagged the documented placeholder in `.env.example`, and would have
taught the author to bypass it. Writing these tests then surfaced three more
bugs, all pinned below:

1. Substring placeholder matching exonerated a real key that merely *contained*
   a placeholder word.
2. AWS's documented example key was reported as a real secret.
3. A token list containing ``"key"`` made every credential whose material
   contains that word look like a placeholder.

This file necessarily contains secret-shaped strings. Lines that trip the
scanner carry an explicit ``check_secrets:allow`` comment. The scanner caps how
many such markers it tolerates, so this cannot silently become "the scanner is
off here" -- and unlike a path allowlist, the exemptions stay visible in review.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"
_spec = importlib.util.spec_from_file_location("check_secrets", _SCRIPT)
assert _spec and _spec.loader
check_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_secrets)

# The `AIza` pattern requires exactly 35 characters after the prefix.
_GOOGLE_FILLER = "SyB3kL9mN2pQ7rT4vW8xY1zA5cD6eF0gH2"  # 34 chars
_GOOGLE_BODY = _GOOGLE_FILLER[:32] + "key"  # 35 chars, ends with "key"
_GOOGLE_KEY = "AIza" + _GOOGLE_BODY
# AKIA + 16 uppercase characters.
_AWS_KEY = "AKIA3XQZ7PLMN2VK8RTB"  # check_secrets:allow


def _hits(line: str) -> list[str]:
    """Labels whose pattern matches `line` as a real (non-placeholder) secret."""
    return [
        name
        for name, pattern in check_secrets.PATTERNS
        for match in pattern.finditer(line)
        if not check_secrets._looks_like_placeholder(match.group(0))
    ]


# ------------------------------------------------------------------ #
# Bug 1: placeholders must not be reported as secrets
# ------------------------------------------------------------------ #


def test_env_example_placeholder_is_not_flagged() -> None:
    """The exact string in `.env.example` must not block a commit."""
    line = "ACS_LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    assert _hits(line) == []


@pytest.mark.parametrize(
    "candidate",
    [
        "sk-xxxxxxxxxxxxxxxxxxxx",
        "sk-changeme-00000000000000",
        "sk-placeholder-000000000000",
        "sk-your-key-here-1234567890",
        "sk-dummy-0000000000000000",
        "sk-fake-00000000000000000",
        "sk-todo-00000000000000000",
        "sk-sample-000000000000000",
        "sk-<insert-key>-0000000000",
        # NOTE: "sk-test_key-..." is deliberately absent. Since "key" was removed
        # from TOKEN_MARKERS (bug 3), that string is no longer a placeholder by
        # construction. Blocking a clearly synthetic value is an acceptable false
        # positive; asserting the opposite would enshrine a rule that lets real
        # keys through.
    ],
)
def test_placeholders_are_recognised(candidate: str) -> None:
    assert check_secrets._looks_like_placeholder(candidate), candidate


def test_aws_documented_example_key_is_a_placeholder() -> None:
    """Bug 2: AWS's own example key was reported as a real secret."""
    assert check_secrets._looks_like_placeholder("AKIAIOSFODNN7EXAMPLE")
    assert _hits("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE") == []


# ------------------------------------------------------------------ #
# Bug 1b: a real key that merely CONTAINS a placeholder word is still caught
# ------------------------------------------------------------------ #


def test_real_key_containing_example_substring_is_still_flagged() -> None:
    """Whole-token matching: 'example' inside longer material must not exonerate it."""
    line = "key = sk-live-9f2c8a1b4d7e6f3aexample0b5c8d2e"  # check_secrets:allow
    assert "openai/deepseek sk-" in _hits(line)


def test_realistic_key_is_not_a_placeholder() -> None:
    assert not check_secrets._looks_like_placeholder(
        "sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"  # check_secrets:allow
    )


# ------------------------------------------------------------------ #
# Genuine secrets must be caught
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "line,label",
    [
        ("key = sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6", "openai/deepseek sk-"),  # check_secrets:allow
        ("AWS_ACCESS_KEY_ID=" + _AWS_KEY, "aws access key"),
        ("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github token"),  # check_secrets:allow
        ("g = AIza" + _GOOGLE_FILLER + "j", "google api key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key block"),  # check_secrets:allow
        ("slack = xoxb-1234567890-abcdefghijkl", "slack token"),  # check_secrets:allow
    ],
)
def test_real_secret_is_detected(line: str, label: str) -> None:
    assert label in _hits(line), f"{label} not detected in: {line}"


def test_test_key_lengths_are_what_the_patterns_require() -> None:
    """Guard the arithmetic so these cases cannot drift out of matching range."""
    assert len(_GOOGLE_FILLER + "j") == 35  # AIza pattern: exactly 35 after prefix
    assert len(_GOOGLE_BODY) == 35  # bug-3 case: 35 chars ending in "key"
    assert _GOOGLE_BODY.endswith("key")
    assert len(_GOOGLE_KEY) == 39
    assert len(_AWS_KEY) == 20  # AKIA + 16 uppercase


def test_private_key_header_is_never_a_placeholder() -> None:
    """Regression: a broad marker must not swallow PEM headers."""
    headers = [
        "-----BEGIN RSA PRIVATE KEY-----",  # check_secrets:allow
        "-----BEGIN PRIVATE KEY-----",  # check_secrets:allow
        "-----BEGIN OPENSSH PRIVATE KEY-----",  # check_secrets:allow
    ]
    for header in headers:
        assert not check_secrets._looks_like_placeholder(header), header
        assert "private key block" in _hits(header), header


def test_generic_words_are_not_treated_as_placeholder_markers() -> None:
    """Bug 3: "key" in TOKEN_MARKERS silently exonerated real credentials.

    The original attempt at this test used an ``AIza`` key whose material ended in
    ``key``. That could not work, and the reason is worth recording: ``_tokens``
    splits on non-letters, so ``...gH2gkey`` tokenises to ``gkey`` and never
    yields ``key``. An adjacent letter always merges into the token, so a real
    39-character Google key can never produce a bare ``key`` token. The bug was
    real, but it could only fire on material where ``key`` is separated from
    other letters by a NON-LETTER character -- so that is what this test pins.
    """
    assert "key" not in check_secrets.TOKEN_MARKERS
    assert not check_secrets._looks_like_placeholder("Zx9Qw8Er7Ty6Up5As4Df3Gh2Jk1Lm")
    candidate = "sk-live-9f2c_key_material_8d1e"  # check_secrets:allow
    assert "key" in check_secrets._tokens(candidate)
    assert not check_secrets._looks_like_placeholder(candidate)


def test_placeholder_marker_sets_stay_small_and_explicit() -> None:
    """Guard against a future broad word silently re-entering the marker set.

    Every entry here is a word that could plausibly appear inside real key
    material. Adding one of them is a decision, not an accident.
    """
    forbidden = {"key", "secret", "token", "api", "live", "prod", "value", "string"}
    assert not (check_secrets.TOKEN_MARKERS & forbidden)


# ------------------------------------------------------------------ #
# Allowlist and exemption mechanics
# ------------------------------------------------------------------ #


def test_env_example_path_is_allowlisted() -> None:
    assert ".env.example" in check_secrets.ALLOWLISTED_PATHS


def test_scanner_allowlists_itself() -> None:
    """This file defines the patterns; it must not flag itself."""
    assert "scripts/check_secrets.py" in check_secrets.ALLOWLISTED_PATHS


def test_scan_lines_skips_allowlisted_paths() -> None:
    lines = [(".env.example", 1, "ACS_LLM_API_KEY=sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6")]  # check_secrets:allow
    assert check_secrets.scan_lines(lines) == []


def test_scan_lines_reports_path_and_line_number() -> None:
    lines = [("src/x.py", 42, "API_KEY = 'sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6'")]  # check_secrets:allow
    findings = check_secrets.scan_lines(lines)
    assert len(findings) == 1
    assert findings[0].startswith("src/x.py:42:")


def test_ignore_marker_suppresses_a_finding() -> None:
    """A marked line is exempt; the same line unmarked is not."""
    secret = "API_KEY = 'sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6'"  # check_secrets:allow
    marked = [( "src/x.py", 1, secret + "  # " + check_secrets.IGNORE_MARKER)]
    unmarked = [("src/x.py", 1, secret)]
    assert check_secrets.scan_lines(marked) == []
    assert len(check_secrets.scan_lines(unmarked)) == 1


def test_count_ignored_counts_marked_lines() -> None:
    lines = [
        ("a.py", 1, "x  # " + check_secrets.IGNORE_MARKER),
        ("a.py", 2, "y"),
        ("b.py", 3, "z  # " + check_secrets.IGNORE_MARKER),
    ]
    assert check_secrets.count_ignored(lines) == 2


def test_ignore_cap_is_enforced_by_the_configuration() -> None:
    """The exemption mechanism must stay bounded."""
    assert check_secrets.MAX_IGNORED_LINES > 0
    assert check_secrets.MAX_IGNORED_LINES <= 200
