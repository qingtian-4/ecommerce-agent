#!/usr/bin/env bash
set -uo pipefail
cd /tmp/acs-runtime-v21-integration-20261022
'/home/qingtian/projects/E-commerce Agent/.venv/bin/python' scripts/eval_v21_pairs.py \
 --cases results/v21_mixed_answers_20261022/proposed_live_cases_v2.json \
 --snapshot results/v21_mixed_answers_20261022/business.sql \
 --v2-root /tmp/acs-external-final-check-20261022 \
 --v21-root /tmp/ecommerce-runtime-v21-20261022 \
 --out results/v21_integration_20261022/paired_live \
 --preflight results/v21_integration_20261022/paired_driver_dry \
 --env-file '/home/qingtian/projects/E-commerce Agent/.env' --live \
 > results/v21_integration_20261022/paired_live.console.log 2>&1
run_code=$?
'/home/qingtian/projects/E-commerce Agent/.venv/bin/python' - "$run_code" <<'PY'
import json,sys
from pathlib import Path
from datetime import UTC,datetime
Path('results/v21_integration_20261022/paired_live.exit.json').write_text(json.dumps({'exit_code':int(sys.argv[1]),'exited_utc':datetime.now(UTC).isoformat()},indent=2)+'\n')
PY
exit "$run_code"
