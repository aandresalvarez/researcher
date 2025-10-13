import pytest
from uamm.tools.math_eval import math_eval


def test_math_eval_basic():
    assert math_eval("1+1") == 2.0
    assert pytest.approx(math_eval("sqrt(4)"), 1e-6) == 2.0


def test_math_eval_disallowed():
    with pytest.raises(ValueError):
        math_eval("__import__('os').system('echo hi')")
