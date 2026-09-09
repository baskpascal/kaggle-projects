import contextlib
import hashlib
import importlib
import importlib.metadata
import io
from pathlib import Path

VERSION = '1.32.7'
INTERPRETER_HASH = 'bc8a54879ef02c7ea64b8b333d6a976f0ea65c4949149d01f463f23bccee653e'


def official():
    with contextlib.redirect_stdout(io.StringIO()):
        module = importlib.import_module('kaggle_environments.envs.kaggriculture.kaggriculture')
    version = importlib.metadata.version('kaggle-environments')
    digest = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    if version != VERSION or digest != INTERPRETER_HASH:
        raise RuntimeError(f'Environment mismatch: version={version}, sha256={digest}; revalidate before changing lock')
    return module


def make_environment(seed, configuration=None, *, verified=False):
    if not verified:
        official()
    from kaggle_environments import make
    return make('kaggriculture', configuration={**(configuration or {}), 'seed': seed}, debug=False)


def fingerprint(*, verified_module=None):
    module = verified_module or official()
    spec = Path(module.__file__).with_suffix('.json')
    return {'version': VERSION, 'interpreter_sha256': INTERPRETER_HASH,
            'specification_sha256': hashlib.sha256(spec.read_bytes()).hexdigest()}
