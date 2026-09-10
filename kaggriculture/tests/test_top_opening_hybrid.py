import importlib.util

from experiments.top_opening_hybrid import build, select_opening


def test_select_opening_uses_largest_exact_family():
    a = [{'farmer': ['NORTH'], 'hands': [], 'market': []}]
    b = [{'farmer': ['SOUTH'], 'hands': [], 'market': []}]
    library = {'tapes': [
        {'team': 'top', 'actions': a, 'money': 1},
        {'team': 'top', 'actions': a, 'money': 2},
        {'team': 'top', 'actions': b, 'money': 99},
    ]}
    opening, evidence = select_opening(library, 'top', 1)
    assert opening == a
    assert evidence['support'] == 2


def test_built_agent_uses_opening_before_planner(tmp_path):
    action = {'farmer': ['NORTH'], 'hands': [], 'market': []}
    library = {'tapes': [{'team': 'top', 'actions': [action], 'money': 1}]}
    path = tmp_path / 'agent.py'
    build(library, 'top', 1, {}, path)
    spec = importlib.util.spec_from_file_location('opening_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.agent({'step': 0}) == action
