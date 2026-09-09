"""One validated spec for a run, and one manifest that ties its evidence back to it.

The project's guarantees are good and they are scattered. `arena.league`, `arena.paired`,
the experiments, `eval.standing`, `eval.submit_gate` and the benchmark scripts each carry
their own defaults for workers, seeds, panels, thresholds and output. `config.yaml`
summarises some of them while `seed_registry.json`, the opponent manifests and Python
constants are the real authorities. The consequence is not hypothetical: it is possible
today to run the right strategy under a combination of parameters that is not the
experiment the report appears to name, and nothing downstream notices.

A `RunSpec` is that combination, written down once, validated before anything is
consumed, and hashed. `run_id` is the hash. Change a material field and it is a different
run, by construction rather than by discipline.

Material and not material are separated on purpose, and the line is what the run measured
rather than how it was carried out. Agents, opponents, panel, seeds, split, registry
revision, backend, engine fingerprint, deadlines, retry policy, thresholds and the gate
that will read the result all enter the `run_id`: any of them moving means the number
means something else. The distribution -- worker counts, CPUs per task, batch size, which
Ray address -- does not, because issue #43 measured batches as bit-exact across nodes, so
a run started locally and resumed on the cluster is the same run and must be allowed to
say so.

The manifest is the other half. A `run_id` that nothing points at proves nothing, so
`manifest()` ties the spec to the job store, the paired comparison, the standing, the
preflight and the built artifact by their hashes, and refuses -- explicitly, never
silently -- to certify a set whose pieces declare different runs. Evidence written before
this module existed carries no `run_id` at all; that is reported as unbound rather than
assumed to fit, which is the same refusal `eval/dossier.py` makes one level up.

See docs/RUN_SPEC.md.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .engine import fingerprint
from .seeds import REGISTRY, SPLITS, validate_seeds
from eval.comparison import digest
from eval.ladder import MAX_RATING_AGE_DAYS, STRONG_CUT, opponent_id
from eval.standing import BANDS, LINEAGE_FLOOR, SAFE_TOP20_WIN_RATE

SCHEMA_VERSION = 1
BACKENDS = ('fast', 'official')
EVIDENCE_PROFILES = ('score', 'audit', 'full')
# The sections that say what was measured. Everything here enters the `run_id`.
MATERIAL = ('schema_version', 'agents', 'opponents', 'panel', 'seeds', 'engine', 'limits',
            'metrics')
# Recorded beside it, deliberately outside the identity: how the work was spread.
INCIDENTAL = ('distribution',)


def _hashes(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f'A run spec must name its {label}')
    for name, entry in value.items():
        digest_value = (entry or {}).get('hash')
        if not isinstance(digest_value, str) or len(digest_value) != 64:
            raise ValueError(f'{label} {name!r} carries no sha256; the artifact hash is the '
                             'authority here and a name is not one')
    return True


def validate(spec, *, registry_path=REGISTRY):
    """Every read-only check, all of them before a single seed is admitted.

    Order is the point. `arena/seeds.py` burns reserved validation seeds *before* the
    first callback, and a run refused after that has spent evidence to learn it was
    misconfigured. So everything checkable without mutating anything is checked here, and
    `admit_run` is reached only by a spec that has already passed.
    """
    if spec.get('schema_version') != SCHEMA_VERSION:
        raise ValueError(f'Unsupported run spec schema {spec.get("schema_version")!r}; '
                         f'this module writes and reads {SCHEMA_VERSION}')
    agents = spec.get('agents') or {}
    if set(agents) != {'baseline', 'candidate'}:
        raise ValueError('A run spec names exactly a baseline and a candidate')
    _hashes(agents, 'agent')
    opponents = spec.get('opponents') or {}
    _hashes(opponents, 'opponent')
    if len({opponent_id(name) for name in opponents}) != len(opponents):
        raise ValueError('Ambiguous opponent ids: two entries reduce to the same id')

    seeds = spec.get('seeds') or {}
    values, split = seeds.get('seeds'), seeds.get('split')
    if not isinstance(values, list) or not values or len(set(values)) != len(values):
        raise ValueError('A run spec needs a nonempty list of unique seeds')
    if split not in SPLITS:
        raise ValueError(f'Seed split {split!r} is not one of {", ".join(SPLITS)}')
    # The registry is the authority on what these seeds are; the spec only records what it
    # was told. Asking it here is read-only and does not consume anything.
    observed = validate_seeds(values, split, path=registry_path)
    for field in ('registry_revision', 'registry_sha256'):
        if seeds.get(field) != observed[field]:
            raise ValueError(f'The spec declares {field} {seeds.get(field)!r} and the '
                             f'registry reports {observed[field]!r}; the run would not be '
                             'the experiment the spec names')

    engine = spec.get('engine') or {}
    if engine.get('backend') not in BACKENDS:
        raise ValueError(f'Backend {engine.get("backend")!r} is not one of {", ".join(BACKENDS)}')
    if not engine.get('environment'):
        raise ValueError('A run spec carries the engine fingerprint it was planned under')

    # A paired comparison's population is its `opponents` list, which is already here, so
    # a panel is not required to run one. It is required to *read* one as an absolute
    # standing, and `eval/standing.py` and `eval/dossier.py` are where that is enforced
    # (issue #45). What this refuses is a half-written reference, which would look like a
    # named population and name nothing.
    panel = spec.get('panel')
    if panel is not None and not (panel.get('revision') and panel.get('observed_at')):
        raise ValueError('A panel reference is a revision and an observation date, or nothing')

    limits = spec.get('limits') or {}
    deadline, attempts = limits.get('deadline_seconds'), limits.get('attempts')
    if deadline is not None and not (isinstance(deadline, (int, float)) and deadline > 0):
        raise ValueError('A deadline is a positive number of seconds, or absent')
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ValueError('attempts must be a positive integer; it is retry policy, and '
                         'retry policy changes what a failure means')

    metrics = spec.get('metrics') or {}
    if metrics.get('evidence_profile', 'full') not in EVIDENCE_PROFILES:
        raise ValueError(f'Evidence profile must be one of {", ".join(EVIDENCE_PROFILES)}')
    blocks = metrics.get('min_blocks')
    if not isinstance(blocks, int) or isinstance(blocks, bool) or blocks < 2:
        raise ValueError('min_blocks must be an integer of at least 2')
    if len(values) < blocks:
        raise ValueError(f'{len(values)} seed blocks declared, under the {blocks} this '
                         'spec says the gate reading it requires')
    if not metrics.get('gate'):
        raise ValueError('A run spec names the gate that will consume its result; a run '
                         'whose reader is undeclared can be read by whichever one passes')
    return True


def material(spec):
    """The sections that define what was measured, canonically ordered."""
    return {key: spec[key] for key in MATERIAL if key in spec}


def run_id(spec):
    return digest(material(spec))


def build(*, baseline, candidate, opponents, seeds, split, registry, backend='fast',
          environment=None, panel=None, min_blocks=100, cut=STRONG_CUT,
          max_rating_age_days=MAX_RATING_AGE_DAYS, lineage_floor=LINEAGE_FLOOR,
          safe_top20=SAFE_TOP20_WIN_RATE, gate='eval.submit_gate.decide',
          deadline_seconds=None, attempts=3, distribution=None, registry_path=REGISTRY,
          now=None, evidence_profile='score'):
    """Assemble a spec from what a caller already knows, then validate and address it.

    `baseline`, `candidate` and `opponents` are `{name: sha256}`; opponents may carry a
    lineage as `{name: {'hash': ..., 'lineage': ...}}`. Nothing here consumes a seed.
    """
    def entry(value):
        return dict(value) if isinstance(value, dict) else {'hash': value}
    spec = {
        'schema_version': SCHEMA_VERSION,
        'agents': {'baseline': {'spec': baseline[0], **entry(baseline[1])},
                   'candidate': {'spec': candidate[0], **entry(candidate[1])}},
        'opponents': {name: entry(value) for name, value in sorted(opponents.items())},
        'panel': (None if panel is None else
                  {'revision': panel.get('revision'), 'observed_at': panel.get('observed_at')}),
        'seeds': {'seeds': sorted(seeds), 'split': split,
                  'registry_revision': registry.get('registry_revision'),
                  'registry_sha256': registry.get('registry_sha256')},
        'engine': {'backend': backend,
                   'environment': environment if environment is not None else fingerprint()},
        'limits': {'deadline_seconds': deadline_seconds, 'attempts': attempts},
        'metrics': {'min_blocks': min_blocks, 'cut': cut,
                    'evidence_profile': evidence_profile,
                    'max_rating_age_days': max_rating_age_days,
                    'lineage_floor': lineage_floor, 'safe_top20': safe_top20,
                    'bands': [[name, low, high, threshold]
                              for name, low, high, threshold in BANDS],
                    'gate': gate},
        'distribution': dict(distribution or {'topology': 'local'}),
    }
    validate(spec, registry_path=registry_path)
    return {**spec, 'run_id': run_id(spec),
            'created_at': (now or datetime.now(timezone.utc)).isoformat()}


def differences(left, right):
    """Which material sections two specs disagree on, for a refusal that names the cause."""
    return sorted(key for key in MATERIAL
                  if digest(left.get(key)) != digest(right.get(key)))


def same_run(specs):
    """Refuse to read evidence from two different runs as one experiment."""
    specs = list(specs)
    if not specs:
        raise ValueError('No run spec to combine')
    ids = {spec['run_id'] for spec in specs}
    if len(ids) > 1:
        moved = differences(specs[0], specs[1])
        raise ValueError('These runs are not the same experiment: '
                         + (f'they differ on {", ".join(moved)}' if moved else
                            'their material fields hash differently')
                         + f' ({", ".join(sorted(short(value) for value in ids))})')
    return specs[0]['run_id']


def short(value):
    return '-' * 12 if not isinstance(value, str) or not value else value[:12]


def _declared_run(path, raw):
    """The run a piece of evidence says it belongs to, or None when it says nothing.

    A report declares it in the JSON; a job store declares it in the `run` table
    `arena/jobs.py` binds. Both are read here rather than inferred, because a manifest
    that guesses is the hand-carry it exists to remove.
    """
    if path.suffix == '.json':
        try:
            return (json.loads(raw) or {}).get('run_id')
        except (ValueError, AttributeError):
            return None
    if path.suffix in ('.sqlite3', '.sqlite', '.db'):
        import sqlite3
        try:
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                row = connection.execute('SELECT run_id FROM run WHERE id = 1').fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None
    return None


def file_evidence(path, kind):
    """One piece of evidence, by its bytes and by the run it says it belongs to."""
    path = Path(path)
    raw = path.read_bytes()
    declared = _declared_run(path, raw)
    return {'kind': kind, 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw), 'run_id': declared if isinstance(declared, str) else None}


def manifest(spec, evidence, *, now=None):
    """Tie a spec to the files that are supposed to be its result, or say why not.

    `evidence` is `{kind: path}`. Every piece is hashed, and every piece that declares a
    `run_id` has to declare this one. A piece that declares none is reported as unbound:
    the reports this project wrote before run specs existed are exactly that, they stay
    readable, and no manifest pretends they were bound.
    """
    now = now or datetime.now(timezone.utc)
    pieces = {kind: file_evidence(path, kind) for kind, path in sorted(evidence.items())}
    wrong = sorted(kind for kind, piece in pieces.items()
                   if piece['run_id'] is not None and piece['run_id'] != spec['run_id'])
    unbound = sorted(kind for kind, piece in pieces.items() if piece['run_id'] is None)
    refusals = [f'{kind} declares run {short(pieces[kind]["run_id"])}, not '
                f'{short(spec["run_id"])}; it is evidence from another experiment.'
                for kind in wrong]
    return {'schema_version': SCHEMA_VERSION, 'run_id': spec['run_id'],
            'spec_sha256': digest(spec), 'generated_at': now.isoformat(),
            'spec': spec, 'evidence': pieces, 'unbound': unbound, 'refusals': refusals,
            'verdict': 'BOUND' if not refusals and not unbound else
                       'REFUSED' if refusals else 'PARTIALLY BOUND',
            'note': ('Every number in the bound evidence reconstructs to this spec, its '
                     'job store and the artifacts it names.')}


def render(spec):
    seeds = spec['seeds']
    panel = spec.get('panel') or {}
    lines = [f'RUN {short(spec["run_id"])}  schema {spec["schema_version"]}',
             f'  baseline    {spec["agents"]["baseline"]["spec"]} '
             f'({short(spec["agents"]["baseline"]["hash"])})',
             f'  candidate   {spec["agents"]["candidate"]["spec"]} '
             f'({short(spec["agents"]["candidate"]["hash"])})',
             f'  opponents   {", ".join(sorted(spec["opponents"]))}',
             f'  panel       {short(panel.get("revision")) if panel else "none"}'
             f'{"  observed " + panel["observed_at"] if panel else ""}',
             f'  seeds       {len(seeds["seeds"])} {seeds["split"]} seeds, registry '
             f'r{seeds["registry_revision"]} ({short(seeds["registry_sha256"])})',
             f'  engine      {spec["engine"]["backend"]} backend, '
             f'{short(digest(spec["engine"]["environment"]))}',
             f'  limits      deadline {spec["limits"]["deadline_seconds"]}s, '
             f'{spec["limits"]["attempts"]} attempt(s)',
             f'  metrics     min {spec["metrics"]["min_blocks"]} blocks, cut '
             f'{spec["metrics"]["cut"]:g}, gate {spec["metrics"]["gate"]}',
             f'  spread      {json.dumps(spec.get("distribution") or {}, sort_keys=True)} '
             '(outside the run_id)']
    return '\n'.join(lines) + '\n'


def load(path):
    """A spec from disk, refusing one whose content no longer hashes to its own id."""
    spec = json.loads(Path(path).read_text(encoding='utf-8'))
    if run_id(spec) != spec.get('run_id'):
        raise ValueError(f'{path} was edited after it was written: its material fields hash '
                         f'to {short(run_id(spec))}, not the {short(spec.get("run_id"))} it '
                         'claims. A run id that does not follow its own spec is worse than '
                         'none, because everything downstream trusts it.')
    return spec


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('spec', help='a run spec written by arena.paired or by hand')
    parser.add_argument('--registry', default=str(REGISTRY))
    parser.add_argument('--evidence', action='append', default=[], metavar='KIND=PATH',
                        help='a result file to bind to this spec, e.g. comparison=out/comparison.json')
    parser.add_argument('--output', help='where to write the evidence manifest')
    args = parser.parse_args()
    spec = load(args.spec)
    validate(spec, registry_path=args.registry)
    print(render(spec), end='')
    if not args.evidence:
        return
    pieces = {}
    for item in args.evidence:
        kind, _, path = item.partition('=')
        if not path:
            parser.error(f'--evidence {item!r} must be KIND=PATH')
        pieces[kind] = path
    result = manifest(spec, pieces)
    print(f'\nmanifest = {result["verdict"]}')
    for reason in result['refusals']:
        print('  refuses: ' + reason)
    for kind in result['unbound']:
        print(f'  unbound: {kind} declares no run_id; it predates run specs and is not '
              'bound to this one.')
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2, default=str) + '\n')


if __name__ == '__main__':
    main()
