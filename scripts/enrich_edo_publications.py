#!/usr/bin/env python3
"""data/edo_publications.csv に派生列を加えた edo_publications_enriched.csv を生成する。

追加する列:
- genre_norm: NDC 分類記号の先頭一致による統一ジャンル。genre_major の
  「日本文学」のような包括ラベルの粒度不揃いを、機械的な規則で揃えたもの。
- period:     出版年による時代区分（化政期 / 天保期 / 幕末期）。

元の7列は変更しない。規則は下の GENRE_RULES / period() がすべてで、
これ以外の手作業による修正は加えていない。

使い方: python3 scripts/enrich_edo_publications.py
"""

import csv
from pathlib import Path

# 先頭一致で評価する。より長い（具体的な）記号を先に置くこと。
GENRE_RULES = [
    ("911.19", "狂歌"),
    ("911.1", "和歌・短歌"),
    ("911.2", "連歌"),
    ("911.3", "俳諧"),
    ("911.4", "川柳・狂句"),
    ("911.6", "歌謡"),
    ("911", "詩歌（その他）"),
    ("912", "戯曲"),
    ("913", "小説・物語"),
    ("914", "随筆"),
    ("915", "日記・紀行"),
    ("919", "漢詩文"),
    ("91", "日本文学（その他）"),
]


def genre_norm(ndc_code: str) -> str:
    if not ndc_code:
        return "不明"
    for prefix, label in GENRE_RULES:
        if ndc_code.startswith(prefix):
            return label
    return "その他"  # NDC 91 以外（歴史・地理など）


def period(year: str) -> str:
    y = int(year)
    if y <= 1829:
        return "1801-1829 化政期"
    if y <= 1843:
        return "1830-1843 天保期"
    return "1844-1867 幕末期"


def main() -> None:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    src = data_dir / "edo_publications.csv"
    dst = data_dir / "edo_publications_enriched.csv"

    with src.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) + ["genre_norm", "period"]
        rows = []
        for row in reader:
            row["genre_norm"] = genre_norm(row["ndc_code"])
            row["period"] = period(row["year"])
            rows.append(row)

    with dst.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{dst.name}: {len(rows)} rows, {len(fieldnames)} cols")
    for col in ("genre_norm", "period"):
        counts = {}
        for row in rows:
            counts[row[col]] = counts.get(row[col], 0) + 1
        joined = " / ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
        print(f"  {col}: {joined}")


if __name__ == "__main__":
    main()
