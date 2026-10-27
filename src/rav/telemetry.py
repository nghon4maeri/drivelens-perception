"""Bounded best-effort timing collector for pilot API/worker (§21.19).

Safe rav_timing JSON logs are queued, batched into PostgreSQL and retained seven days.
Collector errors never change job outcomes; dropped records/flush failures appear in heartbeat.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
from uuid import uuid4

from psycopg.types.json import Jsonb

from .store.pilot_operations import append_events, safe_event


class TimingCollector(logging.Handler):
    def __init__(self, db, component: str):
        super().__init__()
        self.db, self.component = db, component
        self.pending = queue.Queue(maxsize=2048)
        self.dropped = self.failures = 0
        self.stop = threading.Event()
        self.local = threading.local()
        self.thread = None

    def emit(self, record):
        if getattr(self.local, 'writing', False):
            return
        try:
            event = safe_event(json.loads(record.getMessage()), self.component, uuid4())
            if event:
                self.pending.put_nowait(event)
        except (ValueError, TypeError, queue.Full):
            self.dropped += 1

    def flush_batch(self):
        rows = []
        for _ in range(100):
            try:
                rows.append(self.pending.get_nowait())
            except queue.Empty:
                break
        self.local.writing = True
        try:
            with self.db.backend() as cur:
                accepted = append_events(cur, rows) if rows else 0
                if self.component == 'worker':
                    cur.execute("DELETE FROM rav_pilot_timing_event WHERE received_at < now()-interval '7 days'")
                cur.execute('INSERT INTO rav_pilot_heartbeat (component,observed_at,details) VALUES (%s,now(),%s) '
                            'ON CONFLICT (component) DO UPDATE SET observed_at=now(),details=EXCLUDED.details',
                            (self.component, Jsonb({'trace_enabled': True, 'dropped': self.dropped + len(rows)-accepted,
                                                   'flush_failures': self.failures, 'pending': self.pending.qsize()})))
            self.dropped += len(rows)-accepted
        except Exception:
            self.failures += 1
            self.dropped += len(rows)
        finally:
            self.local.writing = False

    def start(self):
        logging.getLogger('rav.timing').addHandler(self)
        def run():
            while not self.stop.is_set():
                self.flush_batch()
                self.stop.wait(10 if self.pending.empty() else 1)
        self.thread = threading.Thread(target=run, name='rav-telemetry', daemon=True)
        self.thread.start()
        return self

    def close(self):
        logging.getLogger('rav.timing').removeHandler(self)
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=12)
        super().close()


def start_collector(db, component: str):
    return TimingCollector(db, component).start() if os.environ.get('RAV_TRACE_ENABLED') == 'true' else None
