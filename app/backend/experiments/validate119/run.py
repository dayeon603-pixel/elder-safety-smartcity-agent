"""Independent external validation of 영점 modules against 소방청 119 구급활동 records (Seoul, 2024).

Validation targets differ from training labels (질병청 신고 / TAAS hotspots). 구-level only, n = 25 구.
Backtests/validations are hypotheses, not proofs. 119 counts are a different, partial system (see caveats in results.json).
Spearman = rank correlation; ties get average ranks. Bootstrap = resample 구 with replacement (paired draws shared by all signals).
"""
from __future__ import annotations

import json, logging, re
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.path import Path as MPath

from load119 import load_act

logger = logging.getLogger("validate119")
HERE = Path(__file__).resolve().parent
EXP = HERE.parent
OUT = HERE / "out"
SEED, N_BOOT, TOPK = 42, 5000, 5
KCF = Path("/Users/chloekang/kcf/data/cache")
GEOJSON = KCF / "extra/boundaries/HangJeongDong_ver20261001.geojson"
POP_CSV = KCF / "j_population_regional/kr_15097972_resident_pop_by_dong_sex_age_202609.csv"
ZONES = KCF / "extra/silverzone/kr_15034532_silverzone_std.csv"
HEAT_TERMS = {"기타 온열손상", "온열손상"}
MONTHS = ["202406", "202407", "202408", "202409"]


# ---------------------------------------------------------------- stats
def rank_avg(a: np.ndarray) -> np.ndarray:
    """Average ranks along last axis (vectorized over leading dims)."""
    a = np.asarray(a, float)
    less = (a[..., None, :] < a[..., :, None]).sum(-1)
    eq = (a[..., None, :] == a[..., :, None]).sum(-1)
    return less + (eq + 1) / 2.0


