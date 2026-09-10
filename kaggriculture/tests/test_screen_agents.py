from experiments.screen_agents import report_labels


def test_parallel_tape_families_do_not_collide():
    assert report_labels(['spa/tape-0/main.py', 'himanshu/tape-0/main.py']) == [
        'spa/tape-0', 'himanshu/tape-0']
