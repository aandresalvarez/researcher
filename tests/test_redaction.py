from uamm.security.redaction import redact


def test_redaction_masks_pii():
    text = "Contact john.doe@example.com or +1 (415) 555-1212, SSN 123-45-6789"
    red, changed = redact(text)
    assert changed is True
    assert "example.com" not in red
    assert "555-1212" not in red
    assert "123-45-6789" not in red
    assert "[REDACTED_EMAIL]" in red
    assert "[REDACTED_PHONE]" in red
    assert "[REDACTED_SSN]" in red
