from experiments.elite_route_search import choose_routes, continuations, fit_routes
from tests.test_elite_route_train import actions, tape


def row(opponent, seed, seat, score=0, margin=0, failures=()):
    return {'opponent': opponent, 'seed': seed, 'seat': seat, 'score': score,
            'margin': margin, 'failures': list(failures), 'opponent_failures': []}


def test_continuations_collapses_equal_next_prefixes():
    tapes = [tape('a', actions('PASS', 'NORTH'), 1),
             tape('b', actions('PASS', 'NORTH'), 2),
             tape('c', actions('PASS', 'SOUTH'), 1)]
    assert set(continuations(tapes)) == {1, 2}


def test_choose_routes_uses_score_then_margin_and_global_fallback():
    failure = [{'step': 144, 'message': 'SHOPPAIR:BAKERY|YARN_STORE'}]
    probes = [row('opp', seed, 0, failures=failure) for seed in (1, 2)]
    routes = {
        3: [row('opp', 1, 0, 1, 5), row('opp', 2, 0, 0, 5)],
        7: [row('opp', 1, 0, 1, 9), row('opp', 2, 0, 0, 9)],
    }
    table, fallback, report = choose_routes(probes, routes, [3, 7], minimum_worlds=2)
    assert table['BAKERY|YARN_STORE'] == 7
    assert fallback == 7
    assert report['BAKERY|YARN_STORE']['selection'] == 'cell'


def test_fit_routes_changes_only_the_runtime_default_state():
    routes = {'144': {'2': {'old': 4}, '4': {'old': 2}}, '216': {'2': {'x': 2}}}
    fitted = fit_routes(routes, 2, {'new': 4})
    assert fitted['144']['2'] == {'new': 4}
    assert fitted['144']['4'] == {'old': 2}
    assert fitted['216'] == routes['216']
