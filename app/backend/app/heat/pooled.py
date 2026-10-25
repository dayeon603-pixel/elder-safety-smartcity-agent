"""Read-only access to the pooled (all test years) block of experiments/heat/out/results.json.

The evaluation owns the numbers; this module only reshapes them. Whatever `pooled_<a>_<b>` block a
variant carries is served (the widest span wins if several exist), so a re-run that adds a year needs
no code change. The parsed file is cached by (path, mtime_ns, size) and its sha256 is returned so a
slide number can be traced to the exact file.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import resolve_paths

RESULTS_NAME = "results.json"
POOLED_KEY_RE = re.compile(r"^pooled_(\d{4})_(\d{4})$")
MAIN_BUDGET_KEY = "B1|per_day"  # same-budget-as-B1 daily alerts, the headline budget
BASELINE = "B1"
METHOD_ORDER = ("B1", "B1S", "B2", "HEUR", "OURS")
METRIC_ORDER = ("capture", "precision", "cases_per_alert")


class PooledUnavailable(RuntimeError):
    """The results file is missing, unreadable, or has no pooled block for the variant."""


@dataclass(frozen=True, slots=True)
class _Loaded:
    stamp: tuple[int, int]
    sha256: str
    data: dict[str, Any]


_CACHE: dict[Path, _Loaded] = {}
_LOCK = threading.Lock()


def results_path() -> Path:
    """Location of results.json (KCF_HEAT_OUT_DIR is read per call so tests can redirect it)."""
    return resolve_paths().out_dir / RESULTS_NAME


def _load(path: Path) -> _Loaded:
    try:
        st = path.stat()
    except FileNotFoundError as exc:
        raise PooledUnavailable(f"{path.name} not found") from exc
    stamp = (st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _CACHE.get(path)
        if hit is not None and hit.stamp == stamp:
            return hit
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise PooledUnavailable(f"{path.name} is unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise PooledUnavailable(f"{path.name} is not a JSON object")
    loaded = _Loaded(stamp, hashlib.sha256(raw).hexdigest(), data)
    with _LOCK:
        _CACHE[path] = loaded
    return loaded


def _is_stat(v: Any) -> bool:
    return isinstance(v, dict) and "est" in v


def _delta(block: dict[str, Any], method: str, metric: str) -> dict[str, Any] | None:
    stat = block.get("delta", {}).get(f"{method}-{BASELINE}", {}).get(metric)
    if not _is_stat(stat):
        return None
    return {"value": stat["est"], "ci_lo": stat.get("lo"), "ci_hi": stat.get("hi")}


def pooled_results(variant: str, path: Path | None = None) -> dict[str, Any]:
    """Pooled rows for a variant.

    Raises:
        PooledUnavailable: file missing/invalid, or no pooled block for the variant.
    """
    loaded = _load(path or results_path())
    var = loaded.data.get("variants", {}).get(variant)
    if not isinstance(var, dict):
        raise PooledUnavailable(f"no results for variant {variant}")
    spans = {k: (int(m[2]) - int(m[1]), int(m[2])) for k in var if (m := POOLED_KEY_RE.match(k))}
    if not spans:
        raise PooledUnavailable(f"no pooled block for variant {variant}")
    key = max(spans, key=lambda k: spans[k])
    block = var[key].get(MAIN_BUDGET_KEY)
    if not isinstance(block, dict) or "methods" not in block:
        raise PooledUnavailable(f"pooled block {key} lacks {MAIN_BUDGET_KEY}")
    first, last = int(key[7:11]), int(key[12:16])
    methods: dict[str, Any] = block["methods"]
    rows: list[dict[str, Any]] = []
    for method in sorted(methods, key=lambda m: METHOD_ORDER.index(m) if m in METHOD_ORDER else len(METHOD_ORDER)):
        stats = {k: v for k, v in methods[method].items() if _is_stat(v)}
        for metric in sorted(stats, key=lambda m: METRIC_ORDER.index(m) if m in METRIC_ORDER else len(METRIC_ORDER)):
            s = stats[metric]
            rows.append({
                "method": method, "metric": metric, "value": s["est"], "ci_lo": s.get("lo"), "ci_hi": s.get("hi"),
                "delta_vs_b1": None if method == BASELINE else _delta(block, method, metric),
            })
    return {
        "variant": variant, "pooled_key": key, "test_years": list(range(first, last + 1)),
        "results_sha256": loaded.sha256, "rows": rows,
    }
