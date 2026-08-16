from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_independent_process_restart_rehydrates_scoped_durable_state(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    helper = Path(__file__).with_name("phase9_process_helper.py")
    database_url = f"sqlite:///{tmp_path / 'phase9.sqlite3'}"
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "LANGGRAPH_CHECKPOINT_BACKEND": "sqlite",
            "LANGGRAPH_CHECKPOINT_DB": str(tmp_path / "checkpoints.sqlite3"),
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "EVIDENCE_S3_ENABLED": "false",
            "TAVILY_ENABLED": "false",
            "PYTHONPATH": str(repository_root),
        }
    )
    seeded = subprocess.run(
        [sys.executable, str(helper), "seed"],
        cwd=repository_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert seeded.returncode == 0, seeded.stderr
    state = json.loads(seeded.stdout.strip().splitlines()[-1])
    environment["CT_PHASE9_STATE"] = json.dumps(state)

    verified = subprocess.run(
        [sys.executable, str(helper), "verify"],
        cwd=repository_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout.strip().splitlines()[-1]) == {
        "restart": "PASS",
        "agent_runs": 1,
        "search_sessions": 1,
    }
