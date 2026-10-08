"""Build the SQLite mock database.

    uv run python scripts/init_db.py

The actual seed data lives in `src/acs/db_seed.py` so tests can build an
identical database without shelling out to this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.db_seed import build  # noqa: E402


def main() -> int:
    settings = get_settings()
    counts = build(settings.db_path, reference_date=settings.reference_date)
    print(f"built {settings.db_path}")
    for table, n in counts.items():
        print(f"  {table:<15} {n:>4} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
