# SPDX-License-Identifier: Apache-2.0
"""E182: bounded seven-callback physical overlay on unmodified Shop0909 v3.

The live parent freezes after acceptance. Baseline state is a deep-copied shadow;
no shared prices are forecast and no speculative observations reach live state.
"""
from copy import copy, deepcopy
import importlib.util
import sys
from pathlib import Path

START, FINAL = 712, 718
DEFAULT_SETTINGS = {'enabled': True, 'max_simulations': 64, 'passes': 1,
                    'proposals_per_actor': 4}


def _sibling(name, filename):
    path = Path(_sibling.__code__.co_filename).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


class ShopClosure:
    def __init__(self, settings=None):
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings.update(settings or {})
        _require(set(self.settings) == set(DEFAULT_SETTINGS), 'unknown setting')
        _require(type(self.settings['enabled']) is bool, 'enabled must be Boolean')
        keys = ('max_simulations', 'passes', 'proposals_per_actor')
        _require(all(type(self.settings[k]) is int for k in keys) and
                 tuple(self.settings[k] for k in keys) in ((64, 1, 4), (128, 1, 8)),
                 'supported search variants are64/1/4 and128/1/8')
        _sibling('unit_model', 'unit_model.py')
        self.router = _sibling('router_parent', 'router_parent.py')
        self.planner = _sibling('_e182_shop_terminal', 'terminal_planner.py')
        folder = Path(_sibling.__code__.co_filename).resolve().parent
        self.parent = self.router.Policy(folder)
        self.plans, self.last_steps = {}, {}
        self.telemetry = {'planner_invocations': 0, 'activated_games': 0,
                          'activated_steps': 0, 'aborts': 0, 'maxplanning_ms': 0.0, 'cases': []}

    def _settings_guard(self, config):
        get = self.planner._get
        self.planner._settings(config)
        _require(get(config, 'shedCapacity', 100) == 100, 'Shop requires capacity100')
        _require(get(config, 'maxMarketOrdersPerTurn', 10) == 10, 'Shop requires ten slots')

    def _fixed_market(self, action):
        market = action.get('market')
        products = set(self.planner.PRODUCTS)
        _require(isinstance(market, list) and len(market) == len(products),
                 'nonfinal market must cover nine products')
        _require(all(isinstance(o, list) and len(o) == 3 and o[0] == 'SELL'
                     and o[1] in products and type(o[2]) is int and o[2] >= 100
                     for o in market), 'nonfinal orders must be stock-clearing SELL')
        _require({o[1] for o in market} == products, 'missing or duplicate product')

    def shadow_baseline(self, obs, config):
        """Called BEFORE the real712 parent callback. No policy executions on real state."""
        self._settings_guard(config)
        seat = int(obs['player'])
        live = self.parent.players.get(seat)
        _require(live is not None and live.plan == 2 and live.day == 29
                 and live.last_step == START - 1, 'parent must be warm through711 on plan2')
        blocked = self.router.WEED_BLOCKED_WORK
        # Inspect pending queues and every appended command, including excess hands.
        commands = [c for q in live.queues.values() for c in q]
        commands += [c for action in self.parent.tapes[2][START:FINAL + 1]
                     for c in [action['farmer'], *action['hands']]]
        _require(all(isinstance(c, list) and c and c[0] not in blocked for c in commands),
                 'weed-dependent queued/upcoming work')
        farm, private = self.planner.physical_state(obs)
        _require(all(type(q) is int and q >= 0 for q in private['shed'].values()), 'bad shed')
        _require(sum(private['shed'].values()) <= 100, 'overfull shed')
        # The engine initializes zero-valued animal keys in every real shed.
        # Preserve those keys in the exact physical state; reject live animals.
        supported = lambda stock: all(item in self.planner.PRODUCTS or
                                     (item in self.planner.ANIMALS and qty == 0)
                                     for item, qty in stock.items())
        _require(supported(private['shed']), 'unsupported shed item')
        _require(all(supported(inv) and
                     all(type(q) is int and q >= 0 for q in inv.values())
                     for inv in private['inventories']), 'unsupported carried stock')
        # Route data is immutable: Policy.act deep-copies every emitted action.
        # Clone only mutable player queues/sales, not13 complete action tapes.
        shadow = copy(self.parent)
        shadow.players = deepcopy(self.parent.players)
        projected = deepcopy(obs)
        schedule, states_before = [], []
        for step in range(START, FINAL + 1):
            projected['step'], projected['day'], projected['hour'] = step, step // 24, step % 24
            states_before.append(deepcopy(shadow.players[seat]))
            action = shadow.act(projected)
            if step < FINAL:
                self._fixed_market(action)
            schedule.append(deepcopy(action))
            run = self.planner.simulate(projected, config, [action])
            _require(run['actions'][0] == action, 'shadow/model final action mismatch')
            projected['farms'][seat] = run['farm']
            projected['private'] = run['private']
        return schedule, states_before

    def _resume_parent(self, obs, plan):
        # Only safe before physical deviation. Restore exact pre-callback logical
        # state because it was frozen, then evaluate parent on the real observation.
        index = int(obs['step']) - START
        seat = int(obs['player'])
        self.parent.players[seat] = deepcopy(plan['parent_states_before'][index])
        return self.parent.act(obs)

    def act(self, obs, config=None):
        seat, step = int(obs['player']), int(obs['step'])
        previous = self.last_steps.get(seat)
        if step == 0 or (previous is not None and step <= previous):
            # A rewind/duplicate starts a fresh logical lifetime for this seat.
            # Never carry an accepted positional schedule into replay/reset input.
            self.plans.pop(seat, None)
            self.parent.players.pop(seat, None)
        self.last_steps[seat] = step
        plan = self.plans.get(seat)
        if plan and plan.get('accepted') and START <= step <= FINAL:
            if previous != step - 1:
                plan.update(abandoned=True, safety_failure=True, reason='nonconsecutive callback')
            # All nonfinal worker/market output independence was established by
            # the shadow queue/market guards. No advancing live parent after commit.
            result = self.planner.terminal_action(obs, config, plan['baseline'][step - START], plan)
            if plan.get('abandoned'):
                if not plan.get('abort_counted'):
                    self.telemetry['aborts'] += 1
                    plan['abort_counted'] = True
                if not plan.get('deviated'):
                    result = self._resume_parent(obs, plan)
                    self.plans.pop(seat, None)
            if result != plan['baseline'][step - START]:
                self.telemetry['activated_steps'] += 1
            plan['telemetry_case'].update(
                abandoned=bool(plan.get('abandoned')), recovery_steps=plan.get('recovery_steps', 0),
                recovery_failures=deepcopy(plan.get('recovery_failures', [])),
                safety_failure=bool(plan.get('safety_failure')))
            return result
        if not self.settings['enabled'] or step != START:
            return self.parent.act(obs)

        # Clone BEFORE real callback: replaying the same step resets Shop DayState.
        self.telemetry['planner_invocations'] += 1
        try:
            baseline, states_before = self.shadow_baseline(obs, config)
        except (ValueError, KeyError, TypeError, IndexError, self.planner.Unsupported) as exc:
            self.telemetry['cases'].append({'step': step, 'accepted': False, 'reason': str(exc)})
            return self.parent.act(obs)
        actual = self.parent.act(obs)
        if actual != baseline[0]:
            self.telemetry['cases'].append({'step': step, 'accepted': False,
                                            'reason': 'actual initial parent callback differs'})
            return actual
        plan = self.planner.plan_terminal(obs, config, baseline,
            max_simulations=self.settings['max_simulations'], passes=self.settings['passes'],
            proposals_per_actor=self.settings['proposals_per_actor'])
        plan['parent_states_before'] = states_before
        self.telemetry['maxplanning_ms'] = max(self.telemetry['maxplanning_ms'], plan.get('planning_ms', 0.0))
        certificate = plan.get('certificate', {})
        case = {'seat': seat, 'step': step, 'accepted': bool(plan.get('accepted')),
                'reason': plan.get('reason'), 'simulations': plan.get('simulations'),
                'planning_ms': plan.get('planning_ms'),
                'changed_workers': plan.get('changed_workers', []),
                'sold_unit_delta': certificate.get('sold_unit_delta', {}),
                'positive_physical_deposit_gain': certificate.get('positive_physical_deposit_gain', False),
                'candidate_overflow': certificate.get('candidate_overflow'),
                'markets_712_717_unchanged': certificate.get('markets_712_717_unchanged', False)}
        plan['telemetry_case'] = case
        self.telemetry['cases'].append(case)
        if not plan.get('accepted'):
            return actual
        self.plans[seat] = plan
        result = self.planner.terminal_action(obs, config, baseline[0], plan)
        self.telemetry['activated_games'] += 1
        self.telemetry['activated_steps'] += int(result != actual)
        return result


def build_agent(settings=None):
    adapter = ShopClosure(settings)

    def agent(observation, configuration=None):
        return adapter.act(observation, configuration)

    agent.adapter = adapter
    agent.telemetry = adapter.telemetry
    agent.settings = adapter.settings
    return agent
