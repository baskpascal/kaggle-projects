"""Import an exact standalone ``%%writefile main.py`` cell with pinned provenance."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile


def build(notebook, output, expected_sha256, source_ref):
    payload = json.loads(Path(notebook).read_text(encoding='utf-8'))
    matches = []
    for cell in payload.get('cells', []):
        source = ''.join(cell.get('source', []))
        if source.startswith('%%writefile main.py\n'):
            matches.append(source.split('\n', 1)[1])
    if len(matches) != 1:
        raise ValueError(f'expected one %%writefile main.py cell, found {len(matches)}')
    source = matches[0]
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f'notebook main.py hash {digest} != pinned {expected_sha256}')
    compile(source, 'main.py', 'exec')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'main.py').write_text(source, encoding='utf-8')

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo('main.py')
        member.size = len(source.encode())
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(source.encode()))
    archive = gzip.compress(stream.getvalue(), mtime=0)
    (output / 'main.tar.gz').write_bytes(archive)
    manifest = {
        'version': output.name,
        'kind': 'exact_public_notebook_agent',
        'source': source_ref,
        'source_main_sha256': digest,
        'main_sha256': digest,
        'archive_sha256': hashlib.sha256(archive).hexdigest(),
        'license': 'Apache-2.0 (embedded verbatim in main.py)',
        'importer': 'experiments/import_notebook_agent.py',
        'decision': 'candidate only; Kaggle submission requires the local 2950 gate',
    }
    (output / 'main.manifest.json').write_text(
        json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--notebook', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.notebook, args.output, args.sha256, args.source), indent=2))


if __name__ == '__main__':
    main()
