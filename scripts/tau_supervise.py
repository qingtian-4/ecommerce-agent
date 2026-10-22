"""Run the τ continuation under a user-owned terminal and persist its actual exit code."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.tau_resume import build_plan, validate_destination  # noqa: E402
from scripts.tau_run_state import atomic_json  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--status',type=Path,required=True)
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    forwarded=[a for a in args.arguments if a!='--']
    if '--resume-from' not in forwarded:
        raise ValueError('--resume-from is required')
    source=forwarded[forwarded.index('--resume-from')+1]
    plan=build_plan(source)
    validate_destination(plan,args.log)
    validate_destination(plan,args.status)
    if args.status.exists():
        raise ValueError('Refusing to overwrite supervisor evidence')
    args.log.parent.mkdir(parents=True,exist_ok=True)
    command=[args.python,str(Path(__file__).with_name('tau_resume.py')),
             *[a for a in args.arguments if a!='--']]
    started=datetime.now(UTC).isoformat()
    with args.log.open('x') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        def forward(signum,frame):
            if process.poll() is None:
                process.send_signal(signum)
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,forward)
        atomic_json(args.status,{'state':'running','supervisor_pid':os.getpid(),
                                'child_pid':process.pid,'started_utc':started})
        code=process.wait()
    atomic_json(args.status,{'state':'exited','child_pid':process.pid,'exit_code':code,
                            'started_utc':started,'exited_utc':datetime.now(UTC).isoformat()})
    return code if code>=0 else 128-code


if __name__=='__main__':
    raise SystemExit(main())
