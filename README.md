# TEI データ分析チュートリアル

架空の明治期日記『槻村清一郎（つきむら せいいちろう）日記』の TEI/XML コーパス(教材用・CC0)を、Google Colab で分析するチュートリアルです。

## はじめかた

下のボタンを押すと、ブラウザでノートブックが開きます(Google アカウントが必要です)。

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/nakamura196/tei-analysis-tutorial/blob/main/tei_analysis.ipynb)

開いたら、上から順に ▶(実行ボタン)を押してください。コードは読めなくて大丈夫です。最初の実行時に「警告: このノートブックは Google が作成したものではありません」と表示されたら、「このまま実行」で進めてください。

## 内容

- `tei_analysis.ipynb` — 分析ノートブック(人名・日付・地名の集計、グラフ、地図)
- `data/` — 日記の TEI ファイル(明治20〜22年、CC0)
- `viewer/` — TEI ビューアーづくりの開始ファイル(`tei.xml` / `index.html` / `style.css`)。教材『AIで作るTEIビューアー【Antigravity版】』で使います。次の URL で直接取得できます

```text
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/viewer/tei.xml
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/viewer/index.html
https://raw.githubusercontent.com/nakamura196/tei-analysis-tutorial/main/viewer/style.css
```
