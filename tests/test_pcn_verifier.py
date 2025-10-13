from uamm.pcn.verification import PCNVerifier


def test_math_verification_passes():
    verifier = PCNVerifier()
    pending = verifier.register(
        "tok1", policy={"tolerance": 0.0}, provenance={"expr": "2+2"}
    )
    assert pending["type"] == "pcn_pending"
    event = verifier.verify_math("tok1", expr="2+2", observed_value=4)
    assert event["type"] == "pcn_verified"
    assert verifier.value_for("tok1") == "4"


def test_math_verification_fails_on_mismatch():
    verifier = PCNVerifier()
    verifier.register("tok2", policy={"tolerance": 0.0}, provenance={"expr": "2+2"})
    event = verifier.verify_math("tok2", expr="2+2", observed_value=5)
    assert event["type"] == "pcn_failed"
    assert verifier.value_for("tok2") is None
    assert verifier.status_for("tok2") == "failed"
