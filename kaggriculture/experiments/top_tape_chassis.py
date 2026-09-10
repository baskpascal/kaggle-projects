"""Place an elite replay stream inside the public yhay recovery chassis."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import zlib


def select(library, team, count):
    tapes = [row for row in library['tapes'] if row.get('team') == team
             and len(row.get('actions', ())) == 719]
    return sorted(tapes, key=lambda row: (row['money'], row['sha256']), reverse=True)[:count]


def build(tape, parent, output):
    parent, output = Path(parent), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Make the executable identity include the tape.  A sibling actions.json is
    # part of a Kaggle bundle but arena.agent_hash historically hashes main.py;
    # inlining prevents different candidates from sharing a misleading hash and
    # makes Ray transport roughly two orders of magnitude smaller.
    actions = [tape['actions'] for _ in range(13)]
    blob = base64.b85encode(zlib.compress(
        json.dumps(actions, separators=(',', ':')).encode(), 9)).decode()
    source = (parent / 'main.py').read_text(encoding='utf-8')
    source = source.replace('import copy\nimport json\n',
                            'import base64\nimport copy\nimport json\nimport zlib\n')
    needle = 'self.tapes = json.loads((Path(folder) / "actions.json").read_text())'
    replacement = f'self.tapes = json.loads(zlib.decompress(base64.b85decode({blob!r})))'
    if source.count(needle) != 1:
        raise ValueError('Parent chassis does not contain the expected actions loader')
    source = source.replace(needle, replacement)
    (output / 'main.py').write_text(source, encoding='utf-8')
    # Remove an artifact left by an older build of this same generated directory.
    (output / 'actions.json').unlink(missing_ok=True)
    if (parent / 'LICENSE.txt').exists():
        shutil.copyfile(parent / 'LICENSE.txt', output / 'LICENSE.txt')
    return {
        'episode': tape['episode'], 'seat': tape['seat'], 'team': tape['team'],
        'money': tape['money'], 'tape_sha256': tape['sha256'],
        'main_sha256': hashlib.sha256((output / 'main.py').read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--team', required=True)
    parser.add_argument('--count', type=int, default=8)
    parser.add_argument('--parent', default='opponents/public/yhay_router_0909')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    tapes = select(json.loads(Path(args.library).read_text()), args.team, args.count)
    reports = []
    for index, tape in enumerate(tapes):
        reports.append(build(tape, args.parent, Path(args.output) / f'tape-{index}'))
    print(json.dumps(reports, indent=2))


if __name__ == '__main__':
    main()
