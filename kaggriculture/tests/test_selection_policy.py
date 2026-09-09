"""Changing coin magnitudes must not change selected experimental candidates."""
import json

from experiments import tape_harvest
from experiments.tape_agent import select
from scripts.issue2_animal_sweep import write_report
from test_tape_harvest import replay, stream


def test_legacy_library_order_and_coin_values_do_not_select_a_tape():
    tapes = [dict(id='rich', sha256='z', score=1, margin=9000),
             dict(id='poor', sha256='a', score=1, margin=1)]
    assert select({'tapes': tapes})['id'] == 'poor'
    tapes[0]['margin'], tapes[1]['margin'] = -500, 100000
    assert select({'tapes': list(reversed(tapes))})['id'] == 'poor'


def test_harvest_neither_filters_nor_ranks_by_margin(tmp_path, monkeypatch):
    margins = [9000, -100]
    def matches(jobs, workers, ordered=True):
        assert ordered, 'harvest pairs jobs with rows positionally'
        for job, margin in zip(jobs, margins):
            actions = stream('NORTH' if job['seat'] == 0 else 'SOUTH')
            from pathlib import Path
            Path(job['replay']).write_text(json.dumps(replay(actions, job['seat'])))
            yield dict(score=1, margin=margin, money=100, opponent_money=1,
                       failures=[], opponent_failures=[])
    monkeypatch.setattr(tape_harvest, 'matches', matches)
    monkeypatch.setattr(tape_harvest, 'agent_hash', lambda name: name)
    first = tape_harvest.harvest(['donor'], ['opponent'], [1000], tmp_path / 'a.json')
    margins.reverse()
    second = tape_harvest.harvest(['donor'], ['opponent'], [1000], tmp_path / 'b.json')
    assert len(first['tapes']) == len(second['tapes']) == 2
    assert [t['sha256'] for t in first['tapes']] == [t['sha256'] for t in second['tapes']]


def test_animal_sweep_report_selection_ignores_margin(tmp_path):
    rows = [dict(animal_type=name, animal_target=3, score_rate=.5, score_ci95=[0, 1],
                 mean_margin_diagnostic=margin, failures=0)
            for name, margin in [('SHEEP', 9000), ('COW', 1)]]
    write_report(tmp_path / 'a', [1000], 1, rows)
    for row in rows:
        row['mean_margin_diagnostic'] *= -100
    write_report(tmp_path / 'b', [1000], 1, list(reversed(rows)))
    for name in ('a', 'b'):
        report = (tmp_path / name / 'report.md').read_text()
        assert '| 1 | COW |' in report
        assert 'Mean margin (diagnostic)' in report
