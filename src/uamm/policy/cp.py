import logging
from typing import Optional, Callable


class ConformalGate:
    """Bootstrap CP gate (PRD §7.10, §7.12).

    If disabled, always returns True. If enabled and a threshold supplier is provided,
    compares S against τ supplied for the current domain.
    """

    def __init__(
        self,
        enabled: bool = False,
        threshold_supplier: Optional[Callable[[], Optional[float]]] = None,
    ):
        self.enabled = enabled
        self._get_tau = threshold_supplier
        self._last_reason: Optional[str] = None

    def accept(self, S: float) -> bool:
        if not self.enabled:
            self._last_reason = "disabled"
            return True
        tau = self._get_tau() if self._get_tau is not None else None
        if tau is None:
            self._last_reason = "missing_tau"
            logging.getLogger("uamm.cp").warning("cp_gate_missing_threshold")
            return False
        self._last_reason = None
        return S >= tau

    @property
    def last_reason(self) -> Optional[str]:
        return self._last_reason
