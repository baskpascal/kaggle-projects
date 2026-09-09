"""A durable job store, so an interrupted run resumes instead of restarting.

The harness used to materialise the whole job list in memory, play it, and write the
report at the end. A run interrupted at game 6 200 of 8 000 lost 6 200 games. Here every
finished game is persisted as it arrives, and `pending()` is the difference between the
plan and what the store already holds.

The identity of a job is `job_id`: a SHA-256 over candidate, opponent, seed, seat,
backend, split **and the two agent hashes** -- not their paths. The hash is a better
identity than a path or a commit because it notices an edited, uncommitted file that a
commit check waves through, and because it travels on every result row, so the
aggregator can *refuse* a mixed set instead of trusting a check made before execution.

SQLite rather than JSONL: resume needs a keyed read, `INSERT OR IGNORE` gives
idempotency for free, and concurrent writers are safe. JSONL appends cheaply but forces
a scan of the whole file to learn what is missing.

Only infrastructure faults are retried. See `INFRASTRUCTURE`.
"""
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
from datetime import datetime, timezone

from .agents import agent_hash

MEMORY = ':memory:'
JOURNAL_MODE = 'MEMORY'
SQLITE_TIMEOUT = 30.
SQLITE_ATTEMPTS = 5
SQLITE_BACKOFF = .2
TRANSIENT = ('disk i/o error', 'database is locked', 'database table is locked',
             'unable to open database file')


def _transient(exc):
    return any(text in str(exc).lower() for text in TRANSIENT)

JOB_FIELDS = ('candidate', 'opponent', 'seed', 'seat', 'backend', 'split')
IDENTITY_FIELDS = (*JOB_FIELDS, 'candidate_hash', 'opponent_hash')

# A worker that died, a pool that broke, a process that never started: the game did not
# happen, so playing it is not repeating it. Everything else -- an agent exception, a
# deadline overrun, an invalid simulation state -- is a *result*, and repeating it would
# make the harness select agents that work if you try a few times, which is precisely
# what the preflight exists to refuse.
INFRASTRUCTURE = (BrokenProcessPool, MemoryError, OSError)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    candidate TEXT NOT NULL, opponent TEXT NOT NULL,
    seed INTEGER NOT NULL, seat INTEGER NOT NULL,
    backend TEXT NOT NULL, split TEXT NOT NULL,
    candidate_hash TEXT NOT NULL, opponent_hash TEXT NOT NULL,
    outcome TEXT NOT NULL, score REAL NOT NULL,
    money REAL, opponent_money REAL, margin REAL,
    failed INTEGER NOT NULL, wall_seconds REAL,
    hostname TEXT, git_commit TEXT, git_dirty INTEGER,
    recorded_at TEXT NOT NULL, row TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_run ON jobs (candidate_hash, split);
-- One store belongs to one run. `job_id` covers the agents, seeds, seats, backend and
-- split, which is everything that decides whether two games are the same game -- and
-- nothing about the deadline, the panel or the thresholds the result will be read under.
-- Two runs that differ only there produce identical job ids, so a resume would silently
-- serve games from the wrong experiment. The bound run id is what refuses that.
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    run_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def job_id(spec):
    missing = [field for field in IDENTITY_FIELDS if spec.get(field) is None]
    if missing:
        raise ValueError(f'Job identity needs {", ".join(missing)}')
    return digest({field: spec[field] for field in IDENTITY_FIELDS})


def plan(candidate, opponents, seeds, *, backend='fast', split='dev'):
    """The exact job list for a run, hashed once rather than once per game."""
    hashes = {name: agent_hash(name) for name in (candidate, *opponents)}
    jobs = []
    for seed in seeds:
        for opponent in opponents:
            for seat in (0, 1):
                spec = {'candidate': candidate, 'opponent': opponent, 'seed': seed,
                        'seat': seat, 'backend': backend, 'split': split,
                        'candidate_hash': hashes[candidate],
                        'opponent_hash': hashes[opponent]}
                jobs.append({**spec, 'job_id': job_id(spec)})
    return jobs


def id_of_row(row, split):
    """Recompute a finished game's identity from the row itself.

    Deliberately not positional: results come back in whatever order the pool finishes
    them after a retry, and pairing by position would silently misfile a game.
    """
    return job_id({**{field: row.get(field) for field in IDENTITY_FIELDS}, 'split': split})


def outcome_of(score):
    return {1.: 'win', 0.: 'loss', .5: 'tie'}[score]


def git_provenance(root=None):
    """Commit and a dirty flag, as diagnostics beside the authoritative agent hashes."""
    def run(*args):
        try:
            done = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None
    commit = run('git', 'rev-parse', 'HEAD')
    status = run('git', 'status', '--porcelain')
    return {'git_commit': commit, 'git_dirty': None if status is None else int(bool(status))}


