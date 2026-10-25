"""Build 관악구 행정동×hour 생활인구(내국인) parquet from 서울 열린데이터광장 OA-14991 monthly zips.

Run: python3 -I tools/living_pop/build_gwanak.py
Reads raw zips as data only (no extraction to disk). Maps 8-digit 생활인구 행정동코드 to the
boundary file's adm_cd2 (10-digit 행안부 code) via adm_cd2[:8]; falls back to name only if needed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("living_pop")

ROOT = Path("/Users/chloekang/kcf/data/cache/extra")
RAW = ROOT / "living_pop" / "raw_seoul_opendata"
OUT = ROOT / "living_pop"
BOUNDARY = ROOT / "boundaries" / "HangJeongDong_ver20261001.geojson"
SGG = "11620"
DATES = {"2025-07-22": "LOCAL_PEOPLE_DONG_202507.zip", "2025-09-04": "LOCAL_PEOPLE_DONG_202509.zip"}
AGE65 = ["남자65세부터69세생활인구수", "남자70세이상생활인구수", "여자65세부터69세생활인구수", "여자70세이상생활인구수"]
URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false (POST infId=OA-14991, infSeq=3, seq={seq})"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def boundary_dongs() -> pd.DataFrame:
    g = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    rows = [f["properties"] for f in g["features"] if f["properties"].get("sgg") == SGG]
    b = pd.DataFrame(rows)[["adm_cd2", "adm_nm"]]
    b["adm_nm"] = b["adm_nm"].str.split().str[-1]
    b["code8"] = b["adm_cd2"].str[:8]
    return b


def main() -> None:
    b = boundary_dongs()
    assert len(b) == 21, len(b)
    sources: dict = {"dataset": "서울 생활인구(내국인) 행정동 단위 (KT 통신데이터 기반, 서울시·KT)",
                     "dataset_id": "OA-14991", "page": "https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do",
                     "license": "공공누리 제1유형 (출처표시, 상업적 이용 및 변경 가능)",
                     "provider": "서울특별시 (서울 열린데이터광장)", "files": [],
                     "column_mapping": {"기준일ID": "date", "시간대구분": "hour", "행정동코드": "adm_cd (8-digit 통계용; matched to boundary adm_cd2[:8], output adm_cd = boundary adm_cd2 10-digit)",
                                        "총생활인구수": "total", "+".join(AGE65): "age65p"},
                     "age_bands_summed_for_age65p": AGE65,
                     "boundary": str(BOUNDARY), "code_join": "living_pop 행정동코드 == boundary adm_cd2[:8]",
                     "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dates": {}}
    for date, zname in DATES.items():
        zp = RAW / zname
        with zipfile.ZipFile(zp) as z:
            (member,) = z.namelist()
            head = z.open(member).read(4)
            enc = "utf-8-sig" if head.startswith(b"\xef\xbb\xbf") else "cp949"  # 202509 = cp949 with stray '?' first byte
            with z.open(member) as f:
                df = pd.read_csv(f, encoding=enc, index_col=False, dtype=str)
        df.columns = [c.lstrip('\ufeff?"').strip('"') for c in df.columns]
        df = df[["기준일ID", "시간대구분", "행정동코드", "총생활인구수", *AGE65]]
        for c in ["총생활인구수", *AGE65]:
            df[c] = pd.to_numeric(df[c], errors="raise")
        log.info("%s: encoding=%s rows=%d", zname, enc, len(df))
        sources.setdefault("encodings", {})[zname] = enc
        avail = sorted(df["기준일ID"].unique())
        key = date.replace("-", "")
        if key not in avail:
            raise SystemExit(f"{date} not in {zname}; available {avail[0]}..{avail[-1]}")
        d = df[(df["기준일ID"] == key) & df["행정동코드"].str.startswith(SGG)].copy()
        codes = set(d["행정동코드"])
        miss_b = sorted(set(b["code8"]) - codes)
        extra = sorted(codes - set(b["code8"]))
        log.info("%s: %d rows, gwanak codes in data=%d, boundary-not-in-data=%s, data-not-in-boundary=%s", date, len(d), len(codes), miss_b, extra)
        d["age65p"] = d[AGE65].sum(axis=1)
        d = d.merge(b, left_on="행정동코드", right_on="code8", how="inner")
        out = pd.DataFrame({"date": pd.to_datetime(d["기준일ID"]).dt.date.astype(str), "hour": d["시간대구분"].astype(int),
                            "adm_cd": d["adm_cd2"], "adm_nm": d["adm_nm"],
                            "total": d["총생활인구수"].astype(float), "age65p": d["age65p"].astype(float)})
        out = out.sort_values(["hour", "adm_cd"]).reset_index(drop=True)
        assert len(out) == 21 * 24, len(out)
        assert out[["total", "age65p"]].notna().all().all()
        p = OUT / f"gwanak_{date}.parquet"
        out.to_parquet(p, index=False)
        tot = out.groupby("hour")[["total", "age65p"]].sum()
        sources["files"].append({"file": zname, "sha256": sha256(zp), "bytes": zp.stat().st_size,
                                 "url": URL.format(seq=zname[-8:-4]), "release": {"202507": "2025-08-07 (portal 등록일)", "202509": "2025-10-07 (portal 등록일)"}[zname[-10:-4]],
                                 "csv_member": member})
        sources["dates"][date] = {"output": str(p), "sha256_parquet": sha256(p), "rows": len(out), "exact_date": True,
                                  "code_mismatch": {"boundary_not_in_data": miss_b, "data_not_in_boundary": extra},
                                  "gwanak_total_by_hour": {int(h): round(v, 1) for h, v in tot["total"].items()},
                                  "gwanak_age65p_by_hour": {int(h): round(v, 1) for h, v in tot["age65p"].items()}}
        for h in (3, 14):
            log.info("%s %02d:00 total=%.0f age65p=%.0f share=%.3f", date, h, tot.loc[h, "total"], tot.loc[h, "age65p"], tot.loc[h, "age65p"] / tot.loc[h, "total"])
    sources["notes"] = [
        "Exact dates used (2025-07-22, 2025-09-04); no substitution.",
        "202507 file covers 29 days: 2025-07-08 and 2025-07-09 absent upstream. 202509 file: 2025-09-18 has 10100 rows (<10176); target dates complete (424 dongs x 24h).",
        "All 21 관악 codes match boundary adm_cd2[:8] exactly; no name fallback used.",
        "Values are model estimates (fractional persons), 내국인 only. 시간대구분 00 = 00:00-00:59.",
        "Sanity vs 주민등록 2024-12 (mois_pop cache): 관악구 477,812, 65+ 88,138 (18.4%). 생활인구 65+ share is ~13-15%: age bands are KT-subscriber-based estimates, not 주민등록; do not mix the two age shares.",
    ]
    (OUT / "gwanak_living_pop.sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
