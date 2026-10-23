"""Restricted local provider evidence, with public structural summaries only.

Never save request headers, keys, or input messages. Provider output can contain
personal information: raw body/SDK data live only below a mode-0700 directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


class ResponseEvidence:
    def __init__(self, root, *, secrets=()):
        original = Path(root)
        if original.is_symlink():
            raise PermissionError("Evidence directory must not be a symlink")
        self.root = original.resolve()
        self.secrets = tuple(s for s in secrets if s)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or stat.S_IMODE(self.root.stat().st_mode) != 0o700:
            raise PermissionError("Evidence directory must be private (0700)")

    def save(self, request_id, record):
        # IDs are locally generated reservation UUIDs, never provider-supplied paths.
        if not request_id or any(c not in '0123456789abcdef' for c in request_id):
            raise ValueError("Evidence ID must be a local hex request ID")
        path = self.root / (request_id + '.json')
        data = json.dumps(record, ensure_ascii=False).encode()
        for secret in self.secrets:
            data = data.replace(secret.encode(), b"[REDACTED_CREDENTIAL]")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return hashlib.sha256(data).hexdigest()
