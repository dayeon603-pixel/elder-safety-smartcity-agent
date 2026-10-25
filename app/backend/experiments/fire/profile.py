"""Descriptive 계절·시간 profile of residential fires (not a forecast; for 안내 timing). Writes out/profile.json."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import OUT_DIR, SEED, dump_json, load_fires  # noqa: E402

NIGHT = lambda h: (h >= 22) | (h < 6)  # noqa: E731


def wilson(k: float, n: float, z: float = 1.96) -> list[float]:
    if n == 0:
        return [float("nan")] * 2
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [float(c - h), float(c + h)]


def main() -> None:
    f = load_fires()
    r = f[f.res].copy()
    r["night"] = NIGHT(r.hour)
    r["winter"] = r.month.isin([12, 1, 2])
    r["any_cas"] = r.cas > 0
    out: dict = {"n_res_fires": len(r), "n_cas": float(r.cas.sum()), "deaths": float(r["사망"].sum()), "years": "2020-2024",
                 "note": "Descriptive. No age field in the fire file, so elderly share is not observable here."}
    for name, g in [("all", r), ("winter", r[r.winter]), ("non_winter", r[~r.winter])]:
        out[f"night_share_fires_{name}"] = {"est": float(g.night.mean()), "ci95": wilson(g.night.sum(), len(g))}
        for key, sub in [("night", g[g.night]), ("day", g[~g.night])]:
            out[f"cas_per_100fires_{name}_{key}"] = {"est": float(100 * sub.any_cas.mean()), "n": len(sub),
                                                      "ci95": [100 * v for v in wilson(sub.any_cas.sum(), len(sub))]}
            out[f"deaths_per_1000fires_{name}_{key}"] = {"est": float(1000 * (sub["사망"] > 0).mean()), "n": len(sub),
                                                         "ci95": [1000 * v for v in wilson((sub["사망"] > 0).sum(), len(sub))]}
    m = r.groupby("month").agg(fires=("cas", "size"), cas=("cas", "sum"), cas_fires=("any_cas", "sum"), deaths=("사망", "sum"))
    m["cas_fire_rate"] = m.cas_fires / m.fires
    m["rate_ci"] = [wilson(a, b) for a, b in zip(m.cas_fires, m.fires)]
    h = r.groupby("hour").agg(fires=("cas", "size"), cas=("cas", "sum"), cas_fires=("any_cas", "sum"), deaths=("사망", "sum"))
    h["cas_fire_rate"] = h.cas_fires / h.fires
    h["rate_ci"] = [wilson(a, b) for a, b in zip(h.cas_fires, h.fires)]
    out["by_month"] = m.reset_index().to_dict("records")
    out["by_hour"] = h.reset_index().to_dict("records")
    # winter-night window: share of all residential casualties/deaths
    wn = r[r.winter & r.night]
    out["winter_night_window"] = {"share_of_fires": len(wn) / len(r), "share_of_cas": float(wn.cas.sum() / r.cas.sum()),
                                  "share_of_deaths": float(wn["사망"].sum() / r["사망"].sum()),
                                  "window_share_of_hours": float((3 / 12) * (8 / 24)),
                                  "deaths_ci95": wilson(wn["사망"].sum(), r["사망"].sum())}
    hm = r.pivot_table(index="month", columns="hour", values="cas", aggfunc="sum", fill_value=0)
    hm.to_csv(OUT_DIR / "profile_month_hour_casualties.csv", encoding="utf-8-sig")
    dump_json(out, OUT_DIR / "profile.json")
    print({k: v for k, v in out.items() if not k.startswith("by_")})


if __name__ == "__main__":
    main()
