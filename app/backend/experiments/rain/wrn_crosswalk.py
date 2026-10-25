"""특보구역 (API허브 wrn_reg.php, 414 rows) -> 2026 시군구 crosswalk.

Rule: land zone whose full name (REG_NAME, e.g. 고양시/당진시/울릉군) equals the 시군구's 시/군 part and whose parent province
agrees. 시 split into 구 (성남시분당구) inherits the 시 zone. 광역시 구 inherit the 광역시 zone if no 구-level zone exists.
Sub-zones (파주동북부, 동해평지...) are kept as extra rows flagged sub=True and are NOT used for 시군구 mapping.
"""
from __future__ import annotations

import re

import pandas as pd

from common import EXTRA, OUT, norm_sgg, sido_group

PROV = {"경기도": "경기도", "강원도": "강원특별자치도", "충청북도": "충청북도", "충청남도": "충청남도", "전라북도": "전북특별자치도",
        "전북특별자치도": "전북특별자치도", "전라남도": "전남광주통합특별시", "경상북도": "경상북도", "경상남도": "경상남도",
        "제주도": "제주특별자치도", "서울": "서울특별시", "서울특별시": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
        "인천": "인천광역시", "광주": "전남광주통합특별시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시"}


def load_reg() -> pd.DataFrame:
    rows = []
    for ln in (EXTRA / "rain_kma" / "wrn_reg.txt").read_text(encoding="utf-8").splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.split()
        if len(f) < 6:
            continue
        rows.append(dict(reg_id=f[0], tm_ed=f[2], up=f[4], ko=f[5], name=f[-1]))
    return pd.DataFrame(rows)


SIDO_PROV = {"경기도": ["L1010000"], "강원특별자치도": ["L1020000"], "충청남도": ["L1030000"], "충청북도": ["L1040000"],
             "전북특별자치도": ["L1060000"], "경상북도": ["L1070000"], "경상남도": ["L1080000"], "제주특별자치도": ["L1090000"],
             "서울특별시": ["L1100000"], "인천광역시": ["L1110000"], "대전광역시": ["L1120000"], "대구광역시": ["L1140000"],
             "부산광역시": ["L1150000"], "울산광역시": ["L1160000"], "세종특별자치시": ["L1170000"]}


def province_of(z: pd.DataFrame) -> dict[str, str]:
    up = z.set_index("reg_id")["up"].to_dict()
    out = {}
    for r in z["reg_id"]:
        cur = r
        for _ in range(6):
            if up.get(cur) in ("L1000000", None):
                break
            cur = up[cur]
        out[r] = cur
    return out


def main() -> None:
    z = load_reg()
    z["prov"] = z["reg_id"].map(province_of(z))
    z = z[z["reg_id"].str.startswith("L")]            # land zones only (S* = sea)
    sgg = pd.read_csv(OUT / "sgg_table.csv", dtype={"sgg_cd": str})
    rows = []
    for _, r in sgg.iterrows():
        nm = norm_sgg(r["sgg"])
        m = re.match(r"^(.+?시)(.+구)$", nm)
        base = m.group(1) if m else nm
        if r["sido"] == "전남광주통합특별시":
            prov = ["L1130000"] if nm.endswith("구") else ["L1050000"]
        else:
            prov = SIDO_PROV.get(r["sido"], [])
        zz = z[z["prov"].isin(prov)]
        cand = zz[zz["name"].map(norm_sgg).str.startswith(base)] if r["sido"] not in ("서울특별시", "인천광역시", "대전광역시", "대구광역시", "부산광역시", "울산광역시", "세종특별자치시") and nm != "울릉군" else zz[zz["reg_id"].isin(prov)]
        if r["sido"] == "전남광주통합특별시" and nm.endswith("구"):
            cand = zz
        if nm == "울릉군":
            cand = z[z["reg_id"] == "L1600000"]
        if r["sido"] == "인천광역시" and nm in ("강화군", "옹진군"):
            cand = z[z["name"].map(norm_sgg).str.startswith(nm[:2]) & z["prov"].isin(["L1110000", "L1010000"])]
        rows.append(dict(sgg_cd=r["sgg_cd"], sido=r["sido"], sgg=r["sgg"], reg_ids=";".join(cand["reg_id"]), reg_names=";".join(cand["name"]), n_zone=len(cand)))
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "zone_crosswalk.csv", index=False)
    print(f"zones {len(z)}; 시군구 mapped {(x.n_zone > 0).sum()}/{len(x)}; unique zones used {len(set(';'.join(x.reg_ids).split(';')) - {''})}")
    print(x[x.n_zone == 0][["sido", "sgg"]].to_string(index=False))
    print(x[x.n_zone > 3][["sido", "sgg", "reg_names"]].to_string(index=False))


if __name__ == "__main__":
    main()