class JobStore:
    """Keyed, idempotent, append-only record of finished games.

    The connection is opened per operation and never held across a call into the match
    executor. That is not fastidiousness: `arena/parallel.py` starts a forkserver, the
    forkserver process inherits whatever file descriptors are open at the moment it
    starts, and a SQLite connection carried across a fork produces exactly the
    `disk I/O error` this store was failing with. Connecting costs about a millisecond
    against a game that costs seven hundred.
    """

    def __init__(self, path, provenance=None, run_id=None):
        if os.environ.get('ARENA_ROLE') == 'ray-worker':
            raise PermissionError('Ray workers cannot open the authoritative job store')
        self.path = path if path == MEMORY else Path(path)
        if self.path != MEMORY:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # An in-memory store dies with the process, so it keeps one connection: there is
        # no durability to protect and reconnecting would discard the whole store.
        self._shared = sqlite3.connect(MEMORY) if self.path == MEMORY else None
        with self._session() as connection:
            connection.executescript(SCHEMA)
        self.provenance = {'hostname': socket.gethostname(),
                           **(git_provenance() if provenance is None else provenance)}
        self.run_id = self.bind(run_id) if run_id is not None else self.bound_run()

    def bound_run(self):
        with self._session() as connection:
            row = connection.execute('SELECT run_id FROM run WHERE id = 1').fetchone()
        return row['run_id'] if row else None

    def bind(self, run_id):
        """Claim this store for one run, or refuse to hand its games to another.

        A store adopts the first run that claims it, which is what keeps a store written
        before run specs existed resumable. Once claimed it never changes hands: resuming
        under a different spec would reuse games played under other deadlines, another
        panel or different thresholds, and the identity `job_id` computes cannot see any
        of that.
        """
        if not isinstance(run_id, str) or not run_id:
            raise ValueError('A run id is a nonempty string')
        held = self.bound_run()
        if held is None:
            with self._session() as connection:
                connection.execute('INSERT INTO run (id, run_id, bound_at) VALUES (1, ?, ?)',
                                   (run_id, datetime.now(timezone.utc).isoformat()))
            return run_id
        if held != run_id:
            raise ValueError(f'{self.path} holds games for run {held[:12]}, and this run is '
                             f'{run_id[:12]}. Resume reuses only the games of its own run; '
                             'the two specs differ in something job identity cannot see.')
        return held

    @contextmanager
    def _open(self):
        connection = self._shared or sqlite3.connect(str(self.path), timeout=SQLITE_TIMEOUT)
        connection.row_factory = sqlite3.Row
        try:
            if self._shared is None:
                # The project checkout lives on a 9p/DrvFs mount, where the rollback
                # journal's per-commit create-write-delete cycle is unreliable while a
                # dozen worker processes are doing their own file IO: it surfaces as a
                # transient `disk I/O error`. Keeping the journal in memory removes that
                # churn. The cost is crash atomicity, not durability of committed rows,
                # so an interrupted or killed run -- which is what this store exists to
                # rescue -- still resumes exactly; only a power loss mid-commit is at risk.
                connection.execute(f'PRAGMA journal_mode={JOURNAL_MODE}')
                connection.execute(f'PRAGMA busy_timeout={int(SQLITE_TIMEOUT * 1000)}')
            yield connection
            connection.commit()
        finally:
            if self._shared is None:
                connection.close()

    @contextmanager
    def _session(self):
        """`_open`, retried: a transient filesystem fault must not lose a finished game."""
        for attempt in range(SQLITE_ATTEMPTS):
            try:
                with self._open() as connection:
                    yield connection
                return
            except sqlite3.OperationalError as exc:
                # A programming error in the statement is also an OperationalError, and
                # repeating it would just be slower. Only retry what a retry can fix.
                if attempt == SQLITE_ATTEMPTS - 1 or not _transient(exc):
                    raise
                time.sleep(SQLITE_BACKOFF * (attempt + 1))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._shared is not None:
            self._shared.close()
            self._shared = None

    def record(self, row, split):
        """Idempotent by job_id: recording the same game twice writes once."""
        identity = id_of_row(row, split)
        with self._session() as connection:
            connection.execute(
                'INSERT OR IGNORE INTO jobs (job_id, candidate, opponent, seed, seat, backend,'
                ' split, candidate_hash, opponent_hash, outcome, score, money, opponent_money,'
                ' margin, failed, wall_seconds, hostname, git_commit, git_dirty, recorded_at, row)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity, row['candidate'], row['opponent'], row['seed'], row['seat'],
                 row['backend'], split, row['candidate_hash'], row['opponent_hash'],
                 outcome_of(row['score']), row['score'], row.get('money'),
                 row.get('opponent_money'), row.get('margin'),
                 int(bool(row.get('failures')) or bool(row.get('opponent_failures'))),
                 row.get('wall_seconds'), row.get('hostname', self.provenance['hostname']),
                 row.get('git_commit', self.provenance.get('git_commit')),
                 row.get('git_dirty', self.provenance.get('git_dirty')),
                 datetime.now(timezone.utc).isoformat(), json.dumps(row, default=str)))
        return identity

    def completed(self):
        with self._session() as connection:
            return {row['job_id'] for row in connection.execute('SELECT job_id FROM jobs')}

    def pending(self, jobs):
        done = self.completed()
        return [job for job in jobs if job['job_id'] not in done]

    def rows(self, job_ids=None):
        """Finished games as the match rows they were, ordered by when they landed.

        Arrival order is a fact about the run, not about the evidence, so a caller that
        needs a reproducible order asks for one: `rows_in_order` materialises a plan.
        """
        wanted = None if job_ids is None else set(job_ids)
        with self._session() as connection:
            cursor = connection.execute('SELECT job_id, row FROM jobs ORDER BY rowid')
            return [json.loads(record['row']) for record in cursor
                    if wanted is None or record['job_id'] in wanted]

    def rows_in_order(self, jobs):
        """The plan's games, in the plan's order, whatever order they were played in.

        Games are persisted as they land so that a driver that dies keeps them, which means
        the store's own order is the order the cluster happened to finish. A report and its
        digest must not move because a node was slow, so the canonical order is the plan's
        and it is reimposed here rather than assumed upstream.
        """
        wanted = [job['job_id'] for job in jobs]
        by_id = {}
        for row in self.rows(set(wanted)):
            identity = row.get('job_id')
            if identity is not None:
                by_id[identity] = row
        missing = [identity for identity in wanted if identity not in by_id]
        if missing:
            raise OSError(f'{len(missing)} planned game(s) are absent from the store')
        return [by_id[identity] for identity in wanted]


