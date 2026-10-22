"""Durable local state for a single sequential τ run; not a business replay queue."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    """Publish complete JSON, fsync file and directory; SIGKILL leaves the old or new value."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def run_lock(path):
    """The kernel releases flock on SIGKILL; never unlink a lock inode while in use."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another run owns this experiment lock') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def process_token(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        if stat[0] == 'Z':
            return None
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()+':'+stat[19]
    except (OSError, IndexError):
        return None


class RunMonitor:
    def __init__(self, out, snapshot):
        self.out, self.snapshot = Path(out), snapshot
        self.done = Event()
        self.started = datetime.now(UTC).isoformat()
        self.token = process_token(os.getpid())
        self.signal = None
        self.thread = None
        self.previous = {}

    def write(self, state):
        atomic_json(self.out/'run_status.json', {
            'pid':os.getpid(), 'process_token':self.token, 'started_utc':self.started,
            'heartbeat_utc':datetime.now(UTC).isoformat(), 'state':state,
            'signal':self.signal, **self.snapshot(),
        })

    def __enter__(self):
        self.write('running')
        def beat():
            while not self.done.wait(2):
                try:
                    self.write('running')
                except OSError:
                    # Final checkpoint will surface storage failure; heartbeat is diagnostic.
                    break
        def stop(signum, frame):
            self.signal = signal.Signals(signum).name
            raise KeyboardInterrupt(self.signal)
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.previous[sig] = signal.signal(sig, stop)
        self.thread = Thread(target=beat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.done.set()
        self.thread.join()
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)
        state = 'interrupted' if kind or self.signal else 'exited'
        self.write(state)
        atomic_json(self.out/'exit.json', {
            'state':state, 'signal':self.signal, 'exception':kind.__name__ if kind else None,
            'exited_utc':datetime.now(UTC).isoformat(), **self.snapshot(),
        })


def budget_state(ledger):
    pending = (Decimal(ledger.calls[-1]['reserved_cny'])
               if ledger.calls and 'charge_guard_cny' not in ledger.calls[-1] else Decimal(0))
    return {'spent_guard_cny':str(ledger.spent), 'pending_reserved_cny':str(pending),
            'guard_including_pending_cny':str(ledger.spent+pending),
            'remaining_cny':str(ledger.limit-ledger.spent-pending)}


class RequestJournal:
    """Write one request at a time, not the growing parent ledger at every checkpoint."""
    def __init__(self, out, prefix):
        self.out, self.prefix = Path(out), prefix
        self.last = None

    def save(self, ledger):
        if len(ledger.calls) > self.prefix:
            last = ledger.calls[-1]
            serialized = json.dumps(last, sort_keys=True, ensure_ascii=False)
            if serialized != self.last:
                atomic_json(self.out/'request_records'/f"{last['sequence']:08d}.json", last)
                self.last = serialized
        # Records are authoritative after a crash, not this cached balance.
        atomic_json(self.out/'ledger_state.json', {
            **budget_state(ledger), 'halted':ledger.halted,
            'last_sequence':len(ledger.calls), 'inherited_requests':self.prefix,
        })
