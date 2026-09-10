import importlib.util

from experiments.deb_overlays import build


def test_builds_an_importable_passthrough(tmp_path):
    parent = tmp_path / 'source.py'
    parent.write_text("def agent(obs):\n return {'farmer':['PASS'],'hands':[],'market':[]}\n")
    bundle = tmp_path / 'bundle'
    result = build(parent, bundle, room_guard=True, return_from=712)
    spec = importlib.util.spec_from_file_location('overlay_test', bundle / 'main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.agent({'step': 0}) == {'farmer': ['PASS'], 'hands': [], 'market': []}
    assert result['room_guard'] is True
