import copy

import pytest

from experiments.state_option_counterfactual import (
    BASE_PARAMETERS,
    OPTIONS,
    build_agent,
    option_summary,
    oracle_gate_failures,
    run_jobs,
    split_states,
)


def result(score, margin=0):
    return {'score': score, 'margin': margin}


def labelled(cow, sheep, current=0, *, cow_margin=0, sheep_margin=0,
             opponent='elite.py', seed=1, seat=0):
    return {
        'opponent': opponent, 'seed': seed, 'seat': seat,
        'state_features': {'cash': float(seed)},
        'results': {
            'FOLLOW_CURRENT': result(current),
            'COW_CAPACITY': result(cow, cow_margin),
            'SHEEP_CAPACITY': result(sheep, sheep_margin),
        },
    }


def test_oracle_and_best_fixed_are_computed_over_the_same_states():
    states = [
        labelled(1, 0, cow_margin=20, seed=1),
        labelled(1, 0, cow_margin=20, seed=2),
        labelled(0, 1, sheep_margin=10, seed=3),
        labelled(0, 1, sheep_margin=10, seed=4),
    ]

    summary = option_summary(states)

    assert summary['best_fixed_option'] == 'COW_CAPACITY'
    assert summary['best_fixed_win_rate'] == .5
    assert summary['current_win_rate'] == 0
    assert summary['oracle_win_rate'] == 1
    assert summary['oracle_gap'] == .5
    assert summary['states_an_option_strictly_beats_current'] == 4
    assert summary['oracle_choices'] == {'COW_CAPACITY': 2, 'SHEEP_CAPACITY': 2}
    assert summary['different_unique_oracle_options'] == [
        'COW_CAPACITY', 'SHEEP_CAPACITY']


def test_margin_never_breaks_a_win_rate_tie():
    states = [labelled(1, 1, current=1, cow_margin=-100, sheep_margin=100, seed=1)]

    summary = option_summary(states)

    assert summary['best_fixed_option'] == 'COW_CAPACITY'
    assert summary['best_overall_fixed'] == 'FOLLOW_CURRENT'
    assert summary['oracle_choices'] == {'FOLLOW_CURRENT': 1}
    assert summary['different_unique_oracle_options'] == []


def test_oracle_kill_gate_names_every_failed_primitive():
    healthy = {
        'oracle_gap': .20,
        'states_an_option_strictly_beats_current': 8,
        'different_unique_oracle_options': ['COW_CAPACITY', 'SHEEP_CAPACITY'],
    }
    assert oracle_gate_failures(healthy, .10, 5) == []

    weak = dict(healthy, oracle_gap=.09,
                states_an_option_strictly_beats_current=4,
                different_unique_oracle_options=['COW_CAPACITY'])
    assert oracle_gate_failures(weak, .10, 5) == [
        'oracle_gap', 'adaptive_states', 'state_heterogeneity']

    # Exact gate values pass; no strict inequality silently raises the threshold.
    boundary = dict(healthy, oracle_gap=.10,
                    states_an_option_strictly_beats_current=5)
    assert oracle_gate_failures(boundary, .10, 5) == []


def test_strict_split_keeps_both_seats_of_each_world_together():
    states = [labelled(1, 0, opponent=f'opponent-{world % 2}.py', seed=world,
                       seat=seat)
              for world in range(8) for seat in (0, 1)]

    train, holdout = split_states(states, .30)
    train_worlds = {(state['opponent'], state['seed']) for state in train}
    holdout_worlds = {(state['opponent'], state['seed']) for state in holdout}

    assert train and holdout
    assert train_worlds.isdisjoint(holdout_worlds)
    for partition in (train, holdout):
        seats = {}
        for state in partition:
            seats.setdefault((state['opponent'], state['seed']), set()).add(state['seat'])
        assert all(value == {0, 1} for value in seats.values())
    assert split_states(states, .30) == (train, holdout)


def test_strict_split_groups_two_tapes_from_the_same_recorded_episode():
    paired = [
        {**labelled(1, 0, opponent='seat-zero.py', seed=11, seat=0), 'episode': 99},
        {**labelled(0, 1, opponent='seat-one.py', seed=11, seat=1), 'episode': 99},
    ]
    states = [*paired,
              {**labelled(1, 0, opponent='other.py', seed=12), 'episode': 100},
              {**labelled(0, 1, opponent='v006.py', seed=13), 'episode': None}]

    train, holdout = split_states(states, .5)

    partitions = [{id(state) for state in train}, {id(state) for state in holdout}]
    assert any(all(id(state) in partition for state in paired) for partition in partitions)


def test_strict_split_refuses_an_impossible_or_invalid_holdout():
    with pytest.raises(ValueError, match='two distinct worlds'):
        split_states([labelled(1, 0, seed=1, seat=0),
                      labelled(0, 1, seed=1, seat=1)])
    with pytest.raises(ValueError, match='between zero and one'):
        split_states([labelled(1, 0, seed=1), labelled(0, 1, seed=2)], 1)


def test_generated_option_wrapper_commits_for_the_declared_window(tmp_path, monkeypatch):
    calls = []

    def fake_policy(observation, configuration, parameters):
        calls.append(copy.deepcopy(parameters))
        return {'farmer': ['PASS'], 'hands': [], 'market': []}

    monkeypatch.setattr('agent.planner.policy', fake_policy)
    path = tmp_path / 'cow.py'
    relative = build_agent(path, 'COW_CAPACITY', switch=120, commitment_steps=48)
    namespace = {}
    exec(path.read_text(encoding='utf-8'), namespace)
    policy = namespace['agent']

    for step in (119, 120, 143, 167, 168):
        policy({'step': step, 'day': step // 24, 'hour': step % 24},
               {'turnsPerDay': 24})

    assert calls[0] == BASE_PARAMETERS
    for parameters in calls[1:4]:
        assert parameters == {**BASE_PARAMETERS, **OPTIONS['COW_CAPACITY']}
    assert calls[4] == BASE_PARAMETERS
    assert relative.endswith('cow.py')


def test_run_jobs_restores_plan_order_from_unordered_results():
    jobs = [
        {'candidate': 'candidate.py', 'opponent': 'a.py', 'seed': 1, 'seat': 0},
        {'candidate': 'candidate.py', 'opponent': 'b.py', 'seed': 2, 'seat': 1},
    ]
    rows = [{**job, 'failures': [], 'opponent_failures': [], 'marker': index}
            for index, job in enumerate(jobs)]

    def reverse_runner(_jobs, _workers):
        return iter(reversed(rows))

    ordered = run_jobs(jobs, reverse_runner, workers=2)
    assert [row['marker'] for row in ordered] == [0, 1]


def test_run_jobs_refuses_duplicate_or_substituted_identity():
    job = {'candidate': 'candidate.py', 'opponent': 'a.py', 'seed': 1, 'seat': 0}
    row = {**job, 'failures': [], 'opponent_failures': []}

    with pytest.raises(ValueError, match='duplicate'):
        run_jobs([job, dict(job)],
                 lambda _jobs, _workers: iter([row, dict(row)]), 1)

    substituted = {**row, 'seed': 999}
    with pytest.raises(OSError, match='substituted'):
        run_jobs([job], lambda _jobs, _workers: iter([substituted]), 1)
