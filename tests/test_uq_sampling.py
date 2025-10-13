from uamm.uq.sampling import generate_answer_variants


def test_generate_answer_variants_uses_evidence():
    base = "The cohort includes 128 patients and improved adherence."
    question = "How many patients are in the cohort and what changed?"
    evidence = [
        "Clinical summary shows 128 patients enrolled.",
        "Adherence improved year over year.",
    ]
    variants = generate_answer_variants(
        base, question=question, evidence_snippets=evidence, count=5
    )
    assert len(variants) >= 5
    assert variants[0] == base.strip()
    assert any("question" in v.lower() for v in variants[1:])
    assert any("evidence" in v.lower() for v in variants[1:])


def test_generate_answer_variants_handles_missing_input():
    variants = generate_answer_variants("", question="", evidence_snippets=[], count=3)
    assert len(variants) >= 3
    assert all(isinstance(v, str) and v for v in variants)
