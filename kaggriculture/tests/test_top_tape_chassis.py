import json

from experiments.top_tape_chassis import build


def test_generated_chassis_is_standalone_and_tape_identified(tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    parent.joinpath('main.py').write_text(
        'import copy\nimport json\nfrom pathlib import Path\n\n'
        'class Policy:\n'
        ' def __init__(self, folder):\n'
        '  self.tapes = json.loads((Path(folder) / "actions.json").read_text())\n'
        'def agent(o,c=None): return {}\n')
    action = {'farmer': ['PASS'], 'hands': [], 'market': []}
    tape = {'actions': [action] * 719, 'episode': 1, 'seat': 0, 'team': 'x',
            'money': 1, 'sha256': 'tape'}
    report = build(tape, parent, tmp_path / 'out')
    source = (tmp_path / 'out/main.py').read_text()
    assert 'actions.json' not in source
    assert not (tmp_path / 'out/actions.json').exists()
    assert report['main_sha256']
