"""Bound public proposal payloads to selected picks; preserve internal snapshots for replay.

Pure lightweight formatting shared by API/worker/local commands. Never mutates its input.
"""
from __future__ import annotations


def batch_scores(scores: dict | None, picks: list[dict]) -> dict | None:
    """Select aligned score rows in pick order using O(batch) auxiliary memory."""
    if scores is None:
        return None
    ids = scores.get('unit_id', [])
    wanted = {p['unit_id'] for p in picks}
    positions = {}
    for i, uid in enumerate(ids):
        if uid in wanted and uid not in positions:
            positions[uid] = i
    rows = [positions[p['unit_id']] for p in picks if p['unit_id'] in positions]
    return {key: [column[i] for i in rows] for key, column in scores.items()
            if isinstance(column, list) and len(column) == len(ids)}


def proposal_response(record: dict, *, include_scores: bool = True) -> dict:
    """Project live or persisted DTOs, including historical evidence envelopes."""
    out = dict(record)
    picks = record['picks']
    ev = record.get('evidence') or {}
    units = ev.get('units', {}) if 'units' in ev else ev
    if not isinstance(units, dict):
        units = {}
    out['evidence'] = {p['unit_id']: units[p['unit_id']] for p in picks
                       if isinstance(units.get(p['unit_id']), list)}
    scores = record.get('scores', ev.get('scores'))
    out['scores'] = batch_scores(scores, picks) if include_scores else None
    for key in ('explain', 'uncertainty_summary'):
        if key not in out and key in ev:
            out[key] = ev[key]
    for key in ('trace', 'strategy_trace'):
        if (trace := record.get(key, ev.get(key))) is not None:
            out[key] = [{k: v for k, v in step.items() if k != 'candidate_ids'} for step in trace]
    inp = dict(record['input'])
    if isinstance(inp.get('execution'), dict):
        execution = dict(inp['execution'])
        if 'candidate_ids' in execution:
            execution['candidate_count'] = len(execution.pop('candidate_ids'))
        if 'source_hashes' in execution:
            execution['source_count'] = len(execution.pop('source_hashes'))
        inp['execution'] = execution
    out['input'] = inp
    return out


def interactive_response(result: dict) -> dict:
    """Keep the current batch and a score-free checkpoint; support rollout with old workers."""
    out = dict(result)
    if result.get('proposal') is not None:
        out['proposal'] = proposal_response(result['proposal'])
    if result.get('checkpoint') is not None:
        checkpoint = dict(result['checkpoint'])
        if checkpoint.get('proposal') is not None:
            checkpoint['proposal'] = proposal_response(checkpoint['proposal'], include_scores=False)
        out['checkpoint'] = checkpoint
    return out
