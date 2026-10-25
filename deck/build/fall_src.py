"""Fall-model numbers for the deck: rule baseline from cv_summary.json, AI model = the SHIPPED config.

The shipped model (app/frontend/public/models/fall_model.json) is v1 logistic regression plus a descent gate.
Its nested-CV numbers live in gate_summary.json under "ur_cv:gate=<min>", where <min> is read from the shipped
JSON, so the deck can never quote a gate value that is not the one in production.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CV = ROOT / "app/backend/experiments/fall/results/cv_summary.json"
GATE = ROOT / "app/backend/experiments/fall/results/opt/gate/gate_summary.json"
FIELD = ROOT / "app/backend/experiments/fall/results/opt/field/field_gate_summary.json"
SHIPPED = ROOT / "app/frontend/public/models/fall_model.json"
SRC = "experiments/fall/results/cv_summary.json (규칙) + opt/gate/gate_summary.json (배포 모델)"


def load() -> tuple[dict[str, Any], str]:
    """Return ({"rule": ..., "logreg": ..., n_fall, n_adl, neg_hours}, gate key used or "")."""
    cv = json.loads(CV.read_text(encoding="utf-8"))
    gate = json.loads(SHIPPED.read_text(encoding="utf-8")).get("descent_gate")
    if not gate or not GATE.is_file():
        return cv, ""
    key = f"ur_cv:gate={gate['min']}"
    gs = json.loads(GATE.read_text(encoding="utf-8"))
    if key not in gs:
        raise KeyError(f"shipped descent_gate.min={gate['min']} has no row {key} in {GATE}")
    out = dict(cv)
    out["logreg"] = gs[key]
    return out, key


def load_field() -> tuple[dict[str, Any], str]:
    """Field-data check of the SHIPPED config (same descent gate as load()): MCFD multi-camera lab set and real CCTV clips (CCTVF).

    Returns ({"mcfd": all-block, "cctvf": all-block}, key). The key is v1_gate=<min> from the shipped fall_model.json.
    """
    gate = json.loads(SHIPPED.read_text(encoding="utf-8")).get("descent_gate")
    key = f"v1_gate={gate['min']}" if gate else "v1_gate=None"
    doc = json.loads(FIELD.read_text(encoding="utf-8"))
    if key not in doc:
        raise KeyError(f"shipped config has no row {key} in {FIELD}")
    return {"mcfd": doc[key]["mcfd"]["all"], "cctvf": doc[key]["cctvf"]["all"]}, key


def export_frontend() -> Path:
    """Write the demo's evidence file (public/models/fall_cv_summary.json) from the same source as the deck."""
    data, key = load()
    out = ROOT / "app/frontend/public/models/fall_cv_summary.json"
    data = {**data, "ai_source": key or "cv_summary.json logreg (no descent gate)"}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(export_frontend())
