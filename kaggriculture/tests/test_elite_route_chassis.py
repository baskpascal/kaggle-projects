from pathlib import Path

from experiments.elite_route_chassis import build


def test_builder_embeds_routes_and_keeps_reactive_call_order(tmp_path):
    parent = tmp_path / 'parent.py'
    parent.write_text('''import copy
import json
from pathlib import Path
ROUTE_STEP = 144
FINAL_PLAN_STEP = 648
LAST_STEP = 718
SHED_CAPACITY = 100
SHOP_PLANS = {}
class DayState:
    def __init__(self):
        self.plan = 0
class Policy:
    def __init__(self, folder):
        self.tapes = json.loads((Path(folder) / "actions.json").read_text())
        if len(self.tapes) != 13 or any(len(tape) != LAST_STEP + 1 for tape in self.tapes):
            raise ValueError("Expected 13 complete, 719-turn action tapes")
    def act(self, observation):
        step=observation["step"]; state=DayState()
        if step == ROUTE_STEP:
            shops = observation["town"]["unlocked_shops"]
            state.plan = SHOP_PLANS.get(tuple(shops[:2]), 0)
        if step == FINAL_PLAN_STEP:
            state.plan = 2
        return self.tapes[state.plan][step]
''')
    action = {'farmer': ['PASS'], 'hands': [], 'market': []}
    tapes = [{'actions': [action] * 719}, {'actions': [action] * 719}]
    target = tmp_path / 'main.py'
    digest = build(tapes, {'144': {'1': {'YARN_STORE': 0}}}, 1, parent, target)
    source = target.read_text()
    assert 'actions.json' not in source
    assert 'self.plan = DEFAULT' in source
    assert 'choices = ROUTES.get' in source
    assert 'len(self.tapes) != 13' not in source
    assert len(digest) == 64
