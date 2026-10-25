"""Data layer for the residential-fire casualty targeting experiment (module S4, 영점 infra).

Unit = stable 시군구 key across 2020-2024 (renames/merges handled in `unit_key`).
Backtests are hypotheses, not proofs: nothing here is a claim about deployed performance.
Return convention: counts only (no returns); all targets are next-year residential (주거) casualties = 사망+부상.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("fire")

SEED = 42
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
FIG_DIR = HERE / "figs"
CACHE = Path("/Users/chloekang/kcf/data/cache")
FIRE_CSV = CACHE / "a_disaster_safety/kr_15044003_fire_incidents_2024.csv"
POP_CSV = CACHE / "extra/mois_pop/sgg_pop_dec_snapshots.csv"
YEARS = [2020, 2021, 2022, 2023, 2024]
TEST_YEARS = [2022, 2023, 2024]
K_FRACS = (0.10, 0.20)
MIN_HIST_FIRES = 10  # minimum-count rule: fewer residential fires in the history window -> flag 자료 부족
SINGLE_HOUSE = ("단독주택", "기타주택")  # 중분류 other than 공동주택 (apartments)
N_BOOT = 2000

_SIDO = {"강원도": "강원특별자치도", "전라북도": "전북특별자치도"}


def set_seed(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def unit_key(sido: str, sgg: str) -> str:
    """Stable 시군구 key. Handles: 강원/전북 name changes, 군위군 (경북 -> 대구 2023), 부천 구 split (2024), 세종."""
    sido = _SIDO.get(sido, sido)
    s = sgg.replace(" ", "")
    if "군위" in s:
        return "대구광역시|군위군"
    if s.startswith("부천시"):
        return "경기도|부천시"
    if sido == "세종특별자치시":
        return "세종특별자치시|세종"
    return f"{sido}|{s}"


def load_fires() -> pd.DataFrame:
    """Residential (주거) fires, one row per fire, with unit key, year, month, hour and casualties."""
    d = pd.read_csv(FIRE_CSV, encoding="cp949")
    d["ts"] = pd.to_datetime(d["화재발생년원일"], format="%Y-%m-%d %H:%M", errors="coerce")
    n_bad = int(d["ts"].isna().sum())
    assert n_bad == 0, f"unparsed timestamps: {n_bad}"
    d["unit"] = [unit_key(a, b) for a, b in zip(d["시도"], d["시군구"])]
    d["year"] = d.ts.dt.year
    d["month"] = d.ts.dt.month
    d["hour"] = d.ts.dt.hour
    d["cas"] = d["사망"].fillna(0) + d["부상"].fillna(0)
    d["res"] = d["장소대분류"] == "주거"
    d["single"] = d["장소중분류"].isin(SINGLE_HOUSE)
    return d


def load_pop() -> pd.DataFrame:
    """Unit x Dec-year population and 65+. Snapshot 'YYYY-12' is known at the start of year YYYY+1."""
    p = pd.read_csv(POP_CSV, encoding="utf-8-sig")
    p["year"] = p.snapshot.str[:4].astype(int)
    parts = p.name.str.split(" ", n=1, expand=True)
    p["unit"] = [
        unit_key(a, b) if isinstance(b, str) else unit_key(a, "세종" if a.startswith("세종") else a)
        for a, b in zip(parts[0], parts[1])
    ]
    p = p.sort_values(["unit", "year", "code"]).drop_duplicates(["unit", "year"], keep="first")
    return p[["unit", "year", "pop", "pop65"]]


def build_panel() -> pd.DataFrame:
    """Unit x year panel: residential fires, casualties, single-house fires, plus Dec(year) pop. Zero-filled."""
    f = load_fires()
    units = sorted(f.unit.unique())
    r = f[f.res]
    g = r.groupby(["unit", "year"]).agg(fires=("cas", "size"), cas=("cas", "sum"), single=("single", "sum"),
                                         deaths=("사망", "sum"))
    idx = pd.MultiIndex.from_product([units, YEARS], names=["unit", "year"])
    panel = g.reindex(idx, fill_value=0).reset_index()
    p = load_pop()
    panel = panel.merge(p, on=["unit", "year"], how="left")
    miss = panel[panel["pop"].isna()]
    assert miss.empty, f"population missing for {miss.unit.unique()[:10]}"
    return panel


def dump_json(obj: object, path: Path) -> None:
    def default(o: object) -> object:
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=default))
