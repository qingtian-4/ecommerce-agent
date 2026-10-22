"""Calibration against pinned official return models, including five frozen pilot false alarms."""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip('tau2')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tau2.data_model.message import ToolMessage  # noqa: E402
from tau2.domains.retail.environment import get_environment  # noqa: E402
from tau_acs_agent import OfficialRegistry  # noqa: E402

from acs.recovery import classify_failure  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parents[1] /
    'results/tau_retail_formal_20261022/protocol_replay_cases.json').read_text())


@pytest.fixture
def registry():
    result = OfficialRegistry(get_environment().get_tools())
    result.normalize_results = True
    return result


@pytest.mark.parametrize('case', CASES, ids=[c['id'] for c in CASES])
def test_five_actual_legal_results_are_valid_and_preserve_facts(registry, case):
    registry.reply = ToolMessage.model_validate(case['reply'])
    payload, events, error = registry.run(case['call']['name'],
        json.dumps(case['call']['arguments']), current_user_id='external')
    assert error is None
    assert classify_failure(payload=payload)['category'] == 'ok'
    expected = (json.loads(case['reply']['content']) if case['reply']['content'].startswith('{')
                else case['reply']['content'])
    assert payload['result'] == expected
    assert events[1].detail['official_content'] == case['reply']['content']


@pytest.mark.parametrize('name,content,expected', [
    ('get_user_details', '[]', 'invalid_result'),
    ('get_user_details', '{"orders":[]}', 'invalid_result'),
    ('get_order_details', '{broken', 'invalid_result'),
    ('find_user_id_by_email', None, 'invalid_result'),
    ('calculate', '3.0', 'ok'),
    ('list_all_product_types', '{}', 'ok'),
])
def test_declared_protocol_rejects_bad_types_and_accepts_legal_strings(
        registry, name, content, expected):
    registry.reply = ToolMessage(role='tool', id='protocol', content=content)
    payload, _, error = registry.run(name, '{}', current_user_id='external')
    assert classify_failure(payload=payload)['category'] == expected
    assert (error is None) == (expected == 'ok')


@pytest.mark.parametrize('tool,content,category', [
    ('find_user_id_by_email', 'Error: User not found', 'not_found'),
    ('cancel_pending_order', 'Error: Invalid reason', 'business_rejected'),
    ('find_user_id_by_email', 'Error: unexpected storage failure', 'tool_failure'),
])
def test_error_flag_is_authoritative_not_success_shape(registry, tool, content, category):
    registry.reply = ToolMessage(role='tool', id='error', content=content, error=True)
    arguments = ({'order_id': '#X', 'reason': 'invalid reason'}
                 if tool == 'cancel_pending_order' else {'email': 'test@example.test'})
    payload, events, error = registry.run(tool, json.dumps(arguments), current_user_id='external')
    assert error and classify_failure(payload=payload)['category'] == category
    assert events[-1].type == 'error'
