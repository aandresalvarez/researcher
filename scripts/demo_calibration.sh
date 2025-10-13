PYTHONPATH=src python scripts/run_demo_evals.py tests/data/demo_eval_dataset.json demo-run
PYTHONPATH=src python scripts/import_cp_artifacts.py --input tests/data/demo_calibration.json --domain-field domain --run-id demo-calib
