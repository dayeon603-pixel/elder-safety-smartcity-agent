"""Step 1: flood rescues (침수/고립/산사태) -> 시군구 x date, 시군구 x hour, storm episodes.

Run: uv run --project /Users/chloekang/kcf/app/backend python parse_rescue.py
Evidence: all counts are [measured] from rescue_2025.csv. n = episodes, not rows.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from common import FLOOD_TYPES, LOG, OUT, RESCUE_CSV, load_dong_index, load_sgg_table, norm_sgg, sido_group

EP_THRESHOLD = 40  # national flood rescues/day to count as a storm day (config; see sensitivity in report)
EP_GAP_DAYS = 1     # days with count < threshold allowed inside one episode (merge 07-17..07-20 style runs)


def load_flood() -> pd.DataFrame:
    d = pd.read_csv(RESCUE_CSV, encoding="cp949", dtype=str)
    need = ["신고년월일", "신고시각", "발생장소_시", "발생장소_구", "사고원인", "사고원인코드명_사고종별"]
    missing = set(need) - set(d.columns)
    if missing:
        raise SystemExit(f"schema drift, missing {missing}")
    LOG.info("rows %d", len(d))
    cats = d["사고원인코드명_사고종별"]
    print("flood-related 종별 candidates found:\n", cats[cats.str.contains("침수|고립|산사태|수난", na=False)].value_counts().to_string())
    f = d[cats.isin(FLOOD_TYPES)].copy()
    # require the parent 사고원인 to match (guards against a same-named 종별 under another cause)
    ok = f["사고원인코드명_사고종별"].map(FLOOD_TYPES) == f["사고원인"]
    print(f"parent-cause mismatch rows dropped: {(~ok).sum()}")
    f = f[ok]
    f["date"] = pd.to_datetime(f["신고년월일"], errors="coerce")
    hr = pd.to_numeric(f["신고시각"].str.slice(0, 2), errors="coerce")
    f = f.assign(hour=hr)
    bad = f["date"].isna() | f["hour"].isna()
    print(f"bad date/hour rows dropped: {bad.sum()}")
    f = f[~bad].copy()
    f["hour"] = f["hour"].astype(int)
    f["key"] = f["발생장소_시"].map(sido_group) + "|" + f["발생장소_구"].fillna("").map(norm_sgg)
    return f


def crosswalk(f: pd.DataFrame, sgg: pd.DataFrame) -> pd.DataFrame:
    """Exact name match on (sido group, 시군구 sans spaces) to 2026 codes, then a documented manual fallback."""
    valid = set(sgg["key"])
    # fallback: 구 that exist only at 시 level in 2026 boundaries (e.g. 수원시팔달구 present; 시 w/o 구 rows)
    f = f.copy()
    f["match"] = f["key"].where(f["key"].isin(valid))
    # fallback 1: rescue reports a 시 (e.g. 인천광역시|중구 renamed) -> unique name within sido in 2026
    un = f[f["match"].isna()]
    fixes = {}
    names = sgg.assign(nm=sgg["sgg"].map(norm_sgg))
    for k in un["key"].unique():
        sido, nm = k.split("|")
        cand = names[(names["sido"].map(sido_group) == sido) & (names["nm"].str.startswith(nm) | names["nm"].map(nm.startswith))]
        if len(cand) == 1:
            fixes[k] = cand["key"].iloc[0]
    f["match"] = f["match"].fillna(f["key"].map(fixes))
    f = f.merge(sgg[["key", "sgg_cd"]].rename(columns={"key": "match"}), on="match", how="left")
    # fallback 2: 2026 reorganisations (인천 중/서/동구 split, 화성시 -> 4 구, 세종) resolved by 읍면동 name,
    # accepted only if the (sido, 읍면동) pair is unique in the 2026 boundaries.
    di = load_dong_index()
    uniq = di.groupby(["sg", "dong"])["sgg_cd"].agg(["nunique", "first"]).reset_index()
    uniq = uniq[uniq["nunique"] == 1].rename(columns={"first": "sgg_cd_dong"})
    m = f["sgg_cd"].isna()
    sub = f.loc[m, ["key", "발생장소_동"]].copy()
    sub["sg"] = sub["key"].str.split("|").str[0]
    sub = sub.merge(uniq[["sg", "dong", "sgg_cd_dong"]], left_on=["sg", "발생장소_동"], right_on=["sg", "dong"], how="left")
    f.loc[m, "sgg_cd"] = sub["sgg_cd_dong"].to_numpy()
    print(f"dong-level fallback resolved {int(m.sum() - f['sgg_cd'].isna().sum())} rescues")
    cov = f["sgg_cd"].notna().mean()
    print(f"crosswalk coverage: {cov:.2%} of flood rescues ({f['sgg_cd'].notna().sum()}/{len(f)}); "
          f"fuzzy fixes used: {len(fixes)}")
    miss = f[f["sgg_cd"].isna()].groupby("key").size().sort_values(ascending=False)
    print("UNMATCHED keys (rescue counts):\n", miss.head(25).to_string())
    print("fuzzy fixes:", fixes)
    miss.rename("n").to_csv(OUT / "crosswalk_unmatched.csv")
    return f


def episodes(daily_nat: pd.Series) -> pd.DataFrame:
    """Contiguous storm days (count >= threshold), merging gaps <= EP_GAP_DAYS."""
    hot = daily_nat[daily_nat >= EP_THRESHOLD].index
    eps, cur = [], []
    for d in hot:
        if cur and (d - cur[-1]).days <= EP_GAP_DAYS + 1:
            cur.append(d)
        else:
            if cur:
                eps.append(cur)
            cur = [d]
    if cur:
        eps.append(cur)
    return eps


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sgg = load_sgg_table()
    sgg.to_csv(OUT / "sgg_table.csv", index=False)
    f = crosswalk(load_flood(), sgg)
    f.to_csv(OUT / "flood_events.csv", index=False)
    g = f.dropna(subset=["sgg_cd"])
    day = g.groupby(["sgg_cd", "date"]).size().rename("n").reset_index()
    day.to_csv(OUT / "sgg_day.csv", index=False)
    g.groupby(["sgg_cd", "date", "hour"]).size().rename("n").reset_index().to_csv(OUT / "sgg_hour.csv", index=False)
    nat = f.groupby("date").size().asfreq("D", fill_value=0)
    print("\nnational daily top 12:\n", nat.sort_values(ascending=False).head(12).to_string())
    short = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "대전광역시": "대전", "울산광역시": "울산",
             "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남",
             "전북특별자치도": "전북", "전남광주통합특별시": "전남광주", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주"}
    names = sgg.set_index("sgg_cd").apply(lambda r: f"{short.get(r['sido'], r['sido'])} {r['sgg']}", axis=1)
    rows = []
    for k, days in enumerate(episodes(nat), 1):
        a, b = days[0], days[-1]
        w = g[(g["date"] >= a) & (g["date"] <= b)]
        tot = int(((f["date"] >= a) & (f["date"] <= b)).sum())
        top = w.groupby("sgg_cd").size().sort_values(ascending=False)
        rows.append(dict(episode=k, start=a.date(), end=b.date(), days=(b - a).days + 1, rescues=tot,
                         n_sgg_with_rescue=int(top.size), peak_day=nat[a:b].idxmax().date(),
                         top_sgg="; ".join(f"{names[c]} {v}" for c, v in top.head(5).items())))
    ep = pd.DataFrame(rows)
    ep.to_csv(OUT / "episodes.csv", index=False)
    print(f"\nEPISODES (threshold {EP_THRESHOLD}/day, gap {EP_GAP_DAYS}d): n={len(ep)}\n", ep.to_string(index=False))
    for th in (50, 100, 150, 200):
        print("sensitivity threshold", th, "-> days>=th:", int((nat >= th).sum()))


if __name__ == "__main__":
    main()