def spearman(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Spearman along last axis; NaN if either side constant."""
    rx, ry = rank_avg(x), rank_avg(y)
    rx = rx - rx.mean(-1, keepdims=True); ry = ry - ry.mean(-1, keepdims=True)
    den = np.sqrt((rx ** 2).sum(-1) * (ry ** 2).sum(-1))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, (rx * ry).sum(-1) / den, np.nan)


def topk_share(score: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """Share of sum(y) in top-k units by score; boundary ties split fractionally. Last axis = units."""
    gt = (score[..., None, :] > score[..., :, None]).sum(-1)
    eq = (score[..., None, :] == score[..., :, None]).sum(-1)
    w = np.clip((k - gt) / eq, 0, 1)
    tot = y.sum(-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(tot > 0, (w * y).sum(-1) / tot, np.nan)


def ci(a: np.ndarray) -> list[float | None]:
    a = a[~np.isnan(a)]
    if len(a) < 100:
        return [None, None]
    return [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]


def boot_idx(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.integers(0, n, size=(N_BOOT, n))


def f(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


# ---------------------------------------------------------------- geo
def seoul_dongs() -> tuple[list[tuple[str, MPath]], pd.DataFrame]:
    gj = json.load(open(GEOJSON, encoding="utf-8"))
    polys, meta = [], []
    for ft in gj["features"]:
        p = ft["properties"]
        if p["sido"] != "11":
            continue
        g = ft["geometry"]
        for po in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            polys.append((p["sggnm"], MPath(np.asarray(po[0], float))))
        meta.append((str(p["adm_cd2"]), p["sggnm"]))
    return polys, pd.DataFrame(meta, columns=["adm_cd2", "sgg"]).drop_duplicates()


def assign_gu(lat: np.ndarray, lon: np.ndarray, polys) -> np.ndarray:
    pts = np.c_[lon, lat]
    out = np.full(len(pts), "", dtype=object)
    for name, path in polys:
        m = (out == "") & path.contains_points(pts)
        out[m] = name
    return out


# ---------------------------------------------------------------- heat
def heat(act: pd.DataFrame, rng: np.random.Generator) -> dict:
    s = act[act.egrcSidoCdNm == "서울특별시"]
    h = s[s.ptntOccrTyCd2Nm.isin(HEAT_TERMS) | s.ptntOccrTyCd3Nm.isin(HEAT_TERMS)]
    h = h[h.gutYm.isin(MONTHS)]
    panel = pd.read_csv(EXP / "heat/out/panel.csv.gz", usecols=["unit_id", "sido", "sgg", "pop", "old_share"]).drop_duplicates("unit_id")
    seo = panel[panel.sido == "서울특별시"].sort_values("unit_id").reset_index(drop=True)
    gus = seo.sgg.tolist(); assert len(gus) == 25
    pred = pd.read_csv(EXP / "heat/out/test_predictions.csv.gz")
    pred = pred[(pred.test_year == 2024) & (pred.budget_kind == "per_day") & pred.region_code.isin(seo.unit_id)]
    pred = pred.assign(ym=pred.date.str[:4] + pred.date.str[5:7])
    pred = pred[pred.ym.isin(MONTHS)]
    # region_code == unit_id check: B1 F alerted must equal panel b1_F
    pf = pd.read_csv(EXP / "heat/out/panel.csv.gz", usecols=["unit_id", "date", "b1_F", "b1_O"])
    chk = pred[(pred.method == "B1")].merge(pf, left_on=["region_code", "date"], right_on=["unit_id", "date"])
    ok = {v: bool((chk[chk.variant == v].alerted.values == chk[chk.variant == v][f"b1_{v}"].values).all()) for v in "FO"}
    assert all(ok.values()), ok
    u2g = dict(zip(seo.unit_id, seo.sgg))
    pred["gu"] = pred.region_code.map(u2g)

    y = h.groupby(["egrcSiggCdNm", "gutYm"]).size().unstack(fill_value=0).reindex(index=gus, columns=MONTHS, fill_value=0)
    pop = seo.set_index("sgg").loc[gus, "pop"].to_numpy(float)
    p65 = (seo.set_index("sgg").loc[gus, "pop"] * seo.set_index("sgg").loc[gus, "old_share"]).to_numpy(float)
    sig: dict[str, np.ndarray] = {}  # (25, 4) per signal
    pop_m = np.repeat(pop[:, None], 4, 1); p65_m = np.repeat(p65[:, None], 4, 1)
    sig["인구 (기준선)"] = pop_m
    sig["65세 이상 인구 (기준선)"] = p65_m
    for v in "FO":
        b = pred[(pred.method == "B1") & (pred.variant == v)].groupby(["gu", "ym"]).alerted.sum().unstack().reindex(index=gus, columns=MONTHS)
        sig[f"특보 규칙 B1 경보일수 ({v})"] = b.to_numpy(float)
    for v in "FO":
        o = pred[(pred.method == "OURS") & (pred.variant == v)].groupby(["gu", "ym"]).score.sum().unstack().reindex(index=gus, columns=MONTHS)
        sig[f"영점 예측 λ 합 ({v})"] = o.to_numpy(float)
    Y = y.to_numpy(float)
    idx = boot_idx(25, rng)
    res: dict = {"n_gu": 25, "n_119_heat_JunSep_total": int(Y.sum()), "n_by_month": dict(zip(MONTHS, Y.sum(0).astype(int).tolist())),
                 "counts_by_gu_month": {g: Y[i].astype(int).tolist() for i, g in enumerate(gus)},
                 "region_code_check_vs_panel_b1": ok, "signals": {}, "paired_diff_season": {}}

    def season_stats(S: np.ndarray, Yv: np.ndarray):
        return spearman(S.sum(1), Yv.sum(1)), topk_share(S.sum(1), Yv.sum(1), TOPK)

    Ys = Y.sum(1)
    boots: dict[str, np.ndarray] = {}
    for name, S in sig.items():
        ss = S.sum(1)
        rho = float(spearman(ss, Ys)); cap = float(topk_share(ss, Ys, TOPK))
        br = spearman(ss[idx], Ys[idx]); bc = topk_share(ss[idx], Ys[idx], TOPK)
        boots[name] = br
        # per-month
        pm = {}
        for j, m in enumerate(MONTHS):
            pm[m] = {"rho": f(spearman(S[:, j], Y[:, j])), "rho_ci": ci(spearman(S[idx, j], Y[idx, j])),
                     "top5_capture": f(topk_share(S[:, j], Y[:, j], TOPK)), "top5_ci": ci(topk_share(S[idx, j], Y[idx, j], TOPK))}
        # pooled 100 구-month points (cluster bootstrap over 구)
        flat = S.reshape(-1); yf = Y.reshape(-1)
        bi = (idx[:, :, None] * 4 + np.arange(4)).reshape(N_BOOT, -1)
        boots_p = spearman(flat[bi], yf[bi]); boots[name + "|pooled"] = boots_p
        res["signals"][name] = {"season_rho": rho, "season_rho_ci": ci(br), "season_top5_capture": cap, "season_top5_ci": ci(bc),
                                "pooled100_rho": f(spearman(flat, yf)), "pooled100_rho_ci": ci(spearman(flat[bi], yf[bi])),
                                "by_month": pm}
    # references
    res["reference"] = {"top5_random_expected": TOPK / 25,
                        "top5_oracle_by_119_counts": float(np.sort(Ys)[::-1][:TOPK].sum() / Ys.sum()),
                        "top5_oracle_ci": ci(np.sort(Ys[idx], axis=1)[:, ::-1][:, :TOPK].sum(1) / Ys[idx].sum(1))}
    for a in ["영점 예측 λ 합 (F)", "영점 예측 λ 합 (O)"]:
        for b in ["인구 (기준선)", "65세 이상 인구 (기준선)", "특보 규칙 B1 경보일수 (F)", "특보 규칙 B1 경보일수 (O)"]:
            if a[-2] != b[-2] and "B1" in b:
                continue  # pair F with F, O with O for B1
            d = boots[a] - boots[b]
            res["paired_diff_season"][f"{a} - {b}"] = {"diff": float(res["signals"][a]["season_rho"] - res["signals"][b]["season_rho"]), "ci": ci(d)}
    res["paired_diff_pooled100"] = {}
    for a in ["영점 예측 λ 합 (F)", "영점 예측 λ 합 (O)"]:
        for b in ["인구 (기준선)", "65세 이상 인구 (기준선)", "특보 규칙 B1 경보일수 (" + a[-2] + ")"]:
            res["paired_diff_pooled100"][f"{a} - {b}"] = {"diff": res["signals"][a]["pooled100_rho"] - res["signals"][b]["pooled100_rho"],
                                                          "ci": ci(boots[a + "|pooled"] - boots[b + "|pooled"])}
    res["mismatch_note"] = {"n_heat_records_seoul_all_year": int(len(s[s.ptntOccrTyCd2Nm.isin(HEAT_TERMS)])),
                            "gu_with_zero_heat_JunSep": int((Ys == 0).sum())}
    return res, sig, Y, gus


# ---------------------------------------------------------------- silver
def silver(act: pd.DataFrame, rng: np.random.Generator, map_path: Path | None = None) -> dict:
    s = act[act.egrcSidoCdNm == "서울특별시"]
    # complete-coverage window: 중부/중랑 소방서 lack 2024-12 -> use Jan-Nov for every 구
    s = s[s.gutYm <= "202411"]
    ped = s[s.ptntOccrTyCd1Nm.fillna("").str.contains("보행자")]
    old70 = ped[ped.ptntAge.isin(["70~79세", "80세 이상"])]  # age bins are decades: 65+ is not separable -> 70+ proxy
    old60p = ped[ped.ptntAge.isin(["60~69세", "70~79세", "80세 이상"])]
    polys, dmeta = seoul_dongs()
    gus = sorted(dmeta.sgg.unique()); assert len(gus) == 25
    # population by 구 (single-year age columns)
    pop = pd.read_csv(POP_CSV, encoding="cp949", thousands=",")
    pop["adm_cd2"] = pop["행정기관코드"].astype(str)
    ages = {c: int(m.group(1)) for c in pop.columns if (m := re.match(r"^(\d+)세", str(c)))}
    pop["p65"] = pop[[c for c, a in ages.items() if a >= 65]].sum(axis=1)
    pop["p70"] = pop[[c for c, a in ages.items() if a >= 70]].sum(axis=1)
    pop = pop.merge(dmeta, on="adm_cd2")
    pg = pop.groupby("sgg")[["계", "p65", "p70"]].sum().reindex(gus)
    # current zones (official registry, LC_TYPE==1 = 노인보호구역), by SIGNGU_NM
    z = pd.read_csv(ZONES); z = z[(z.CTPRVN_NM == "서울특별시") & (z.LC_TYPE == 1)]
    cur = z.groupby("SIGNGU_NM").size().reindex(gus, fill_value=0)
    # our picks: assign by polygon containment (map file has no 구)
    mp = pd.read_csv(map_path or EXP / "silver/out/map_seoul_current_vs_ours_k3918_fold2024.csv")
    ours_pts = mp[mp.kind == "ours"]
    og = assign_gu(ours_pts.lat.values, ours_pts.lon.values, polys)
    ours = pd.Series(og[og != ""]).value_counts().reindex(gus, fill_value=0)
    # sanity: current zones assigned by polygon vs SIGNGU_NM
    cg = assign_gu(z.LATITUDE.values, z.LONGITUDE.values, polys)
    agree = float((cg == z.SIGNGU_NM.values).mean())
    y = old70.groupby("egrcSiggCdNm").size().reindex(gus, fill_value=0).to_numpy(float)
    y60 = old60p.groupby("egrcSiggCdNm").size().reindex(gus, fill_value=0).to_numpy(float)
    yall = ped.groupby("egrcSiggCdNm").size().reindex(gus, fill_value=0).to_numpy(float)
    sig = {"현행 노인보호구역 수 (기준선)": cur.to_numpy(float), "65세 이상 인구 (기준선)": pg.p65.to_numpy(float),
           "70세 이상 인구 (기준선)": pg.p70.to_numpy(float), "영점 선정 지점 수": ours.to_numpy(float)}
    idx = boot_idx(25, rng)
    res = {"n_gu": 25, "window": "2024-01..11 (중부/중랑 12월 파일 없음)", "n_ped_all_ages": int(yall.sum()),
           "n_ped_70plus": int(y.sum()), "n_ped_60plus": int(y60.sum()),
           "age_note": "119 연령은 10세 구간(60~69/70~79/80+)이라 65세 이상 분리 불가: 주 지표는 70세 이상, 보조로 60세 이상",
           "n_ped_age_unknown('기타')": int((ped.ptntAge == "기타").sum()),
           "n_current_zones_seoul": int(cur.sum()), "n_ours_picks_seoul": int(ours.sum()),
           "n_ours_picks_outside_seoul_polygons": int((og == "").sum()),
           "zones_polygon_vs_SIGNGU_agree": agree,
           "counts_by_gu": {g: {"ped70": int(y[i]), "ped60": int(y60[i]), "cur": int(cur.iloc[i]), "ours": int(ours.iloc[i]),
                                "p65": int(pg.p65.iloc[i]), "p70": int(pg.p70.iloc[i])} for i, g in enumerate(gus)},
           "signals": {}}
    boots = {}
    for tag, Yv in [("ped70", y), ("ped60", y60)]:
        for name, S in sig.items():
            br = spearman(S[idx], Yv[idx]); boots[(tag, name)] = br
            res["signals"].setdefault(name, {})[tag] = {"rho": float(spearman(S, Yv)), "ci": ci(br),
                                                         "top5_capture": float(topk_share(S, Yv, TOPK)), "top5_ci": ci(topk_share(S[idx], Yv[idx], TOPK))}
    res["rho_ours_vs_p65"] = float(spearman(sig["영점 선정 지점 수"], sig["65세 이상 인구 (기준선)"]))
    res["rho_current_vs_p65"] = float(spearman(sig["현행 노인보호구역 수 (기준선)"], sig["65세 이상 인구 (기준선)"]))
    res["reference"] = {"top5_random_expected": 0.2, "top5_oracle": float(np.sort(y)[::-1][:5].sum() / y.sum())}
    res["paired_diff_ped70"] = {}
    for b in ["현행 노인보호구역 수 (기준선)", "65세 이상 인구 (기준선)", "70세 이상 인구 (기준선)"]:
        d = boots[("ped70", "영점 선정 지점 수")] - boots[("ped70", b)]
        res["paired_diff_ped70"][f"영점 - {b}"] = {"diff": res["signals"]["영점 선정 지점 수"]["ped70"]["rho"] - res["signals"][b]["ped70"]["rho"], "ci": ci(d)}
    more = ours.to_numpy() > cur.to_numpy()
    sh_cur = cur.to_numpy() / cur.sum(); sh_our = ours.to_numpy() / ours.sum()
    more_share = sh_our > sh_cur
    bs = lambda m: float(y[m].sum() / y.sum())
    bm = lambda m: (y[idx] * m[idx]).sum(1) / y[idx].sum(1)
    res["share_ped70_in_gu_ours_gt_current"] = {"n_gu": int(more.sum()), "share": bs(more), "ci": ci(bm(more)),
                                                "note": "영점 728지점 vs 현행 194개: 개수 비교는 대부분 구에서 자명하게 성립"}
    res["share_ped70_in_gu_ours_share_gt_current_share"] = {"n_gu": int(more_share.sum()), "share": bs(more_share), "ci": ci(bm(more_share)),
                                                            "note": "서울 내 비중끼리 비교(영점 비중 > 현행 비중)"}
    # by hour (70+ pedestrian calls)
    hr = old70.gutHh.astype(int).value_counts().reindex(range(24), fill_value=0)
    hr_all = ped.gutHh.astype(int).value_counts().reindex(range(24), fill_value=0)
    res["by_hour_ped70"] = hr.astype(int).tolist(); res["by_hour_ped_all"] = hr_all.astype(int).tolist()
    res["share_ped70_hours_06_18"] = float(hr.loc[6:17].sum() / hr.sum())
    res["share_ped70_hours_18_06"] = float(1 - hr.loc[6:17].sum() / hr.sum())
    res["share_ped70_hours_00_06"] = float(hr.loc[0:5].sum() / hr.sum())
    return res, sig, y, gus, hr, hr_all


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    rng = np.random.default_rng(SEED)
    act = load_act()
    assert len(act) == 627108
    hres, hsig, hY, hgus = heat(act, rng)
    sres, ssig, sy, sgus, hr, hr_all = silver(act, rng)
    out = {"seed": SEED, "n_boot": N_BOOT, "topk": TOPK, "n_records_total": int(len(act)),
           "caveats": ["구 단위만 (n=25); 구 내부 지점 수준 검증 아님",
                       "119는 질병청 신고(학습 라벨), TAAS(실버 라벨)과 다른 체계: 독립 검증",
                       "119 레코드에 일(day) 없음: 월 단위 이상 검증 불가",
                       "ptntAge '기타'(미상) 포함 레코드는 노인 집계에서 제외 -> 하한",
                       "2,432건 완전 중복행은 ID가 없어 제거하지 않음",
                       "인구는 2026-09 스냅샷(정적)"],
           "heat": hres, "silver": sres}
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    np.savez(OUT / "arrays.npz", hY=hY, hgus=np.array(hgus), sy=sy, hr=hr.values, hr_all=hr_all.values)
    logger.info("done")


if __name__ == "__main__":
    main()
