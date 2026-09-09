"""Run independent matches in parallel, one fresh process per game.

Isolation here is not a nicety. A submission bundle is third-party code that runs
`import`-time statements inside our interpreter, and `arena.agents` can *notice* a load
that rebinds something the arena reads but cannot undo it. The undo is the process
boundary, so every game gets its own address space and no worker is ever reused for a
second game.

That guarantee used to be paid for with a full interpreter start per game: `spawn` gives
the child nothing, so each of the 719-turn games re-imported the engine before playing.
Measured on 24 games, that overhead was about 1.15 of every 1.85 seconds — roughly
two thirds of the cost was not simulation.

`forkserver` keeps the guarantee and drops the overhead. A separate server process imports
the engine once and every worker is forked from it, so a worker starts warm but still
starts *clean*: the server never runs a game, so nothing a bundle did can reach the next
fork. Measured on the same 24 games, byte-identical final money in every configuration:

    method       1 worker              14 workers
    spawn        1.857 s/game          0.265 s/game   ( 3.8 games/s)
    forkserver   0.705 s/game          0.086 s/game   (11.6 games/s)

`forkserver` is POSIX-only, so the start method falls back to `spawn` where it is missing
and the results are the same either way.

The second guarantee is the deadline, and it lives here rather than in the child on
purpose. `arena.match.deadline` arms `signal.setitimer` **inside the interpreter that runs
the bundle**, so the bundle can switch it off with two lines, and a bundle that never
returns hangs the worker for good without the guard ever firing. Anything that runs in the
same interpreter as adversarial code can be reached by it. The barrier that cannot be
reached is the parent: one process per game, a wall-clock deadline held here, and a kill of
the child's whole process group when it expires. The in-child timer stays as an
optimisation for the honest slow agent; the safety argument does not rest on it.

A killed game is a failure result, never a retry. Repeating it would let the harness select
agents that work if you try a few times, which is what the preflight exists to refuse.
"""
import multiprocessing
import os
import queue as queuelib
import signal
import time

from .agents import agent_hash
from .engine import fingerprint
from .match import run_match
from .resources import resource_lease

# A full 719-turn game costs seconds, not minutes, in every configuration measured here, so
# this is about two orders of magnitude of headroom: it exists to end a hang, not to police
# a slow agent. `ARENA_MATCH_TIMEOUT` overrides it, and `timeout=None` disables it entirely
# for a caller that has its own supervision.
MATCH_TIMEOUT = float(os.environ.get('ARENA_MATCH_TIMEOUT', 300.))

# Imported once in the forkserver parent, before any game exists. Keep this list to modules
# that are pure to import: anything with import-time side effects would have those effects
# shared by every forked worker instead of being redone per game.
PRELOAD = ('arena.match', 'arena.engine', 'arena.agents', 'arena.telemetry',
           'kaggle_environments')
MATCH_FIELDS = ('candidate', 'opponent', 'seed', 'seat', 'backend', 'configuration',
                'replay', 'telemetry_enabled', 'replay_steps', 'evidence_profile')


def _match_kwargs(job):
    """Keep transport/store metadata out of ``run_match``'s public arguments."""
    return {field: job[field] for field in MATCH_FIELDS if field in job}


def _run(kwargs):
    return run_match(**_match_kwargs(kwargs))


def _play(index, job, results):
    """Run one game in this child and hand the row back, tagged with its position.

    The first thing the child does is leave its parent's process group. A bundle can spawn
    subprocesses, and killing only the child we know about would leave those running; with
    the child as its own group leader, one `killpg` reaches the whole tree.
    """
    try:
        os.setsid()
    except (AttributeError, OSError):  # Windows, or already a group leader.
        pass
    try:
        results.put((index, run_match(**_match_kwargs(job)), None))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent, never swallowed
        results.put((index, None, f'{type(exc).__name__}: {exc}'))


def _terminate(process):
    """Kill the child and everything it started, then reap it."""
    if process.pid is None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        process.kill()
    process.join(5)
    if process.is_alive():  # pragma: no cover - the group kill has never failed in practice
        process.kill()
        process.join()


def timeout_row(job, seconds):
    """A killed game, shaped like any other row so no consumer has to special-case it.

    Both sides carry the failure. We cannot attribute a hang to one agent from outside the
    process, and the alternative -- guessing -- would either credit us a win we did not play
    or concede one we did not lose. Both sides failing scores 0.5 by the same rule
    `run_match` uses, and every comparison in `eval/` already refuses evidence containing
    callback failures, so a timed-out game invalidates its comparison instead of quietly
    counting as a draw.
    """
    failure = [{'step': None, 'kind': 'MatchTimeout',
                'message': f'Killed after {seconds:g}s; the game did not finish'}]
    profile = job.get('evidence_profile')
    if profile is None:
        profile = ('full' if job.get('telemetry_enabled', True) else 'audit')
    return {
        'candidate': job['candidate'], 'opponent': job['opponent'],
        'candidate_hash': agent_hash(job['candidate']),
        'opponent_hash': agent_hash(job['opponent']),
        'seed': job.get('seed'), 'seat': job.get('seat', 0), 'score': .5,
        'money': 0., 'opponent_money': 0., 'margin': 0., 'steps': 0,
        'backend': job.get('backend', 'fast'), 'configuration': None,
        'environment': fingerprint(), 'failures': failure, 'opponent_failures': list(failure),
        'evidence_profile': profile,
        'audit': {}, 'opponent_audit': {}, 'sales': {}, 'opponent_sales': {},
        'telemetry_version': None, 'daily': None, 'opponent_daily': None,
        'runtime_ms': [], 'unsold_items': 0, 'wall_seconds': seconds, 'timed_out': True,
    }