def single_provenance(rows):
    """Refuse a set whose games were not all played by the same two artifacts.

    `eval/ladder.py` already demands exact pairing and raises when it is absent; this is
    the same discipline one level down, applied per result row rather than to a check
    made before execution, which is what makes it survive distribution.
    """
    candidates = {row['candidate_hash'] for row in rows}
    if len(candidates) > 1:
        raise ValueError(f'Mixed candidate hashes in one result set: {sorted(candidates)}')
    per_opponent = {}
    for row in rows:
        per_opponent.setdefault(row['opponent'], set()).add(row['opponent_hash'])
    mixed = sorted(name for name, hashes in per_opponent.items() if len(hashes) > 1)
    if mixed:
        raise ValueError(f'Mixed opponent hashes for: {", ".join(mixed)}')
    environments = {digest(row['environment']) for row in rows if 'environment' in row}
    if len(environments) > 1:
        raise ValueError('Result set spans more than one engine fingerprint')
    return True


def execute(jobs, store, split, runner, *, workers=4, attempts=3, on_row=None):
    """Play the pending jobs, persisting each as it lands, retrying only faults.

    `runner(jobs, workers)` yields match rows; injecting it keeps this independent of the
    executor in `arena/parallel.py`. After an infrastructure fault the remaining work is
    recomputed from the store, so nothing already finished is replayed and nothing lost
    mid-stream is skipped.

    Each row is recorded the moment the runner yields it, and every runner in the project
    now yields on completion rather than in plan order, so the peak of computed-but-unsaved
    work is the in-flight window instead of everything behind the slowest game. The rows
    handed back are re-materialised in the plan's order, so a report and its digest do not
    depend on which node finished first.
    """
    if attempts < 1:
        raise ValueError('attempts must be positive')
    last = None
    for attempt in range(attempts):
        remaining = store.pending(jobs)
        if not remaining:
            return store.rows_in_order(jobs)
        # The worker needs the expected hashes and IDs so a remote batch can prove which
        # artifacts and jobs it actually executed. ``arena.parallel`` strips this transport
        # metadata before calling ``run_match``.
        payload = [dict(job) for job in remaining]
        expected = {job['job_id'] for job in remaining}
        seen = set()
        try:
            for row in runner(payload, workers):
                identity = id_of_row(row, split)
                if row.get('job_id', identity) != identity:
                    raise ValueError(f'Result job_id does not match its contents: {row.get("job_id")}')
                if identity not in expected:
                    raise ValueError(f'Result does not belong to the admitted plan: {identity}')
                if identity in seen:
                    raise ValueError(f'Runner returned duplicate job: {identity}')
                seen.add(identity)
                row['job_id'] = identity
                store.record(row, split)
                if on_row:
                    on_row(row)
            missing = expected - seen
            if missing:
                raise OSError(f'Runner omitted {len(missing)} admitted job(s)')
        except INFRASTRUCTURE as exc:
            last = exc
            continue
        return store.rows_in_order(jobs)
    raise RuntimeError(f'Infrastructure faults exhausted {attempts} attempts: {last}')
