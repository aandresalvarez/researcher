from uamm.uq.snne import normalize


def test_snne_normalize_bounds():
    assert 0.0 <= normalize(0.0) <= 1.0
    assert normalize(-100.0) > 0.5
    assert normalize(100.0) < 0.5
