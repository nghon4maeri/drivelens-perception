"""Public size regressions: pool score arrays must never reach browser DTOs."""
import json

from src.rav.proposal_response import interactive_response, proposal_response


def test_large_pool_payload_is_bounded_and_preserves_internal_snapshot():
    count = 100_000
    ids = [f'unit-{i}' for i in range(count)]
    scores = {'unit_id': ids, 'combined': [0.5] * count, 'novelty': [0.2] * count}
    record = {'input': {'recipe': 'diversity', 'execution': {'candidate_ids': ids, 'source_hashes': dict.fromkeys(ids, 'digest')}},
              'picks': [{'unit_id': ids[i]} for i in (99_999, 4)], 'scores': scores,
              'evidence': {'scores': scores, 'interactive_basis': {'all_ids': ids},
                           'units': {ids[4]: [{'label': 'real evidence'}]}},
              'trace': [{'step': 'candidates', 'count': count, 'candidate_ids': ids}]}
    result = {'proposal': record, 'checkpoint': {'state': {'kept_ids': [ids[4]]}, 'proposal': record}}
    public = interactive_response(result)
    assert len(json.dumps(public)) < 2000
    assert public['proposal']['scores']['unit_id'] == [ids[99_999], ids[4]]
    assert public['checkpoint']['proposal']['scores'] is None
    assert public['proposal']['input']['execution'] == {'candidate_count': count, 'source_count': count}
    assert public['proposal']['trace'] == [{'step': 'candidates', 'count': count}]
    assert public['proposal']['evidence'] == {ids[4]: [{'label': 'real evidence'}]}
    assert len(record['scores']['unit_id']) == count
    assert record['input']['execution']['candidate_ids'] is ids
    assert len(record['input']['execution']['source_hashes']) == count
    assert interactive_response(public) == public


def test_historical_per_unit_evidence_and_absent_scores():
    record = {'input': {}, 'picks': [{'unit_id': 'one'}],
              'evidence': {'one': [{'label': 'kept'}], 'other': [{'label': 'exclude'}]}}
    public = proposal_response(record)
    assert public['scores'] is None
    assert public['evidence'] == {'one': [{'label': 'kept'}]}
