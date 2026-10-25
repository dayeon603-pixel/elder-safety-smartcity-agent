"""Per-구 day vs night 'walk-4-minute' coverage tables from out/cov.pkl (primary schedule treatment, w_main weights)."""
from __future__ import annotations

import pickle

import numpy as np
import pandas as pd

import w4lib as L


def table(res: dict, sc: str = "primary", scheme: str = "w_main") -> pd.DataFrame:
    rows = []
    gus = res["gus"]
    for rk in res["radii"]:
        k = res["radii"].index(rk)
        for kind in ("p65", "ptot"):
            j = res["names"].index((scheme, kind)); den = res["denom"][j]
            cov = res["cov"][sc][:, :, k, j, :]  # dt,h,gu
            st = res["static"][k, j]
            for g in range(26):
                if g < 25:
                    d_, c_, s_ = den[g], cov[:, :, g], st[g]
                else:
                    d_, c_, s_ = den.sum(), cov.sum(axis=2), st.sum()
                rows.append({"radius": rk, "kind": kind, "gu": gus[g] if g < 25 else "서울 전체", "pop": d_,
                             "static_all_open": s_ / d_,
                             "wd_day": c_[0, L.DAY].mean() / d_, "wd_night": c_[0, L.NIGHT].mean() / d_,
                             "we_day": c_[1:3][:, L.DAY].mean() / d_, "we_night": c_[1:3][:, L.NIGHT].mean() / d_,
                             "hol_night": c_[3, L.NIGHT].mean() / d_})
    t = pd.DataFrame(rows); t["wd_drop_pp"] = (t.wd_day - t.wd_night) * 100
    return t


if __name__ == "__main__":
    res = pickle.load(open(L.OUT / "cov.pkl", "rb"))
    allt = pd.concat([table(res, sc).assign(scenario=sc) for sc in ("primary", "upper", "asreport")])
    allt.to_csv(L.OUT / "gu_table.csv", index=False, float_format="%.4f")
    t = table(res); p = t[(t.radius == L.PRIMARY) & (t.kind == "p65")].sort_values("wd_night")
    print(p.round(3).to_string(index=False))
    # sensitivity to population weighting (city, 65+, primary)
    for sch in ("w_main", "w_resonly", "w_foot", "w_uni"):
        for rk in (L.PRIMARY, "v1.2_4min"):
            q = table(res, "primary", sch); q = q[(q.gu == "서울 전체") & (q.kind == "p65") & (q.radius == rk)].iloc[0]
            print(sch, rk, "static %.3f day %.3f night %.3f we_night %.3f" % (q.static_all_open, q.wd_day, q.wd_night, q.we_night))
