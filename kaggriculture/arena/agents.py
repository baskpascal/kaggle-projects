import builtins
import hashlib
import inspect
import random
import sys
import types
import weakref
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {
    'crop': {'only_crop': 'CARROT', 'adaptive': False, 'max_quadrants': 1},
    'animal': {'animal_target': 6, 'animal_type': 'GOOSE', 'max_hands': 9},
    'diversified': {'opponent_weight': 1.2, 'max_hands': 10, 'max_quadrants': 3},
    # Issue #25: a conservative opening profile for the selected frozen base.
    'liquidity_first': {
        'cash_reserve': 1200,
        'expand_day': 9,
        'max_hands': 7,
        'plant_until_hour': 16,
        'sale_floor_fraction': 0.9,
        'sale_recovery_fraction': 0.99,
    },
}


# Unwinding sys.modules cannot undo a bundle that reaches into a module the
# arena already imported. Nothing at load time can, so the isolation itself
# comes from one process per game. What load time *can* do is notice, which is
# the difference between evidence and a silently corrupted game.
INTERPRETER = 'kaggle_environments.envs.kaggriculture.kaggriculture'
# `signal` is watched for what it says about a bundle, not because watching it protects
# anything: the per-turn timer is armed through this module, and a bundle that rebinds
# `signal.setitimer` or `signal.signal` is reaching for the guard. Handler *state* is not a
# module attribute, so a bundle that merely calls `signal.signal(SIGALRM, SIG_IGN)` passes
# this check untouched -- that case is caught by the elapsed-time check in `arena.match`
# and, in the limit, by the process kill in `arena.parallel`.
WATCHED = ('builtins', 'json', 'random', 'time', 'math', 'copy', 'signal', INTERPRETER)
_ARITIES = weakref.WeakKeyDictionary()


def _watched_modules():
    # Import the interpreter if a caller has not already, so detection never
    # depends silently on which module happened to be imported first.
    if INTERPRETER not in sys.modules:
        from .engine import official
        official()
    modules = [sys.modules[name] for name in WATCHED if name in sys.modules]
    return modules + [module for name, module in sys.modules.items()
                      if name.split('.')[0] in ('arena', 'agent', 'eval')
                      and isinstance(module, types.ModuleType)]


def module_fingerprint():
    """Identity of every attribute the arena relies on, plus the global RNG."""
    snapshot = {}
    for module in _watched_modules():
        name = getattr(module, '__name__', None)
        if name is None:
            continue
        snapshot[name] = {key: id(value) for key, value in vars(module).items()}
    snapshot['__builtins__'] = {key: id(value) for key, value in vars(builtins).items()}
    snapshot['__random_state__'] = {'state': hash(repr(random.getstate()))}
    return snapshot


def tampering(before, after):
    """Report names a load rebound, added or removed in an arena-visible module."""
    findings = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name, {}), after.get(name, {})
        for key in sorted(set(old) | set(new)):
            if key.startswith('__') and key.endswith('__') and name != '__random_state__':
                continue
            if key not in new:
                findings.append(f'{name}.{key} removed')
            elif key not in old:
                findings.append(f'{name}.{key} added')
            elif old[key] != new[key]:
                findings.append(f'{name}.{key} rebound')
    return findings


