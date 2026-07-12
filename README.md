# TEI データ分析チュートリアル

架空の明治期日記『槻村清一郎（つきむら せいいちろう）日記』の TEI/XML コーパス(教材用・CC0)を、Google Colab で分析するチュートリアルです。

## はじめかた

下のボタンを押すと、ブラウザでノートブックが開きます(Google アカウントが必要です)。

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/nakamura196/tei-analysis-tutorial/blob/main/tei_analysis.ipynb)

開いたら、上から順に ▶(実行ボタン)を押してください。コードは読めなくて大丈夫です。最初の実行時に「警告: このノートブックは Google が作成したものではありません」と表示されたら、「このまま実行」で進めてください。

## 内容

- `tei_analysis.ipynb` — 分析ノートブック(人名・日付・地名の集計、グラフ、地図)
- `data/` — 日記の TEI ファイル(明治20〜22年、CC0)
- `data/edo_publications.csv` — 江戸後期(1801〜1867年)の日本文学書誌 627 件(NDL サーチの SRU 検索結果から抽出・加工)。教材『RAWGraphs ではじめる人文学データの可視化』で使います
- `data/edo_publications_enriched.csv` — 上記 CSV に `genre_norm`(NDC 分類記号の先頭一致で統一したジャンル)と `period`(時代区分)の2列を機械的に加えたもの。生成規則は `scripts/enrich_edo_publications.py` を参照
- `data/edo_publications_enriched_known.csv` — 上記から出版地「不明」の303行を除いた324件(出版地判明分のみ)
- `data/okunohosomichi.xml` — 松尾芭蕉『おくのほそ道』の教材用 TEI(本文はパブリックドメイン、マークアップは CC0)。青空文庫版(杉浦正一郎校註、[図書カード No.61619](https://www.aozora.gr.jp/cards/002240/card61619.html))から `scripts/build_okunohosomichi_tei.py` で生成。全45章段に、人名(persName 105箇所・人物台帳73人)、地名(placeName 252箇所・座標つき地名台帳)、日付(date 66箇所。『曾良旅日記』に基づく推定行程をグレゴリオ暦で付与)のアノテーション入り。架空日記と同じ構造(`div[@type='entry']` + `head/date/@when` + `standOff/listPlace`)なので、ノートブックの手法がそのまま適用できます

```text
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/data/okunohosomichi.xml
```

『おくのほそ道』では同じ場所が「白川の關」「白河の關」のように違う表記で登場するため、地名の集計には表層形(`el.text`)ではなく `@ref` 属性で台帳に紐づける方法が確実です:

```python
import xml.etree.ElementTree as ET
import pandas as pd

NS = {"tei": "http://www.tei-c.org/ns/1.0"}
root = ET.parse("okunohosomichi.xml").getroot()

gaz = {}  # 台帳: id -> (標準名, 緯度, 経度)
for pl in root.findall("tei:standOff/tei:listPlace/tei:place", NS):
    pid = pl.get("{http://www.w3.org/XML/1998/namespace}id")
    name = pl.find("tei:placeName", NS).text
    lat, lon = map(float, pl.find("tei:location/tei:geo", NS).text.split())
    gaz[pid] = (name, lat, lon)

rows = []
for div in root.findall("tei:text/tei:body/tei:div[@type='entry']", NS):
    when = div.find("tei:head/tei:date", NS).get("when")
    for el in div.findall(".//tei:placeName", NS):
        pid = (el.get("ref") or "").lstrip("#")
        if pid in gaz:
            name, lat, lon = gaz[pid]
            rows.append({"日付": when, "表記": el.text, "標準名": name, "緯度": lat, "経度": lon})

df = pd.DataFrame(rows)
print(df["標準名"].value_counts().head(10))
```

```text
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/data/edo_publications.csv
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/data/edo_publications_enriched.csv
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/data/edo_publications_enriched_known.csv
```

- `viewer/` — TEI ビューアーづくりの開始ファイル(`tei.xml` / `index.html` / `style.css`)。教材『AIで作るTEIビューアー【Antigravity版】』で使います。次の URL で直接取得できます

```text
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/viewer/tei.xml
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/viewer/index.html
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/viewer/style.css
```

## コピー用テキスト

教材 PDF の依頼文・コードは、[prompts フォルダ](prompts/)の各ファイルからコピーできます(PDF から直接コピーすると改行が乱れることがあるため)。
