from __future__ import annotations

from typing import Dict, Iterable, List

from uamm.config.settings import Settings, load_settings
from uamm.evals.suites import list_suites, run_suite
from uamm.evals.storage import store_eval_run


def default_suite_ids() -> List[str]:
    return [suite.id for suite in list_suites()]


def run_suites(
    run_id: str,
    suite_ids: Iterable[str] | None = None,
    *,
    settings: Settings | None = None,
    update_cp_reference: bool = True,
) -> Dict[str, List[Dict[str, any]]]:
    settings = settings or load_settings()
    suite_ids = list(suite_ids or default_suite_ids())
    results: List[Dict[str, any]] = []
    for suite_id in suite_ids:
        result = run_suite(
            suite_id,
            run_id=f"{run_id}:{suite_id}" if len(suite_ids) > 1 else run_id,
            settings=settings,
            update_cp_reference=update_cp_reference,
        )
        summary = {k: v for k, v in result.items() if k != "records"}
        results.append(summary)
        store_eval_run(
            settings.db_path,
            run_id=run_id,
            suite_id=suite_id,
            metrics=summary.get("metrics", {}),
            by_domain=summary.get("by_domain", {}),
            records=result.get("records", []),
            notes={
                "category": summary.get("category"),
                "description": summary.get("description"),
            },
        )
    return {"run_id": run_id, "suites": results}
