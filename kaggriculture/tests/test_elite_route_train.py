import importlib.util

from experiments.elite_route_train import build, select_training_tapes, train


def actions(prefix, tail):
    return [{'farmer': [prefix if turn < 144 else tail], 'hands': [], 'market': []}
            for turn in range(719)]


def tape(identity, stream, money):
    return {'sha256': identity, 'team': 'elite', 'actions': stream, 'money': money}


def test_select_training_tapes_keeps_the_largest_compatible_opening_family():
    common = actions('PASS', 'NORTH')
    library = {'tapes': [tape('a', common, 1), tape('b', common, 2),
                         tape('c', actions('SOUTH', 'EAST'), 100)]}
    assert {row['sha256'] for row in select_training_tapes(library, 'elite')} == {'a', 'b'}


def test_trained_route_switches_only_to_an_exact_prefix_match(tmp_path):
    streams = [actions('PASS', 'NORTH'), actions('PASS', 'SOUTH')]
    tapes = [tape('a', streams[0], 10), tape('b', streams[1], 20)]
    histories = {
        'a': {step: ('BAKERY', 'BAKERY') for step in range(144, 649, 72)},
        'b': {step: ('YARN_STORE', 'YARN_STORE') for step in range(144, 649, 72)},
    }
    routes, _ = train(tapes, histories)
    path = tmp_path / 'main.py'
    build(tapes, routes, path, 'test')
    spec = importlib.util.spec_from_file_location('trained_route_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observation = {'step': 144, 'day': 6, 'hour': 0, 'player': 0,
                   'town': {'unlocked_shops': ['YARN_STORE', 'YARN_STORE']}}
    assert module.agent(observation)['farmer'] == ['SOUTH']


def test_build_accepts_an_explicit_default_route(tmp_path):
    streams = [actions('PASS', 'NORTH'), actions('PASS', 'SOUTH')]
    tapes = [tape('a', streams[0], 100), tape('b', streams[1], 1)]
    path = tmp_path / 'main.py'
    build(tapes, {}, path, 'test', default=1)
    spec = importlib.util.spec_from_file_location('explicit_default_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observation = {'step': 200, 'day': 8, 'hour': 8, 'player': 0,
                   'town': {'unlocked_shops': []}}
    assert module.agent(observation)['farmer'] == ['SOUTH']
