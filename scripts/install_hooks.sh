#!/usr/bin/env bash
# Install the repo's git hooks.
#
#   bash scripts/install_hooks.sh
#
# The pre-commit hook runs the secret scanner. This exists because a leaked API
# key in a portfolio repo is a real, irreversible failure mode, and the first
# version of the guard already produced a false positive that would have taught
# the author to bypass it.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="$repo_root/.git/hooks"
mkdir -p "$hooks_dir"

cat > "$hooks_dir/pre-commit" <<'HOOK'
#!/usr/bin/env bash
# Managed by scripts/install_hooks.sh - do not edit by hand.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
cd "$root"

if command -v uv >/dev/null 2>&1; then
  runner=(uv run --quiet python)
else
  runner=(python3)
fi

"${runner[@]}" scripts/check_secrets.py
HOOK

chmod +x "$hooks_dir/pre-commit"
echo "installed: $hooks_dir/pre-commit"
