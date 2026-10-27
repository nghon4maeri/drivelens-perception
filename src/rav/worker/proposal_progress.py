"""Bounded proposal phase timings for existing progress JSON and safe admin telemetry (§21.40).

Worker-only: phase(name, done, total, cache_hit=None), close on exit. No prompts/keys in output.
"""
import logging
import re
import time
from datetime import UTC, datetime

from ..store.pilot_runtime import PhaseTiming, Progress
from ..timing import emit, timing
from .session_context import check_cancel

log = logging.getLogger(__name__)


class ProposalProgress:
    def __init__(self, runtime, lease, stop):
        self.runtime, self.lease, self.stop = runtime, lease, stop
        self.stages, self.sequence = [], 0
        self.started, self.last_write, self.span = 0., 0., None

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        self._finish((kind, value, tb))
        self.flush()

    def _finish(self, error=(None, None, None)):
        if self.span:
            self.stages[-1].elapsed_ms = round((time.perf_counter() - self.started) * 1000, 3)
            self.stages[-1].state = 'error' if error[0] else 'done'
            self.span.__exit__(*error)
            if self.stages[-1].cache_hit is not None:
                emit('worker.propose.cache', kind=self.stages[-1].phase, cache_hit=self.stages[-1].cache_hit)
            self.span = None

    def phase(self, phase, done=0, total=0, *, cache_hit=None):
        check_cancel(self.stop)
        if not phase.replace('_', '').isalpha() or phase != phase.upper():
            phase = 'INFER_UNCERTAINTY' if phase.startswith('det.') else 'COMPUTE_' + re.sub('[^A-Z_]', '_', phase.upper())[:32]
        changed = not self.stages or phase != self.stages[-1].phase
        if changed:
            self._finish()
            if len(self.stages) >= 24:
                raise ValueError('too many proposal phases')
            self.started = time.perf_counter()
            self.stages.append(PhaseTiming(phase=phase, started_at=datetime.now(UTC).isoformat(),
                elapsed_ms=0, state='running', done=done, total=total, cache_hit=cache_hit))
            self.span = timing('worker.propose.' + phase.lower(), cache_hit=cache_hit)
            self.span.__enter__()
        stage = self.stages[-1]
        stage.done, stage.total = done, total
        if cache_hit is not None:
            stage.cache_hit = cache_hit if stage.cache_hit is None else stage.cache_hit and cache_hit
        stage.elapsed_ms = round((time.perf_counter() - self.started) * 1000, 3)
        if changed or time.monotonic() - self.last_write >= 1:
            self.flush()

    def flush(self):
        if not self.stages:
            return
        self.sequence += 1
        stage = self.stages[-1]
        try:
            self.runtime.update_progress(self.lease, Progress(sequence=self.sequence, phase=stage.phase,
                done=stage.done, total=stage.total, stages=self.stages))
        except Exception as exc:
            log.warning('proposal progress unavailable: %s', type(exc).__name__)
        self.last_write = time.monotonic()
