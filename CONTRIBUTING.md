# Contributing

This repository is currently a single-maintainer project, so most of this file is
about keeping the history honest rather than coordinating people. It exists
because the repository previously had no written conventions, and an audit of the
first fifteen commits found that none of them followed the commit format now
required — an avoidable problem, since nothing had stated the requirement.

If you are an agent or assistant working in this repository, treat this file as
binding.

---

## 1. Before you start

1. Read `README.md` (current state, baseline numbers, known defects) and
   `docs/EXPERIMENT_LOG.md` (measured findings and regression risks).
2. Check `git status` and the current branch. **Do not overwrite uncommitted
   work.** If the tree is dirty, ask before touching the affected files.
3. The remote repository is `qingtian-4/ecommerce-agent`. Visibility is a live
   repository setting; check it before publication rather than inferring it here. Commit and
   push only when authorized by the user; do not force-push or rewrite shared
   history without an explicit request (see §5).
4. For anything larger than a single small change, state a brief plan and how
   the finished behaviour will be verified. Group commits around complete
   deliverables, not individual implementation steps.

## 2. Environment and verification

```bash
uv sync --extra dev --frozen         # install locked dependencies
uv run pytest                        # offline tests, no model key needed
uv run ruff check .                  # lint, line-length 100 (see pyproject.toml)
uv run python scripts/workbench.py --demo  # synthetic local demonstration
```

Tests are designed to run **without an API key or network**: the agent uses a
scripted fake LLM, retrieval uses a deterministic fake embedder, and the database
is a temporary SQLite file. Keep it that way — a test that needs a live API is a
test that will be skipped.

Delete `__pycache__` before committing if you have been running things:

```bash
find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +
```

## 3. Commits

### Format

Conventional Commits. **Title only; do not write a body.**

```
<type>(<scope>): <description>
```

- Imperative, present tense: `add`, `fix` — not `added`, `fixes`.
- The title says **what** changed. No trailing period.
- Keep the title under ~72 characters.

**The one exception:** a breaking change takes a `!` after the type/scope **and**
a 2–3 line footer explaining how to migrate. Nothing else gets a body. Rationale:
`!` alone tells a reader that something broke but not what to do about it, and
that is the one piece of information a title cannot carry.

```
feat(llm)!: disable thinking mode by default

Set ACS_LLM_THINKING=true to restore the previous behaviour.
```

### Types

| type | use for |
|---|---|
| `feat` | new capability |
| `fix` | bug fix |
| `docs` | documentation only |
| `test` | tests and evaluation sets |
| `refactor` | behaviour-preserving restructuring |
| `perf` | performance |
| `style` | formatting, unused imports |
| `build` | dependencies, interpreter pin, packaging |
| `chore` | housekeeping, generated artefacts |
| `ci` | CI configuration |
| `revert` | reverting a previous commit |

### Scopes

Scopes are optional. Reuse these where they fit, so `git log --grep` stays
useful: `config`, `llm`, `rag`, `db`, `tools`, `agent`, `ui`, `eval`, `scripts`,
`src`, `build`, `docs`.

### Atomicity

1. **One commit, one complete, runnable change.** Keep a feature or fix together
   with its necessary corrections, regression tests, related documentation and
   evaluation evidence. Choose the commit type by its primary purpose; do not
   split related work into separate `feat`, `fix` and `test` commits.
2. Each commit must build, pass the relevant tests and be independently
   revertable. Verify affected application behaviour before committing. Keep
   code, data/index versions and evaluation views consistent; do not commit an
   intermediate state that needs a later patch to become runnable.
3. Prefer one commit for one completed request. Group related small steps and
   commit after verifying the complete result. Split only when the changes are
   independently useful, runnable and revertable; unrelated work still belongs
   in separate commits.
4. If you notice an unrelated problem, report it and fix it in a **separate**
   commit. Do not fix it in passing.
5. Do not mix unrelated mechanical cleanup into behaviour changes. A rename or
   move required by the same change may stay with that change.

**Why this matters here specifically:** an earlier commit in this repository's
history bundled an unrelated documentation change with a source cleanup.
Reverting the documentation would have silently removed the source change too.
Related documentation and tests belong with their implementation; unrelated
cleanup does not. Atomicity is about complete behaviour and reversibility.

### Size

Keep the diff focused and reviewable. Line count is a review signal, not a target
or an automatic reason to split a complete change. Split large changes only at
boundaries that leave each commit runnable; do not separate required tests or
fixes merely to make commits smaller. Generated files, datasets and `results/**`
may be large when they provide the evidence for the same change.

### Message/scope accuracy

The title must describe everything the commit does. A title claiming one change
while the diff contains another is worse than a vague title, because it is
actively misleading — this repository has already produced one such commit
(`docs(config): document the thinking-mode toggles` originally claimed to wire
config it did not touch).

Before committing, run:

```bash
git show --stat HEAD     # does the file list match the title?
```

## 4. What not to commit

- Secrets, API keys, tokens. `.env` is gitignored; `.env.example` holds
  placeholders only. A pre-commit hook runs `scripts/check_secrets.py`; if it
  blocks a fixture that must contain secret-shaped strings, add a
  `check_secrets:allow` marker on that line rather than bypassing the hook.
- Personal data. The mock database is synthetic (`USER001`/`张伟`); keep it that
  way. Real customer data must never enter this repository.
- Large binaries, virtualenvs, `__pycache__`, editor state.
- Generated artefacts whose inputs are committed, **unless** they are the
  evidence for a published number — `results/**` is committed on purpose so the
  README's metrics are reproducible rather than merely asserted.

## 5. History

`main` is the default branch. Preserve the existing commit history when pushing
to GitHub. Rewriting local or shared history is off by default.

If you believe history needs rewriting:

1. Create a backup branch first (`git branch -f backup/<name> HEAD`).
2. State what will change and why, and wait for approval.
3. Prefer `git commit-tree` over interactive rebase when only messages change: it
   preserves author **and** committer timestamps, and never touches the working
   tree or index, so it cannot disturb uncommitted work.
4. Verify afterwards that every commit's **tree and file list** still matches its
   original — "the final tree matches" is not sufficient, since a middle commit
   could have gained or lost a file.

## 6. Reporting

When a task is done, report:

- files changed
- commits created (with their SHAs)
- what was run to verify it (tests, lint, manual steps)
- what is unfinished, uncertain, or a known risk

Report failures and mistakes plainly. If a claim in this file or in an earlier
report turns out to be wrong, correct it in the next report rather than quietly
moving on — two of the reports that produced this file contained exactly that
class of error, and they cost more to untangle than to admit.
