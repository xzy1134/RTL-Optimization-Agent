"""Small serial graph state; raw source and full logs live in artifacts."""
from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class SearchBudget:
    max_candidates: int = 8
    max_rounds: int = 8
    patience: int = 4
    runtime_seconds: float = 60


class AgentState(TypedDict, total=False):
    run_dir: str
    original_path: str
    original_hash: str
    extra_sources: list[str]
    top: str
    eda_config: dict
    config_fingerprint: str | None
    budget: dict
    deadline: float
    started: float
    depth_limit: int | None
    baseline: dict
    baseline_feasible: bool
    baseline_proof: dict
    best: dict | None
    parent: dict
    round_index: int
    candidate_count: int
    passed_count: int
    no_improvement_rounds: int
    attempted_hashes: set[str]
    failures: list[dict]
    history: list[str]
    recommendations: list[dict]
    strategy_attempts: dict[str, int]
    primary_strategy: str
    current: dict
    stop_reason: str | None
    model_usage: dict
    evaluation_seconds: float
    model_seconds: float
    report_path: str
