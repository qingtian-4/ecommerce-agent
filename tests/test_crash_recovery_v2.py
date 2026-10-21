"""Actually terminate a worker before/after SQLite commits; never replay the operation."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acs.sessions import SessionStore

CASES = json.loads((Path(__file__).parents[1] / "data/eval/crash_v2.json").read_text())
WORKER = r"""
import os,sqlite3,sys
from pathlib import Path
from pydantic import SecretStr
from acs.config import Settings
from acs.sessions import SessionStore
from acs.returns import ReturnService
from acs.database import BusinessDB
mode,db,sid,docs=sys.argv[1:]
s=Settings(_env_file=None,db_path=Path(db),docs_dir=Path(docs),admin_username='crash-test',
           admin_password=SecretStr('public-test-password'))
store=SessionStore(s.db_path);svc=ReturnService(s,store)
tid=store.begin_turn(sid,'USER001','我想退O10024，测试崩溃边界')
if mode=='received':os._exit(91)
if mode in {'model_decision','stream'}:
 from acs.service_agent import CustomerServiceAgent
 from acs.cs_tools import ToolRegistry
 from tests.conftest import ScriptedLLM,ScriptedResponse,ScriptedToolCall
 class Partial(ScriptedLLM):
  def chat_stream(self,*args,**kwargs):
   yield '部分流式文字'
   yield self.chat(*args,**kwargs)
 model=(Partial([ScriptedResponse(text='部分流式文字')]) if mode=='stream' else
        ScriptedLLM([ScriptedResponse(wants_tools=True,tool_calls=[
            ScriptedToolCall('read','get_order','{"order_id":"O10024"}')])]))
 agent=CustomerServiceAgent(settings=s,llm=model,registry=ToolRegistry(s))
 for update in agent.chat_stream('测试中断'):
  if (mode=='stream' and update.answer) or any(e.type=='tool_call' for e in update.trace):
   os._exit(91)
 raise AssertionError('model boundary not exercised')
BusinessDB(s).get_order('O10024','USER001')
if mode=='read':os._exit(91)
store.update_return_task(sid,'USER001',tid,{'order_id':'O10024','item_id':None})
if mode=='draft':os._exit(91)
with store._connect() as conn:
 item=conn.execute("SELECT id FROM order_items WHERE order_id='O10024' LIMIT 1").fetchone()[0]
store.update_return_task(sid,'USER001',tid,{'item_id':item,'quantity':1,'reason':'no_reason',
    'condition':'sealed','secondary_sale_ok':True})
preview=svc.preview(sid,'USER001',tid)
assert preview['ready'],preview
if mode=='preview':os._exit(91)
class CrashCommit(sqlite3.Connection):
 def __exit__(self,*args):
  if mode.endswith('_before'):os._exit(91)
  value=super().__exit__(*args)
  os._exit(91)
  return value
original=store._connect
def crash_connection():
 c=sqlite3.connect(db,factory=CrashCommit)
 c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
if mode.startswith('submit_'):store._connect=crash_connection
request=svc.confirm_and_submit(sid,'USER001',preview['preview_id'],tid)
if mode.startswith('review_'):
 store._connect=crash_connection
 svc.review(request['request_id'],'approved','测试审核回执',reviewer='crash-test')
raise AssertionError('crash injection did not run')
"""


@pytest.mark.parametrize("mode", CASES)
def test_process_death_reconciles_authoritative_state_without_replay(settings, mode):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    child = subprocess.run(
        [sys.executable, "-c", WORKER, mode, str(settings.db_path), sid, str(settings.docs_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd()) + os.pathsep + str(Path("src").resolve())},
        timeout=20,
    )
    assert child.returncode == 91, child.stderr
    restarted = SessionStore(settings.db_path)
    assert restarted.recover_interrupted() == 1
    assert restarted.recover_interrupted() == 0
    state = restarted.load(sid, "USER001")
    assert state["last_result"]["stop_reason"] == "interrupted"
    assert not state["running"]
    receipt = state["last_result"]["totals"]["recovery"]
    assert receipt["replayed"] is False
    with restarted._connect() as conn:
        requests = conn.execute(
            "SELECT request_id,status FROM return_requests WHERE session_id=?", (sid,)
        ).fetchall()
        reviews = conn.execute(
            "SELECT * FROM return_reviews WHERE request_id IN "
            "(SELECT request_id FROM return_requests WHERE session_id=?)",
            (sid,),
        ).fetchall()
    committed = mode in {"submit_after", "review_before", "review_after"}
    assert len(requests) == int(committed)
    assert len(reviews) == int(mode == "review_after")
    if committed:
        status = "approved" if mode == "review_after" else "pending_review"
        assert requests[0]["status"] == receipt["request"]["status"] == status
        assert requests[0]["request_id"] in state["history"][-1]["content"]
    else:
        assert receipt["request"] is None
    if mode in {"draft", "preview", "submit_before"}:
        assert restarted.get_return_task(sid, "USER001")["task"]["order_id"] == "O10024"
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]
    with pytest.raises(PermissionError):
        restarted.load(sid, "USER002")


def test_reconciliation_can_be_disabled_without_changing_business_data(settings):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退O10024")
    before = store.update_return_task(sid, "USER001", tid, {"order_id": "O10024"})
    assert store.recover_interrupted(reconcile=False) == 1
    assert store.get_return_task(sid, "USER001")["task"] == before["task"]
    assert "totals" not in store.load(sid, "USER001")["last_result"]
