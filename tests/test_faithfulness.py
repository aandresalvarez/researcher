from uamm.verification.faithfulness import extract_claims, compute_faithfulness


def test_extract_claims_basic():
    text = "Protein X causes Y in mice. See the 2019 study [1]. However, Z remains unproven!"
    claims = extract_claims(text)
    # Should extract non-trivial sentences
    assert any("Protein X" in c for c in claims)
    assert any("However" in c for c in claims)


def test_compute_faithfulness_alignment():
    answer = (
        "Protein X causes Y in mice according to Smith 2019."
        " The telescope has 100 members and includes several cities."
    )
    pack = [
        {
            "snippet": "Smith 2019 reports that Protein X causes Y in mice.",
            "title": "Protein study",
        },
        {"snippet": "The observatory partners include labs in five cities."},
    ]
    out = compute_faithfulness(answer, pack, threshold=0.2)
    assert out["claim_count"] >= 2
    # First claim supported, second likely not with these snippets
    assert out["supported_count"] >= 1
    assert out["supported_count"] <= out["claim_count"]
    assert isinstance(out["score"], float) or out["score"] is None
