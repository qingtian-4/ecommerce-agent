# Seed business clock regression

2026-10-22. Scope: isolate the merged-main CI date failure; no model/network calls.

`db_seed.build` previously used the host's `date.today()`, whereas live queries use
`database.business_today`, the Asia/Shanghai date or explicit reference date. At
16:30 UTC the seed could be one calendar day behind the query. Tests freeze UTC
2026-10-21 15:30/16:30 independently of host timezone, and cover both default dates
and an explicit 2026-01-05 reference date.

- `red.log`: 1 failed, 3 passed on old implementation; UTC 16:30 seed created_at
  was 2026-10-17 rather than 2026-10-18.
- `green.log`: all 18 tests pass (4 new clock cases, 14 existing database cases),
  exit code 0. Both dates and delivered_days_ago=2 are asserted.
- `ruff.log`: targeted Ruff passed. `git diff --check` passed.
- `initial_test_import_error.log`: initial test referenced a nonexistent module
  timedelta; corrected before the actual red test. Preserved, not defect evidence.
- `selection_error.log`: first green command selected a nonexistent test file;
  corrected to tests/test_seed_clock.py and tests/test_database.py.

The implementation reuses the existing clock with
`Settings(_env_file=None, reference_date=reference_date)`. No new clock abstraction,
policy change, frozen-data rewrite, credentials read, or assertion weakening.
This worktree has not been committed or pushed; main-worktree files were untouched.

Final local regression: 699 passed, 1 skipped, 4 warnings in 673.92s.
See full_pytest.log. This also reruns the two originally failing main-CI checks.
The final repair is prepared as its own reviewable commit/PR; V2 remains separate.
