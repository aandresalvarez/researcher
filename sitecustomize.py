"""
Global site customization for the UAMM project.

Purpose
-------
- Silence a noisy upstream FastAPI compatibility warning that appears on
  Python 3.14+ when FastAPI imports the Pydantic v1 compatibility layer.

Context
-------
- UAMM targets Pydantic v2 APIs in its own codebase.
- FastAPI (>=0.115) still imports `pydantic.v1` internally for compatibility,
  which raises the following UserWarning under Python 3.14+:
    "Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater."

This filter is narrowly scoped to that specific warning from FastAPI's
compatibility module, and does not affect other warnings.
"""

from __future__ import annotations

import warnings

# Ignore only FastAPI's Pydantic v1 shim warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    message=(
        r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 or greater\."
    ),
    category=UserWarning,
    module=r"fastapi\._compat\.v1",
)
