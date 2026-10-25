"""Load the silver-zone and AED experiment outputs into SQLite (idempotent, provenance-checked).

`python -m app.modules.load [--force]`. Each module is reloaded as a unit in one transaction, and only
when a source file's sha256 changed or the `region` table changed (sgg_code / gu codes depend on it).
Silver reads the promoted run (`out/opt/promoted/`, method ours_opt) by default; `KCF_SILVER_SOURCE=v1`
falls back to `out/*.csv` (ours_full). See silver_config.py.
Every source file, loaded or not, is registered in `datasets` and `module_run` with its sha256.
The loader copies values; it computes nothing except percent -> fraction for AED open shares.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from ..config import APP_ROOT
from ..heat.common import GEOJSON, IngestError, register_dataset, resolve_paths, utc_now_iso
from . import geo
from . import silver_config as sc

logger = logging.getLogger("modules.load")

EXPERIMENTS_DIR = APP_ROOT / "backend" / "experiments"
SILVER_ENV, AED_ENV = "KCF_SILVER_OUT_DIR", "KCF_AED_OUT_DIR"
SILVER, AED = "silver", "aed"
PROTOCOL = "protocol.json"
CSV_ENCODING = "utf-8-sig"
PCT = 100.0
FRACTION_DIGITS = 10  # percent -> fraction without binary noise (0.328, not 0.32799999999999996)
HOTSPOT_KIND = "hotspot_2024"
SITE_KINDS = ("current", "ours")
EMPTY_HOURS = "nan-nan"
LIST_TOP5, LIST_5PCT = "top5_per_gu", "budget_5pct"
SEOUL = "서울특별시"
DATA_GO_KR = "https://www.data.go.kr/data/{id}/fileData.do"
APIS_AED = "https://apis.data.go.kr/B552657/AEDInfoInqireService/getAedFullDown"
APIS_119 = "https://apis.data.go.kr/1661000/EmergencyInformationService/getEmgencyActivityInfo"
LICENSE_PUBLIC = "공공누리/공공데이터포털 이용허락범위 제한 없음 (원 데이터 라이선스는 출처 확인 필요)"
LICENSE_DERIVED = "자체 산출물 (원 데이터 이용 조건을 따름)"


class ModuleLoadError(RuntimeError):
    """A source file is missing/malformed or violates a table constraint."""


@dataclass(frozen=True, slots=True)
class Source:
    """One experiment output file: how it is registered and (optionally) which loader consumes it."""

    file: str
    source_url: str
    description: str
    loads: bool
    relpath: str = ""  # sub-directory under the module's out dir ("" = the out dir itself)
    dataset_prefix: str = ""  # keeps promoted files from overwriting the v1 rows in `datasets`

    @property
    def key(self) -> str:
        """Unique name within the module (relpath/file), used in module_run.source_file."""
        return f"{self.relpath}/{self.file}" if self.relpath else self.file


_SILVER_REGISTER_ONLY = (
    Source("coverage_by_fold.csv", DATA_GO_KR.format(id=15029185),
           "실버존 모듈: 시험 연도별 포착률 (출처 등록만)", False),
    Source("test_hotspots_meta.csv", DATA_GO_KR.format(id=15029185),
           "실버존 모듈: 시험 사고다발지 메타 (출처 등록만)", False),
)
_SILVER_LOADED_DESC = (
    ("coverage_results.csv", "실버존 모듈: 방법별 포착률과 95% 구간 ({run} 산출)"),
    ("delta_results.csv", "실버존 모듈: 방법 간 포착률 차이(%p)와 쌍체 부트스트랩 구간 ({run})"),
    ("map_seoul_current_vs_ours_k3918_fold2024.csv", "실버존 모듈: k=3918 현행 구역, 에이전트 후보, 2024 사고다발지 지도 점 ({run})"),
)
_SILVER_RUN_LABEL = {sc.PROMOTED: "experiments/silver/out/opt/promoted, 최적화 선택 ours_opt",
                     sc.V1: "experiments/silver/run.py, ours_full"}
_SILVER_PREFIX = {sc.PROMOTED: "promoted_", sc.V1: ""}
PROTOCOL_KEY = sc.SILVER_PROTOCOL  # e.g. "opt/protocol_opt.json" (promoted) or "protocol.json" (v1)
SILVER_SOURCES: tuple[Source, ...] = (
    *(Source(f, DATA_GO_KR.format(id=15034532), d.format(run=_SILVER_RUN_LABEL[sc.SILVER_SOURCE]), True,
             relpath=sc.SILVER_RELPATH, dataset_prefix=_SILVER_PREFIX[sc.SILVER_SOURCE]) for f, d in _SILVER_LOADED_DESC),
    *_SILVER_REGISTER_ONLY,
    Source(Path(sc.SILVER_PROTOCOL).name, DATA_GO_KR.format(id=15034532),
           "실버존 모듈: 실험 설정과 입력 파일 sha256 (출처 등록만)", False,
           relpath=str(Path(sc.SILVER_PROTOCOL).parent) if Path(sc.SILVER_PROTOCOL).parent != Path() else "",
           dataset_prefix=_SILVER_PREFIX[sc.SILVER_SOURCE]),
)
AED_SOURCES = (
    Source("summary.json", APIS_119, "AED 모듈: 소방청 구급활동정보 x AED 개방시간 요약 통계", True),
    Source("hourly_gap.csv", APIS_AED, "AED 모듈: 시간대별 공공장소 심정지 건수와 AED 열림 비율", True),
    Source("seoul_gu_night_gap_map.csv", APIS_AED, "AED 모듈: 서울 구별 밤 공백 점수", True),
    Source("recommended_candidates_top5_per_gu.csv", APIS_AED, "AED 모듈: 구별 24시간 전환 후보 상위 5곳", True),
    Source("recommended_candidates_5pct.csv", APIS_AED, "AED 모듈: 예산 5% 전환 후보 전체", True),
    Source("policy_eval.csv", APIS_AED, "AED 모듈: 전환 정책 비교 평가 (출처 등록만)", False),
    Source("aed_diagnostics.json", APIS_AED, "AED 모듈: 개방시간 자료 진단 (출처 등록만)", False),
    Source(PROTOCOL, APIS_AED, "AED 모듈: 실험 설정 (출처 등록만)", False),
)
_PROTOCOL_KEY = {SILVER: PROTOCOL_KEY, AED: PROTOCOL}


# ---------------------------------------------------------------- file helpers
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding=CSV_ENCODING, newline="") as fh:
            return list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ModuleLoadError(f"{path.name}: unreadable ({exc})") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModuleLoadError(f"{path.name}: unreadable ({exc})") from exc
    if not isinstance(data, dict):
        raise ModuleLoadError(f"{path.name}: top level is not an object")
    return data


def _num(row: dict[str, str], col: str) -> float:
    """Finite float of a required column."""
    try:
        v = float(row[col])
    except (KeyError, ValueError, TypeError) as exc:
        raise ModuleLoadError(f"column {col!r}: not a number ({row.get(col)!r})") from exc
    if not math.isfinite(v):
        raise ModuleLoadError(f"column {col!r}: not finite")
    return v


def _int(row: dict[str, str], col: str) -> int:
    v = _num(row, col)
    if v != int(v):
        raise ModuleLoadError(f"column {col!r}: not an integer ({v})")
    return int(v)


def _text(row: dict[str, str], col: str) -> str | None:
    v = (row.get(col) or "").strip()
    return v or None


def _req_text(row: dict[str, str], col: str) -> str:
    v = _text(row, col)
    if v is None:
        raise ModuleLoadError(f"column {col!r}: empty")
    return v


def _frac(pct: float) -> float:
    return round(pct / PCT, FRACTION_DIGITS)


def _row_count(path: Path) -> int:
    if path.suffix == ".json":
        return len(_read_json(path))
    return len(_read_csv(path))


def _paths(env_name: str, default: Path, override: Path | None) -> Path:
    return override or Path(os.environ.get(env_name, str(default)))


def _region_fingerprint(conn: sqlite3.Connection) -> str:
    codes = "|".join(f"{r['code']}:{r['name']}" for r in conn.execute("SELECT code, name FROM region ORDER BY code"))
    return hashlib.sha256(codes.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- silver
def _load_silver(conn: sqlite3.Connection, d: Path, geojson: Path | None) -> dict[str, int]:
    res = _read_csv(d / "coverage_results.csv")
    if not any(r.get("method") == sc.OURS and r.get("pool") == sc.HEADLINE_POOL for r in res):
        raise ModuleLoadError(f"coverage_results.csv has no {sc.HEADLINE_POOL}/{sc.OURS} row "
                              f"(KCF_SILVER_SOURCE={sc.SILVER_SOURCE})")
    conn.executemany(
        "INSERT INTO silver_result (variant, stratum, pool, method, k, coverage, ci_lo, ci_hi, n_hot, casualties) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(_req_text(r, "variant"), _req_text(r, "stratum"), _req_text(r, "pool"), _req_text(r, "method"),
          _int(r, "k"), _num(r, "coverage"), _num(r, "lo"), _num(r, "hi"), _int(r, "n_hot"), _num(r, "cas"))
         for r in res],
    )
    dl = _read_csv(d / "delta_results.csv")
    conn.executemany(
        "INSERT INTO silver_delta (variant, stratum, pool, method, k, vs, delta_pp, lo_pp, hi_pp) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(_req_text(r, "variant"), _req_text(r, "stratum"), _req_text(r, "pool"), _req_text(r, "method"),
          _int(r, "k"), _req_text(r, "vs"), _num(r, "delta_pp"), _num(r, "lo_pp"), _num(r, "hi_pp")) for r in dl],
    )
    pts = _read_csv(d / "map_seoul_current_vs_ours_k3918_fold2024.csv")
    for r in pts:
        if _req_text(r, "kind") not in (*SITE_KINDS, HOTSPOT_KIND):
            raise ModuleLoadError(f"map file: unknown kind {r['kind']!r}")
    assigned = geo.assign_points(conn, [(_num(r, "lat"), _num(r, "lon")) for r in pts], geojson)
    n_site = n_hot = 0
    for r, (code, method) in zip(pts, assigned, strict=True):
        flags = (_int(r, "cur_within_300m"), _int(r, "ours_within_300m"))
        if r["kind"] == HOTSPOT_KIND:
            conn.execute(
                "INSERT INTO silver_hotspot_2024 (lat, lon, casualties, sgg_code, sgg_method, covered_current, "
                "covered_ours) VALUES (?,?,?,?,?,?,?)",
                (_num(r, "lat"), _num(r, "lon"), _num(r, "cas"), code, method, *flags),
            )
            n_hot += 1
        else:
            conn.execute(
                "INSERT INTO silver_site (kind, lat, lon, sido, name, sgg_code, sgg_method, cur_within_300m, "
                "ours_within_300m) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["kind"], _num(r, "lat"), _num(r, "lon"), _text(r, "sido"), _text(r, "zone_nm"), code, method, *flags),
            )
            n_site += 1
    return {"silver_result": len(res), "silver_delta": len(dl), "silver_site": n_site, "silver_hotspot_2024": n_hot}


# ---------------------------------------------------------------- aed
def _aed_stats(summary: dict[str, Any]) -> list[tuple[str, float, float | None, float | None, int | None]]:
    """Named statistics from summary.json: (name, value, ci_lo, ci_hi, n). Percent values become fractions."""
    out: list[tuple[str, float, float | None, float | None, int | None]] = []
    try:
        public = float(summary["audit"]["public_ca"])
        out.append(("public_arrests_total", public, None, None, None))
        for sched, key in (("weekday", "weekday"), ("weekend", "weekend_W1_as_reported")):
            desc = summary["desc"][key]
            for label, src in (("night", "mean_pct_open_night"), ("day", "mean_pct_open_day"),
                               ("night_arrest_weighted", "arrest_weighted_pct_open_night"),
                               ("day_arrest_weighted", "arrest_weighted_pct_open_day")):
                out.append((f"{sched}.open_share_{label}", _frac(float(desc[src])), None, None, None))
            lt50 = desc["share_arrests_when_open_lt_50pct"]
            out.append((f"{sched}.share_arrests_open_lt_50pct", float(lt50[0]), float(lt50[1]), float(lt50[2]), int(lt50[3])))
        night = summary["desc"]["weekday"]["night_share_of_arrests_22_06"]
        out.append(("public_arrests_night_share", float(night[0]), float(night[1]), float(night[2]), int(night[3])))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ModuleLoadError(f"summary.json: unexpected structure ({exc!r})") from exc
    return out


def _load_aed(conn: sqlite3.Connection, d: Path) -> dict[str, int]:
    stats = _aed_stats(_read_json(d / "summary.json"))
    conn.executemany("INSERT INTO aed_stat (name, value, ci_lo, ci_hi, n) VALUES (?,?,?,?,?)", stats)
    hourly = _read_csv(d / "hourly_gap.csv")
    conn.executemany(
        "INSERT INTO aed_hourly_gap (hour, public_arrests, open_share_weekday, open_share_weekend, "
        "open_share_weekend_w2) VALUES (?,?,?,?,?)",
        [(_int(r, "hour"), _int(r, "public_ca"), _frac(_num(r, "pct_open_weekday")),
          _frac(_num(r, "pct_open_weekend_W1_as_reported")),
          _frac(_num(r, "pct_open_weekend_W2_placeholder_as_weekday"))) for r in hourly],
    )
    codes = {str(r["name"]): str(r["code"]) for r in conn.execute(
        "SELECT code, name FROM region WHERE sido = ?", (SEOUL,))}
    gus = _read_csv(d / "seoul_gu_night_gap_map.csv")
    conn.executemany(
        "INSERT INTO aed_gu_gap (gu, sgg_code, source_code, n_aed, n_aed_24h_weekday, night_open_share, "
        "day_open_share, gap_score, public_arrests_night) VALUES (?,?,?,?,?,?,?,?,?)",
        [(_req_text(r, "gu"), codes.get(_req_text(r, "gu")), _req_text(r, "code"), _int(r, "n_aed"),
          _int(r, "n_aed_24h_weekday"), _frac(_num(r, "night_pct_open_weekday")),
          _frac(_num(r, "day_pct_open_weekday")), _num(r, "night_gap_score_weekday"),
          _num(r, "public_ca_per_yr_night_est")) for r in gus],
    )
    n_cand = 0
    for kind, fname in ((LIST_TOP5, "recommended_candidates_top5_per_gu.csv"), (LIST_5PCT, "recommended_candidates_5pct.csv")):
        rows = sorted(_read_csv(d / fname),
                      key=lambda r: (_req_text(r, "gu"), -_num(r, "gain_score"), _req_text(r, "serialSeq")))
        rank: dict[str, int] = {}
        for r in rows:  # no manager name or phone column is read: only the facility, place and hours
            gu = _req_text(r, "gu")
            rank[gu] = rank.get(gu, 0) + 1
            hours = _text(r, "weekday_hours")
            conn.execute(
                "INSERT INTO aed_candidate (list_kind, serial_seq, gu, rank_in_gu, org, address, place, weekday_hours, "
                "night_open_weekday_share, gain_score, lat, lon) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind, _req_text(r, "serialSeq"), gu, rank[gu], _req_text(r, "org"), _req_text(r, "address"),
                 _req_text(r, "place_in_building"), None if hours in (None, EMPTY_HOURS) else hours,
                 _num(r, "night_open_weekday_share"), _num(r, "gain_score"), _num(r, "lat"), _num(r, "lon")),
            )
            n_cand += 1
    return {"aed_stat": len(stats), "aed_hourly_gap": len(hourly), "aed_gu_gap": len(gus), "aed_candidate": n_cand}


# ---------------------------------------------------------------- driver
_OWNED = {
    SILVER: ("silver_hotspot_2024", "silver_site", "silver_delta", "silver_result"),
    AED: ("aed_candidate", "aed_gu_gap", "aed_hourly_gap", "aed_stat"),
}
Loader = Callable[[sqlite3.Connection], dict[str, int]]


def _needs_reload(conn: sqlite3.Connection, module: str, shas: dict[str, str], note: str) -> bool:
    have = {str(r["source_file"]): (str(r["sha256"]), str(r["note"])) for r in conn.execute(
        "SELECT source_file, sha256, note FROM module_run WHERE module = ?", (module,))}
    return have != {f: (s, note) for f, s in shas.items()}


def _load_module(
    conn: sqlite3.Connection, module: str, directory: Path, sources: Sequence[Source], loader: Loader,
    force: bool, geojson_known: bool,
) -> str:
    paths = {s.key: directory / s.relpath / s.file for s in sources}
    for p in paths.values():
        if not p.is_file():
            raise ModuleLoadError(f"missing source file: {p}")
    shas = {f: _sha256(p) for f, p in paths.items()}
    protocol_sha = shas[_PROTOCOL_KEY[module]]
    note = json.dumps({"region_fp": _region_fingerprint(conn), "geojson": geojson_known}, sort_keys=True)
    if not force and not _needs_reload(conn, module, shas, note):
        logger.info("%s: unchanged", module)
        return "unchanged"
    now = utc_now_iso()
    try:
        with db.transaction(conn):
            for table in _OWNED[module]:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - table names are constants above
            counts = loader(conn)
            conn.execute("DELETE FROM module_run WHERE module = ?", (module,))
            for s in sources:
                path = paths[s.key]
                n = _row_count(path)
                ds = register_dataset(
                    conn, name=f"{module}_{s.dataset_prefix}{path.stem}", path=path, source_url=s.source_url,
                    license_=LICENSE_PUBLIC if module == AED else LICENSE_DERIVED, description=s.description,
                    row_count=n, roles={"kind": "module", "module": module, "loaded": s.loads,
                                        "silver_source": sc.SILVER_SOURCE if module == SILVER else None},
                    sha256=shas[s.key],
                )
                conn.execute(
                    "INSERT INTO module_run (module, source_file, sha256, protocol_sha, dataset_id, n_rows, loaded, "
                    "note, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (module, s.key, shas[s.key], protocol_sha, ds, n, int(s.loads), note, now),
                )
    except (sqlite3.Error, IngestError) as exc:
        raise ModuleLoadError(f"{module}: {exc}") from exc
    logger.info("%s: loaded %s", module, counts)
    return "loaded"


def load_modules(
    conn: sqlite3.Connection, silver_dir: Path | None = None, aed_dir: Path | None = None,
    geojson: Path | None = None, force: bool = False,
) -> dict[str, str]:
    """Load both modules; returns {module: 'loaded' | 'unchanged'}.

    Raises:
        ModuleLoadError: a file is missing/malformed or a table constraint rejects a row (nothing is
            partially written: each module is one transaction).
    """
    sd = _paths(SILVER_ENV, EXPERIMENTS_DIR / SILVER / "out", silver_dir)
    ad = _paths(AED_ENV, EXPERIMENTS_DIR / AED / "out", aed_dir)
    sd_data = sd / sc.SILVER_RELPATH  # where the loaded silver files live (promoted sub-dir or the out dir)
    geo_known = geojson is not None and geojson.is_file()
    return {
        SILVER: _load_module(conn, SILVER, sd, SILVER_SOURCES, lambda c: _load_silver(c, sd_data, geojson), force,
                             geo_known),
        AED: _load_module(conn, AED, ad, AED_SOURCES, lambda c: _load_aed(c, ad), force, geo_known),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="app.modules.load", description=__doc__)
    ap.add_argument("--force", action="store_true", help="reload even when nothing changed")
    args = ap.parse_args(argv)
    paths = resolve_paths()
    conn = db.connect(paths.db_path)
    try:
        db.migrate(conn)
        result = load_modules(conn, geojson=paths.data_dir / GEOJSON, force=args.force)
        from .mobility import MobilityLoadError, load_mobility

        try:
            result["mobility"] = load_mobility(conn, force=args.force)
        except MobilityLoadError as exc:  # the mobility output is optional: report, do not fail the other modules
            logger.error("mobility: %s", exc)
            result["mobility"] = "error"
    except ModuleLoadError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        conn.close()
    logger.info("modules: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
