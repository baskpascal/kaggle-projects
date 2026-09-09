"""Assemble a shop-router bundle: the public 0909 tapes under a routing table we fitted.

The tapes and every runtime rule stay byte-identical to `yhay81/shop-router-0909`; what
this builder changes is the `SHOP_PLANS` table, which the original leaves keyed only on
pairs containing `YARN_STORE`. The table is written as source so the artifact reads as one
file with no hidden state, and the bundle keeps the upstream `actions.json` and licence
unchanged, which is also what makes the diff against the parent short enough to audit.

    python -m experiments.build_shop_router --table experiments/results/<run>/table.json \
        --output versions/v006

The archive is the submission: `main.py`, `actions.json` and `LICENSE.txt` at the root,
with fixed member metadata and `mtime=0`, so the same inputs always give the same bytes.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

TABLE = re.compile(r'^SHOP_PLANS = \{.*?^\}$', re.MULTILINE | re.DOTALL)
MEMBERS = ('main.py', 'actions.json', 'LICENSE.txt')


def render(table):
    lines = ['SHOP_PLANS = {']
    for pair, plan in sorted(table.items()):
        first, second = pair
        lines.append(f'    ("{first}", "{second}"): {plan},')
    lines.append('}')
    return '\n'.join(lines)


def build(bundle, table, output, *, note=None):
    bundle, output = Path(bundle), Path(output)
    source = (bundle / 'main.py').read_text()
    if not TABLE.search(source):
        raise ValueError('The bundle does not carry a SHOP_PLANS table to replace')
    rewritten = TABLE.sub(lambda _: render(table), source, count=1)
    if note:
        rewritten = rewritten.replace('SHOP_PLANS = {', f'# {note}\nSHOP_PLANS = {{', 1)
    compile(rewritten, 'main.py', 'exec')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'main.py').write_text(rewritten)
    for name in MEMBERS[1:]:
        (output / name).write_bytes((bundle / name).read_bytes())
    payload = {name: (output / name).read_bytes() for name in MEMBERS}
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as handle:
        for name in MEMBERS:
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(payload[name]), 0o644, 0
            handle.addfile(member, io.BytesIO(payload[name]))
    bytes_ = gzip.compress(archive.getvalue(), mtime=0)
    (output / 'main.tar.gz').write_bytes(bytes_)
    return {'members': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
            'archive_sha256': hashlib.sha256(bytes_).hexdigest(),
            'archive_bytes': len(bytes_),
            'routes': len(table)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--bundle', default='opponents/public/yhay_router_0909')
    parser.add_argument('--table', required=True, help='JSON mapping "SHOP|SHOP" to a plan index')
    parser.add_argument('--note')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = json.loads(Path(args.table).read_text())
    table = {tuple(key.split('|')): int(plan) for key, plan in raw.items()}
    print(json.dumps(build(args.bundle, table, args.output, note=args.note), indent=2))


if __name__ == '__main__':
    main()
