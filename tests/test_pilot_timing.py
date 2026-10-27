"""Latency logs must preserve failures, isolate correlation and omit payload secrets."""
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.rav.timing import correlation, emit, log, timing


def test_safe_failed_span_and_concurrent_context():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log.addHandler(handler)
    try:
        with correlation(upload_id="u1", token="SECRET"):
            with pytest.raises(RuntimeError), timing("test.failure", url="SIGNED_SECRET"):
                raise RuntimeError("SECRET SQL password")
        def work(i):
            with correlation(request_id=str(i)):
                emit("test.parallel", sql="SECRET")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(work, range(12)))
        emit("test.outside")
    finally:
        log.removeHandler(handler)
    raw = stream.getvalue()
    assert "SECRET" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["upload_id"] == "u1"
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["outcome"] == "error" and rows[0]["wall_ms"] >= 0
    assert {r["request_id"] for r in rows[1:-1]} == {str(i) for i in range(12)}
    assert all("upload_id" not in r for r in rows[1:])
    assert "request_id" not in rows[-1]
