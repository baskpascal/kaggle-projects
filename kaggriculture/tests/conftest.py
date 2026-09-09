"""Shared fixtures for the engine rule-validation suites."""
import json

from arena.engine import make_environment, official
from arena.match import observations

PASS = {'farmer': ['PASS'], 'hands': [], 'market': []}


def act(env, farmer=None, hands=None, market=None):
    """Step the official environment with a player-0 action; player 1 passes."""
    env.step([{'farmer': farmer or ['PASS'], 'hands': hands or [], 'market': market or []},
              dict(PASS)])
    return observations(env.state)[0]


def farm10(money=3000.):
    """Bare 10x10 farm with only NW unlocked, farmer on the NW shed-access tile."""
    module = official()
    return {'money': money, 'farmer': [4, 4], 'hands': [], 'hires_today': 0,
            'unlocked_quadrants': ['NW'],
            'tiles': [[module._initial_tile(x, y, 10) for x in range(10)] for y in range(10)]}


def private(shed=None, seeds=None, inventories=None):
    return {'shed': dict(shed or {}), 'seeds': dict(seeds or {}),
            'inventories': [dict(i) for i in (inventories or [{}])]}


def fingerprint(farm, priv):
    return json.dumps([farm, priv], sort_keys=True)
