"""No-network checks for the separate, globally metered external pilot."""
from decimal import Decimal

import pytest
from scripts.eval_tau_v2 import TaggedLedger, settings_for

from acs.config import Settings
from acs.llm import Usage
from acs.runtime import BudgetExceeded


def test_all_provider_roles_share_one_conservative_budget(tmp_path):
    ledger = TaggedLedger(tmp_path / 'requests.json')
    ledger.limit = Decimal('.04')
    for role in ['agent', 'user', 'judge']:
        ledger.arm, ledger.task, ledger.role = 'official', '17', role
        record = ledger.begin({'messages': [], 'max_tokens': 1})
        assert record['role'] == role
        ledger.complete(record, Usage('model', role, prompt_tokens=1000,
                                     completion_tokens=0, total_tokens=1000))
    assert ledger.spent == Decimal('.012')
    ledger.limit = ledger.spent
    with pytest.raises(BudgetExceeded):
        ledger.begin({'messages': [], 'max_tokens': 1})
    assert len(ledger.calls) == 3


def test_missing_usage_retains_reserve_and_prevents_another_paid_attempt(tmp_path):
    ledger = TaggedLedger(tmp_path / 'requests.json')
    record = ledger.begin({'messages': [], 'max_tokens': 768})
    ledger.complete(record, None)
    assert ledger.spent == Decimal(record['reserved_cny'])
    with pytest.raises(BudgetExceeded):
        ledger.begin({'messages': [], 'max_tokens': 768})
    assert len(ledger.calls) == 1


def test_v2_only_activates_runtime_mechanisms_in_official_environment():
    original = Settings(_env_file=None)
    baseline, candidate = [settings_for(original, arm) for arm in ['acs_v1', 'acs_v2']]
    changed = {k for k in baseline.model_dump()
               if baseline.model_dump()[k] != candidate.model_dump()[k]}
    assert changed == {'workflow_router_enabled', 'runtime_recovery_enabled'}
    assert candidate.workflow_router_enabled and candidate.runtime_recovery_enabled
    assert not any([candidate.return_state_check_enabled, candidate.order_candidate_render_enabled,
                    candidate.order_constraint_enabled, candidate.order_aggregation_enabled,
                    candidate.execution_progress_enabled])
    assert not original.workflow_router_enabled and not original.runtime_recovery_enabled
