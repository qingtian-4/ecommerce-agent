#!/usr/bin/env bash
set -euo pipefail
cd '/home/qingtian/projects/E-commerce Agent'
export TAU2_DATA_DIR=/tmp/acs-external-tau-20261022/data
exec .venv/bin/python scripts/tau_supervise.py \
  --python /tmp/acs-external-tau-env-20261022/bin/python \
  --log results/tau_retail_formal_supplement_attempt2.console.log \
  --status results/tau_retail_formal_supplement_attempt2.process.json -- \
  --resume-from results/tau_retail_formal_20261022/live \
  --out results/tau_retail_formal_supplement_attempt2 \
  --upstream /tmp/acs-external-tau-20261022 \
  --v1-root /tmp/acs-external-v1-20261022 \
  --preflight results/tau_recovery_v21_20261022/acceptance_dry_run \
  --approval-file results/tau_recovery_authorized_20261022/approval.json --live
