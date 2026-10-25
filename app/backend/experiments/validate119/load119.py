"""Load cached 119 구급활동 records (Seoul 2024). No API calls. Backtests/validation are hypotheses, not proofs."""
from __future__ import annotations
import glob, gzip, json
from pathlib import Path
import pandas as pd

ACT = Path("/Users/chloekang/kcf/data/cache/extra/ems/api/act")


def load_act() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(ACT / "*.json.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            x = json.load(fh)
        for it in x["items"]:
            it["_file"] = Path(f).name
            rows.append(it)
    return pd.DataFrame(rows)
