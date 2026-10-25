"""Overnight 07-16/17 case: 당진·아산·서산·예산 etc. Hourly accumulations vs first rescue calls. Observed-accumulation proxy."""
import numpy as np, pandas as pd
from analyze_hourly import build, fire, CRIT
from common import OUT
from hourly_data import T0

sgg = pd.read_csv(OUT / "sgg_table.csv", dtype={"sgg_cd": str})
day = pd.read_csv(OUT / "sgg_day.csv", dtype={"sgg_cd": str}, parse_dates=["date"])
hr = pd.read_csv(OUT / "sgg_hour.csv", dtype={"sgg_cd": str})
W, qc, F, info, Y, Y3, codes = build(sgg, day, hr)
a, b = (pd.Timestamp("2025-07-16") - T0).days * 24, (pd.Timestamp("2025-07-21") - T0).days * 24
rows = []
for nm in ["당진시", "아산시", "서산시", "예산군", "산청군", "홍성군", "태안군", "김포시"]:
    i = int(sgg.index[sgg.sgg == nm][0])
    Fi = {k: v[a:b, i] for k, v in F.items()}
    adv = fire({k: Fi[k][:, None] for k in Fi}, CRIT["adv"])[:, 0]
    wrn = fire({k: Fi[k][:, None] for k in Fi}, CRIT["warn"])[:, 0]
    y = Y[a:b, i]
    t = lambda m: (T0 + pd.Timedelta(hours=a + int(np.argmax(m)))).strftime("%m-%d %H:00") if m.any() else "never"  # noqa: E731
    first_adv = int(np.argmax(adv)) if adv.any() else None
    rows.append(dict(sgg=nm, rescues=int(y.sum()), first_rescue=t(y > 0), first_adv_proxy=t(adv), first_warn_proxy=t(wrn),
                     rescues_before_adv=int(y[:first_adv].sum()) if first_adv is not None else int(y.sum()),
                     max_R3=round(np.nanmax(Fi["R3"]), 1), max_R6=round(np.nanmax(Fi["R6"]), 1), max_R12=round(np.nanmax(Fi["R12"]), 1),
                     max_R24=round(np.nanmax(Fi["R24"]), 1)))
r = pd.DataFrame(rows)
r.to_csv(OUT / "case_0716_17.csv", index=False)
print(r.to_string(index=False))
