"""Build a top-replay opening followed by the repository's reactive planner."""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import zlib


TEMPLATE = '''"""Top opening / reactive planner experiment. {provenance}"""
import base64
import json
import zlib
from agent.planner import policy

OPENING = json.loads(zlib.decompress(base64.b85decode({blob!r})))
PARAMETERS = json.loads({parameters!r})

def agent(observation, configuration=None):
    turn = observation.get('step')
    if turn is None:
        turn = int(observation.get('day', 0)) * 24 + int(observation.get('hour', 0))
    if 0 <= turn < len(OPENING):
        return OPENING[turn]
    return policy(observation, configuration, PARAMETERS)
'''


def select_opening(library, team, steps):
    rows = [row for row in library['tapes'] if row.get('team') == team
            and len(row.get('actions', ())) >= steps]
    if not rows:
        raise ValueError(f'no complete opening for {team!r}')
    groups = {}
    for row in rows:
        digest = hashlib.sha256(json.dumps(
            row['actions'][:steps], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        groups.setdefault(digest, []).append(row)
    digest, family = max(groups.items(), key=lambda item: (
        len(item[1]), max(row['money'] for row in item[1]), item[0]))
    return family[0]['actions'][:steps], {
        'team': team, 'steps': steps, 'support': len(family),
        'available_tapes': len(rows), 'opening_sha256': digest,
    }


def build(library, team, steps, parameters, output):
    opening, provenance = select_opening(library, team, steps)
    blob = base64.b85encode(zlib.compress(json.dumps(
        opening, separators=(',', ':')).encode(), 9)).decode()
    source = TEMPLATE.format(blob=blob, parameters=json.dumps(parameters, sort_keys=True),
                             provenance=json.dumps(provenance, sort_keys=True))
    compile(source, str(output), 'exec')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding='utf-8')
    provenance['parameters'] = parameters
    provenance['artifact_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', required=True)
    parser.add_argument('--team', required=True)
    parser.add_argument('--steps', type=int, required=True)
    parser.add_argument('--parameters', default='{}', help='JSON planner overrides')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = build(json.loads(Path(args.library).read_text()), args.team, args.steps,
                   json.loads(args.parameters), args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