def start_method(requested=None):
    """`forkserver` when the platform has it, else `spawn`; never a bare `fork`.

    A bare `fork` would be faster still and is rejected on purpose: it is incompatible with
    one-process-per-game in `ProcessPoolExecutor`, and forking a process that has already
    played a game would hand the next game whatever the last bundle left behind.
    """
    requested = requested or os.environ.get('ARENA_START_METHOD')
    available = multiprocessing.get_all_start_methods()
    if requested:
        if requested not in available or requested == 'fork':
            raise ValueError(f'Unusable start method {requested!r}; available: {available}')
        return requested
    return 'forkserver' if 'forkserver' in available else 'spawn'


def matches(jobs, workers=4, method=None, timeout=-1, ordered=True):
    """Play every job in its own process, at most `workers` at a time.

    One process per game is the isolation, and it is also what makes the deadline
    enforceable: there is nothing to unwind, only a process to kill.

    `ordered=True` yields rows in job order however they finish, which is the contract the
    positional callers in `experiments/` rely on: it is a guarantee, not an accident, and
    `tests/test_parallel.py` pins it.

    `ordered=False` yields each row the moment it lands. That matters because holding a
    finished game to wait for an earlier one holds it *in RAM*: with the default 300 s
    deadline, one hung first game can leave every later game of the run unpersisted, and a
    driver that dies in that window replays work it had already computed. A common row is
    116-134 KB, so the buffer is expensive as well as fragile. Unordered, the peak of
    unpersisted results is the in-flight window -- at most `workers` rows -- instead of the
    whole run. `arena.jobs.execute` correlates by `job_id` and never by position, so it
    wants this mode; `stream` below is that pairing spelled out.
    """
    if workers < 1:
        raise ValueError('workers must be positive')
    jobs = list(jobs)
    limit = MATCH_TIMEOUT if timeout == -1 else timeout
    if limit is not None and limit <= 0:
        raise ValueError('timeout must be positive, or None to disable the barrier')
    if not jobs:
        return
    chosen = start_method(method)
    context = multiprocessing.get_context(chosen)
    if chosen == 'forkserver':
        context.set_forkserver_preload(list(PRELOAD))
    requested_workers = workers
    useful_workers = min(workers, len(jobs))
    with resource_lease(useful_workers) as resources:
        if useful_workers != requested_workers:
            resources['requested_workers'] = requested_workers
            resources['adjustment'] = (f'requested {requested_workers} workers, adjusted to '
                                       f'{useful_workers}: this batch has {len(jobs)} jobs')
        workers = resources['granted_workers']
        results = context.Queue()
        running, buffered, started_at, vanished = {}, {}, {}, {}
        pending, emitted, done = iter(range(len(jobs))), 0, set()
        try:
            while len(done) < len(jobs):
                while len(running) < workers:
                    index = next(pending, None)
                    if index is None:
                        break
                    process = context.Process(target=_play, args=(index, jobs[index], results),
                                              daemon=False)
                    process.start()
                    running[index], started_at[index] = process, time.monotonic()
                try:
                    index, row, error = results.get(timeout=.1)
                    process = running.pop(index, None)
                    if process is not None:
                        process.join(5)
                    if index not in done:  # A row that lost the race with its own kill.
                        if error is not None:
                            raise RuntimeError(f'Match failed in its own process: {error}')
                        done.add(index)
                        row['execution_resources'] = dict(resources)
                        if ordered:
                            buffered[index] = row
                        else:
                            yield row
                except queuelib.Empty:
                    pass
                for index, process in list(running.items()):
                    if limit is not None and time.monotonic() - started_at[index] > limit:
                        _terminate(process)
                        running.pop(index)
                        done.add(index)
                        row = timeout_row(jobs[index], limit)
                        row['execution_resources'] = dict(resources)
                        if ordered:
                            buffered[index] = row
                        else:
                            yield row
                    elif not process.is_alive():
                        # Exiting and being read are not simultaneous: a child that has already
                        # put its row on the queue is dead before the parent drains it. Only a
                        # child still unreported well after it died has actually crashed.
                        first_seen = vanished.setdefault(index, time.monotonic())
                        if time.monotonic() - first_seen > 5:
                            running.pop(index)
                            raise RuntimeError(f'Match worker died without a result: {jobs[index]}')
                while emitted in buffered:
                    yield buffered.pop(emitted)
                    emitted += 1
            # Ordered mode drains inside the loop as the gap closes, so what is left here is
            # only the tail that completed after the last job started.
            while emitted in buffered:
                yield buffered.pop(emitted)
                emitted += 1
        finally:
            for process in running.values():
                _terminate(process)
            results.close()


def stream(jobs, workers=4, method=None, timeout=-1):
    """A `runner(jobs, workers)` that hands every game to its caller the moment it lands.

    This is the shape `arena.jobs.execute` wants: it identifies a result by recomputing
    `job_id` from the row's own contents, so arrival order carries no meaning for it, and
    persisting immediately is the whole point of the durable store.
    """
    return matches(jobs, workers=workers, method=method, timeout=timeout, ordered=False)
