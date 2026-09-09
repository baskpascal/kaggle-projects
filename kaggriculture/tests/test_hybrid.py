import numpy as np
import pytest

from arena.hybrid import (FEATURE_COUNT, ReplayBuffer, evaluate_model,
                          samples_from_rows)


def rows():
    return [
        {'money': 120_000, 'opponent_money': 80_000, 'seat': 0, 'steps': 719,
         'failures': [], 'opponent_failures': [], 'score': 1., 'seed': 10,
         'job_id': 'a', 'hostname': 'pc-a'},
        {'money': 70_000, 'opponent_money': 90_000, 'seat': 1, 'steps': 719,
         'failures': [], 'opponent_failures': ['x'], 'score': 0., 'seed': 10,
         'job_id': 'b', 'hostname': 'pc-b'},
    ]


def test_match_rows_become_small_float32_value_samples():
    features, targets, origins = samples_from_rows(rows())
    assert features.shape == (2, FEATURE_COUNT)
    assert features.dtype == targets.dtype == np.float32
    assert targets.tolist() == [1., 0.]
    assert origins[1] == {'job_id': 'b', 'seed': 10, 'seat': 1, 'hostname': 'pc-b'}


def test_replay_buffer_checkpoints_and_resumes_atomically(tmp_path):
    path = tmp_path / 'nested' / 'replay.npz'
    features, targets, origins = samples_from_rows(rows())
    buffer = ReplayBuffer(path)
    buffer.append(features, targets, origins)
    buffer.append(features, targets, origins)
    resumed = ReplayBuffer(path)
    assert resumed.features.shape == (4, FEATURE_COUNT)
    assert resumed.targets.tolist() == [1., 0., 1., 0.]
    train_x, train_y, eval_x, eval_y = resumed.split(.25)
    assert len(train_x) == len(train_y) == 3
    assert len(eval_x) == len(eval_y) == 1
    assert not path.with_name(path.name + '.tmp').exists()


def test_replay_buffer_refuses_misaligned_samples(tmp_path):
    buffer = ReplayBuffer(tmp_path / 'replay.npz')
    with pytest.raises(ValueError, match='equal lengths'):
        buffer.append(np.zeros((2, FEATURE_COUNT)), np.zeros(1), [{}, {}])


def test_cpu_evaluation_needs_no_torch():
    features = np.zeros((2, FEATURE_COUNT), dtype=np.float32)
    result = evaluate_model({'weight': [0.] * FEATURE_COUNT, 'bias': 0.},
                            features, np.asarray([0., 1.], dtype=np.float32))
    assert result == {'mse': .25, 'accuracy': .5, 'samples': 2}
