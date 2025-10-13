from uamm.policy.cp import ConformalGate


def test_cp_gate_disabled_accepts_all():
    g = ConformalGate(enabled=False)
    assert g.accept(0.0) is True
    assert g.accept(1.0) is True


def test_cp_gate_with_threshold():
    # supplier returns 0.7
    g = ConformalGate(enabled=True, threshold_supplier=lambda: 0.7)
    assert g.accept(0.69) is False
    assert g.accept(0.7) is True
    assert g.accept(0.9) is True
