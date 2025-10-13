import json
import logging
from typing import Any, Dict


_logger = logging.getLogger("uamm.step")


def log_step(payload: Dict[str, Any]) -> None:
    """Log a single step as JSON. Do not include PHI/PII text."""
    try:
        _logger.info(json.dumps(payload, separators=(",", ":")))
    except Exception:
        # best effort; avoid raising from logging
        pass
