"""Container bootstrap: initialise only an absent database, then run the normal UI."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.db_seed import build  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def main():
    settings = get_settings()
    if not settings.db_path.exists():
        build(settings.db_path, reference_date=settings.reference_date)
    SessionStore(settings.db_path)
    os.execv(sys.executable, [sys.executable, "app.py", *sys.argv[1:]])


if __name__ == "__main__":
    main()
