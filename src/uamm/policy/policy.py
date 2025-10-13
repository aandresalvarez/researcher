from dataclasses import dataclass


@dataclass
class PolicyConfig:
    w1: float = 0.55
    w2: float = 0.45
    tau_accept: float = 0.85
    delta: float = 0.05


def final_score(snne_norm: float, s2: float, cfg: PolicyConfig) -> float:
    return cfg.w1 * (1.0 - snne_norm) + cfg.w2 * s2


def decide(S: float, cfg: PolicyConfig, cp_accept: bool) -> str:
    if cp_accept and S >= cfg.tau_accept:
        return "accept"
    if cfg.tau_accept - cfg.delta <= S < cfg.tau_accept:
        return "iterate"
    return "abstain"