def load_agent(name, seed=0, strict=True):
    if name.startswith('xliq::'):
        from agent.xliq import wrap_agent
        return wrap_agent(load_agent(name.split('::', 1)[1], seed, strict))
    if name.startswith('clock::'):
        base = load_agent(name.split('::', 1)[1], seed, strict)
        def canonical_clock_agent(observation, configuration=None):
            day, hour = observation.get('day'), observation.get('hour')
            turns = (configuration or {}).get('turnsPerDay', 24)
            if (type(day) is not int or type(hour) is not int or type(turns) is not int
                    or day < 0 or turns <= 0 or not 0 <= hour < turns):
                raise ValueError('Malformed day/hour clock')
            derived = day * turns + hour
            supplied = observation.get('step')
            if supplied is not None and (type(supplied) is not int or supplied != derived):
                raise ValueError('step disagrees with canonical day/hour clock')
            normalized = dict(observation)
            normalized['step'] = derived
            return invoke(base, normalized, configuration or {})
        return canonical_clock_agent
    if name == 'champion':
        return load_agent(str(ROOT / 'versions/v000/main.py'), seed, strict)
    if name in ('starter', 'pass'):
        from .engine import official
        return official().agents[name]
    if name == 'random':
        # The upstream random agent constructs an unseeded Random every turn.
        # This explicitly named local seeded baseline is reproducible instead.
        import random
        rng = random.Random(seed)
        def random_agent(obs, config=None):
            me = obs['farms'][obs['player']]
            choices = [['PASS'], ['NORTH'], ['SOUTH'], ['EAST'], ['WEST'], ['WATER'], ['HARVEST']]
            available = [c for c, n in obs['private']['seeds'].items() if n > 0]
            if available:
                choices += [['PLANT', rng.choice(available)]]
            market = [['SELL', c, n] for c, n in obs['private']['shed'].items() if n > 0]
            if me['money'] >= 20 and not available:
                market.append(['BUY_SEED', 'CARROT', 1])
            return {'farmer': rng.choice(choices), 'hands': [], 'market': market[:10]}
        return random_agent
    if name in VARIANTS or name == 'challenger':
        from agent.planner import policy
        params = VARIANTS.get(name, {})
        def variant(obs, config=None):
            return policy(obs, config, params)
        return variant
    path = Path(name).resolve()
    if not path.is_file():
        raise ValueError(f'Unknown agent {name!r}')
    # Fresh namespace per seat/game prevents accidental state leakage.
    # Public bundles register names such as v23/v43 in sys.modules. Keep those
    # modules private to this load, including when two seats load the same file.
    # Fingerprint first: it may import the interpreter, and anything the arena
    # itself pulled in must be inside `before` so the unwind never deletes it.
    # Dropping a native extension from sys.modules and re-importing it later
    # crashes the interpreter outright.
    fingerprint_before = module_fingerprint()
    before = dict(sys.modules)
    name = '__kaggriculture_submission__'
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        source = path.read_bytes()
        exec(compile(source, str(path), 'exec'), module.__dict__)
        function = module.__dict__['agent']
    finally:
        for key in set(sys.modules) - before.keys():
            del sys.modules[key]
        sys.modules.update(before)
    found = tampering(fingerprint_before, module_fingerprint())
    if found and strict:
        raise RuntimeError(f'{path} mutated arena-visible state at import: ' + '; '.join(found[:8]))
    load_agent.last_tampering = found
    function.__arena_sha256__ = hashlib.sha256(source).hexdigest()
    return function


def prepare(function):
    """Resolve callback arity once, when an agent enters a match."""
    try:
        _ARITIES[function] = len(inspect.signature(function).parameters)
    except TypeError:
        # Some callable objects cannot be weak-referenced. They still get one lookup per
        # load through an attribute when their implementation permits it.
        arity = len(inspect.signature(function).parameters)
        try:
            function.__arena_arity__ = arity
        except (AttributeError, TypeError):
            return function
    return function


def invoke(function, observation, configuration):
    arity = getattr(function, '__arena_arity__', None)
    if arity is None:
        arity = _ARITIES.get(function)
    if arity is None:
        prepare(function)
        arity = getattr(function, '__arena_arity__', None)
        if arity is None:
            arity = _ARITIES.get(function)
    if arity is None:  # rare callable objects that are neither mutable nor weak-referenceable
        arity = len(inspect.signature(function).parameters)
    if arity >= 2:
        return function(observation, configuration)
    return function(observation)


def agent_hash(name):
    if name.startswith('xliq::'):
        digest = hashlib.sha256()
        digest.update(agent_hash(name.split('::', 1)[1]).encode())
        digest.update((ROOT / 'agent' / 'xliq.py').read_bytes())
        return digest.hexdigest()
    if name.startswith('clock::'):
        digest = hashlib.sha256()
        digest.update(agent_hash(name.split('::', 1)[1]).encode())
        digest.update(b'canonical-day-hour-adapter-v1')
        return digest.hexdigest()
    if name == 'champion':
        return agent_hash(str(ROOT / 'versions/v000/main.py'))
    path = Path(name)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if name in ('starter', 'pass'):
        from .engine import INTERPRETER_HASH
        return hashlib.sha256((INTERPRETER_HASH + name).encode()).hexdigest()
    digest = hashlib.sha256()
    for source in sorted((ROOT / 'agent').glob('*.py')):
        digest.update(source.name.encode())
        digest.update(source.read_bytes())
    digest.update(Path(__file__).read_bytes())
    digest.update(name.encode())
    return digest.hexdigest()
