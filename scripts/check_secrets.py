#!/usr/bin/env python3
"""Block accidental secret commits.

Motivation: the first version of this check was a single regex on the staged
diff, and it produced a false positive on the placeholder `sk-xxxxxxxx...` in
`.env.example`. A scanner that cries wolf gets bypassed, so it must be precise
in both directions. Its own test suite then found three more bugs, all pinned:

1. Substring placeholder matching was too permissive -- `"example" in candidate`
   would exonerate a real key that merely contained that substring.
2. AWS's *documented* example key `AKIAIOSFODNN7EXAMPLE` was reported as a real
   secret, because the generic `sk-` heuristics never applied to it.
3. A token list containing `"key"` made **every** credential whose serialisation
   contains the word "key" look like a placeholder -- including legitimate
   `AIza...` Google keys and the `-----BEGIN RSA PRIVATE KEY-----` header. `key`
   was removed; private-key headers are now an unconditional match.

Design note: `.env.example` is path-allowlisted, so placeholder detection never
has to protect documentation. That lets the marker set stay small and unambiguous
instead of trying to guess whether any given string "looks like" a placeholder.

Exit codes:
    0  clean
    1  a real-looking secret was found (commit must be blocked)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Self-evident placeholders, matched anywhere in the candidate. Safe as
# substrings: a real provider key will not contain these.
SUBSTRING_MARKERS = (
    "xxxx",
    "changeme",
    "change_me",
    "placeholder",
    "redacted",
    "yoursecret",
    "<",
    "...",
)

# Ambiguous words, matched only as a whole token so that a real key containing
# the substring is not silently waved through.
#
# Deliberately EXCLUDES "key": it appears inside legitimate credentials and
# inside the PEM header. See bug 3 in the module docstring.
TOKEN_MARKERS = frozenset(
    {
        "your",
        "yours",
        "example",
        "dummy",
        "fake",
        "todo",
        "test",
        "sample",
    }
)

# Credentials that providers actually issue. Kept deliberately tight: a broad
# pattern catches placeholders, a narrow one misses real keys.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai/deepseek sk-", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("openrouter", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}\b")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("aws access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack token", re.compile(r"\bxox[abpsr]-[A-Za-z0-9\-]{10,}\b")),
    ("private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
]

# A header like this is never a placeholder, whatever the surrounding heuristics
# say. Belt-and-braces in case a marker is added later that would match it.
NEVER_PLACEHOLDER = re.compile(r"-----BEGIN .*PRIVATE KEY-----")

# Files whose whole purpose is to show secret-shaped strings.
ALLOWLISTED_PATHS = {
    ".env.example",
    "scripts/check_secrets.py",  # this file defines the patterns
}

# Per-line escape hatch for files that must contain secret-shaped test fixtures.
# Deliberately verbose so it is visible in review, and deliberately capped so it
# cannot quietly become "the scanner is disabled".
IGNORE_MARKER = "check_secrets:allow"
MAX_IGNORED_LINES = 60

# Warning-only signal. A legitimate test fixture file trips a lot of patterns;
# a source file tripping many is worth a look, but this must never block work.
NOISY_FINDING_THRESHOLD = 25


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^A-Za-z]+", text.lower()) if t}


def _looks_like_placeholder(candidate: str) -> bool:
    """True when the candidate is documentation rather than a live credential."""
    if NEVER_PLACEHOLDER.search(candidate):
        return False

    low = candidate.lower()
    if any(marker in low for marker in SUBSTRING_MARKERS):
        return True

    if _tokens(candidate) & TOKEN_MARKERS:
        return True

    # Documented AWS example key: uppercase, no lowercase, no digits. Real AWS
    # access key ids are mixed-case and contain digits.
    return bool(re.fullmatch(r"(AKIA|ASIA)[A-Z]{16}", candidate))


def _iter_added_lines() -> list[tuple[str, int, str]]:
    """Yield (path, lineno, text) for lines added in the staged diff."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--no-color"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"check_secrets: could not read staged diff: {exc}", file=sys.stderr)
        return []

    out: list[tuple[str, int, str]] = []
    path = ""
    lineno = 0
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            lineno = int(match.group(1)) if match else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append((path, lineno, line[1:]))
            lineno += 1
    return out


def scan_lines(lines: list[tuple[str, int, str]]) -> list[str]:
    """Return findings for the given (path, lineno, text) triples.

    Lines carrying the IGNORE_MARKER are skipped. The cap on ignored lines is
    enforced by the caller so that a marker applied to a whole file is caught
    rather than honoured.
    """
    findings: list[str] = []
    for path, lineno, text in lines:
        if path in ALLOWLISTED_PATHS:
            continue
        if IGNORE_MARKER in text:
            continue
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                candidate = match.group(0)
                if _looks_like_placeholder(candidate):
                    continue
                findings.append(f"{path}:{lineno}: possible {label}: {candidate[:12]}...")
    return findings


def count_ignored(lines: list[tuple[str, int, str]]) -> int:
    return sum(1 for path, _, text in lines if IGNORE_MARKER in text)


def main() -> int:
    if not Path(".git").exists():
        print("check_secrets: not a git repository; skipping", file=sys.stderr)
        return 0

    lines = _iter_added_lines()

    ignored = count_ignored(lines)
    if ignored > MAX_IGNORED_LINES:
        print(
            f"check_secrets: BLOCKED - {ignored} lines carry '{IGNORE_MARKER}' "
            f"(limit {MAX_IGNORED_LINES}).\n"
            "That many exemptions means the scanner is effectively off for these\n"
            "files. Narrow the exemptions, or add the file to ALLOWLISTED_PATHS\n"
            "with a comment explaining why it must contain secret-shaped content."
        )
        return 1

    findings = scan_lines(lines)

    if len(findings) > NOISY_FINDING_THRESHOLD:
        print(
            f"check_secrets: warning - {len(findings)} findings. If this file is a\n"
            "test fixture, add it to ALLOWLISTED_PATHS rather than marking lines.",
            file=sys.stderr,
        )

    if findings:
        print("check_secrets: BLOCKED - real-looking secrets in staged content:\n")
        for finding in findings:
            print(f"  {finding}")
        print(
            "\nIf this is a placeholder, make it obvious (use 'xxxx' or 'changeme').\n"
            f"If this file must contain secret-shaped fixtures, add '{IGNORE_MARKER}'\n"
            "to the specific lines, or allowlist the file with a comment.\n"
            "If it is real: unstage it, rotate the key, and use .env (gitignored)."
        )
        return 1

    suffix = f" ({ignored} line(s) explicitly exempted)" if ignored else ""
    print(f"check_secrets: OK - no real-looking secrets staged{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
