"""Stream the cached 119 activity files (Seoul 2024) and keep a slim table. Read-only on the cache."""
from __future__ import annotations
import glob, gzip, json
from pathlib import Path
import pandas as pd

ACT = Path("/Users/chloekang/kcf/data/cache/extra/ems/api/act")
OUT = Path(__file__).resolve().parent / "out" / "ems_seoul_2024_slim.csv"
KEEP = ["egrcSidoCdNm", "egrcSiggCdNm", "gutYm", "gutHh", "ptntAge", "ptntOccrTyCd1Nm", "ruptSptmCdNm", "ruptOccrPlcCdNm"]


def main() -> None:
    rows: list[dict] = []
    for f in sorted(glob.glob(str(ACT / "*_2024*.json.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for it in json.load(fh)["items"]:
                if it.get("egrcSidoCdNm") == "서울특별시":
                    rows.append({k: it.get(k) for k in KEEP})
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    print(len(rows))


if __name__ == "__main__":
    main()
