# 青空文庫テキストの TEI/XML 化ガイドライン

青空文庫のテキストから、固有表現(人名・地名・日付)アノテーション付きの TEI P5 文書を
一定の品質で作成するための作業指針。人間・AI エージェントのどちらが作業する場合も、
**このファイルを参照点とする**。

- 生成パイプライン: `scripts/build_aozora_tei.py`(散文作品の汎用)
  / `scripts/build_okunohosomichi_tei.py`(日記・紀行の先例)
- 出力先: `data/aozora/<slug>.xml`
- 作品の追加 = **スペックを 1 つ足すだけ**(`scripts/aozora_specs/<slug>.json`、
  または `build_aozora_tei.py` の `WORKS` に直接)。本文抽出・TEI 生成・タグ付けは
  パイプラインが行うので、手作業の中心は「底本の選択」と「固有表現対応表の作成・検証」

---

## 1. 底本(青空文庫カード)の選択

1. 作家ページ(`https://www.aozora.gr.jp/index_pages/person<N>.html`)から図書カードを探す。
   カードページは UTF-8、本文 XHTML(`cards/<dir>/files/<id>_<n>.html`)は Shift_JIS
2. 同一作品に複数カードがある場合の優先順: **新字新仮名 > 新字旧仮名 > 旧字**。
   ただし文語作品(舞姫など)や翻刻系(おくのほそ道)は新字旧仮名・旧字でも可。
   「作業中」(未公開)の作品 ID は使えない
3. カードの「作品データ」「底本データ」を確認し、TEI の `sourceDesc` に転記される
   底本情報が正しく取れることを確認する(パイプラインが `bibliographical_information`
   から自動転記する)
4. 著作権: 著者・翻訳者・校訂者すべての保護期間満了を確認(青空文庫収録済みなら通常は満了。
   「著作権存続」マークのある作品は使わない)

## 2. 本文抽出の方針(パイプラインが実施 — 変えない)

- ルビは親文字のみ残す(読みは捨てる)
- 注記 `［＃...］` は除去。**外字は Unicode 実字に置換**(img の alt / 面区点 / U+ 表記から解決)。
  取りこぼしは「〓」になるので、**〓 が 1 件でも残ったら**元 XHTML の該当箇所を調べ、
  正しい文字をスペックの `fixes`(置換表)に入れる
- 見出し(h3/h4 の midashi)があれば章 `div` に分割、なければ全体を 1 つの `div`
  (head は作品タイトル)
- 杉浦校註版のような**校註・解説・凡例は収録しない**(本文のみ)

## 3. TEI 構造の規約

- `teiHeader`: 既存出力に倣う。必須要素:
  - `respStmt` に「固有表現アノテーション(AI 支援・編者確認)」を明記
  - `availability`: 本文はパブリックドメイン、**マークアップ等の付加部分は CC0 1.0**
  - `notesStmt`: 座標が概値であること、架空の場所には座標を付けないことを注記
  - `sourceDesc`: 青空文庫カード URL + 底本 + 「ルビ・注記は省略し、外字は Unicode
    実字に置換した」
- `standOff`: `listPerson`(person/@xml:id + persName + note)、
  `listPlace`(place/@xml:id + placeName + location/geo + note)。
  **id は英小文字・数字・ハイフンのみ**(ローマ字)
- `text/body`: 章 = `<div type="chapter" xml:id="secNN" n="NN">` + `<head>` + `<p>` 列。
  日記・紀行は `<div type="entry">` + `<head>` 内 `<date when="...">`
  (おくのほそ道の先例を参照 — ノートブック教材と互換にする場合はこちら)
- インライン: `<persName ref="#id">` / `<placeName ref="#id">` / `<date when="...">`。
  **表記揺れは @ref で台帳に統合する**(白川の關/白河の關 → 同じ place)
- **このタグ語彙は表示側との契約でもある**: archivebase の単一スタイルシート
  `tei-aozora.xsl`(apps/web/public/tei/xsl/、コーパスの既定ビュー)が上記の語彙
  だけを前提に、固有表現の色分け+台帳ツールチップ・OSM リンク・句ブロック・
  巻末台帳を描画する。**語彙を増やす場合は tei-aozora.xsl も同時に更新する**

## 4. 固有表現アノテーションの基準

### persName(人名)
- **固有名のみ**。役割語・親族呼称(下人・老婆・母・土工・遊女)は付けない
- 可: 名前を持つ動物(ごん)、神・伝説上の存在(ゼウス・木の花さくや姫・玉藻の前)、
  実在人物への言及(井伏鱒二・白河楽翁)、句の作者名(曾良)
- 同一人物の別表記(正太郎/正太/正さん、惣五郎/宗悟)は**同じ id に複数タグで束ねる**
- 登場人物が全員無名なら persName 0 件でよい(羅生門の先例 — それ自体が分析結果)

### placeName(地名)
- 実在・架空を問わず**固有の場所名**。建物・店・寺社・街路・関所・山川・国名・地域名も可
- 実在地には**概値座標**(小数4桁まで)を付け、精度に応じて note に「概値」「伝承地」
  「国名・概値」等を書く。歌枕など所在に諸説ある地は伝承地でよい(note 必須)
- **架空・宗教的世界観上の場所(極楽・地獄・森羅殿)は座標なし**(location を出さない)
  + note に趣旨を書く
- 位置に自信がない実在地は座標 null にして note に理由を書く方が、誤った座標より良い

### date(日付)
- **明示的な年号・日付のみ**(「元祿二とせ」「明治廿一年」「十四日」「文月」)。
  年齢(「二十六の年」)・相対表現(「去年の暮」「翌年」)は付けない
- `@when` は ISO 8601。精度は落としてよい: 年のみ `1888`、年月 `1890-01`、
  月日のみ `--08-20`(年不明)。元号のみなら開始年(寛政 → `1789`)
- 日記・紀行の章日付のように**編者が推定で付与する日付**は、teiHeader の notesStmt に
  推定の根拠(例: 『曾良旅日記』に基づく行程、旧暦→新暦換算表)を必ず書く

## 5. 辞書マッチの制約と検証(最重要)

パイプラインのタグ付けは「**表層形の全出現を機械的にタグ付け**(長い表層形優先・
重複領域は不可)」。出現位置の個別指定はできない。したがって:

1. **候補表層形の全出現について前後 20 字の文脈を列挙して目視確認する**。
   誤マッチが 1 件でもある表層形は使えない
2. 回避策は 2 つ: (a) より長い表層形にする(太田豊太郎 → 太田 の順で重ねる、
   湯殿山 → 湯殿 の順で重ねる)、(b) 除外してレポートに理由を書く
3. 実際に踏んだ罠の例(除外判断の参考):
   - 「日本」が「日本髪」に、「芝」が「芝居」に、「衛」が「綦衛の矢」に、
     「南京」が「南京玉」に、「中山寺」が花火の銘柄にマッチ
   - 「月山」が刀の銘「終月山と銘を切て」にマッチ(おくのほそ道では出現番号指定で回避 —
     これは `build_okunohosomichi_tei.py` 専用機能。汎用側では表層形の工夫か除外で対応)
4. ビルド時の警告(「一度もマッチしなかったタグ」「台帳に無い参照」)は**ゼロにする**

## 6. スペック JSON のスキーマ(`scripts/aozora_specs/<slug>.json`)

```jsonc
{
  "slug": "torokko",
  "url": "https://www.aozora.gr.jp/cards/000879/files/43016_16836.html",  // XHTML
  "card": "https://www.aozora.gr.jp/cards/000879/card43016.html",
  "title": "トロッコ",
  "author": "芥川龍之介",
  "fixes": {"〓": "鍤"},                        // 外字の手当て(不要なら省略)
  "persons": {"ryohei": ["良平", "主人公の少年"]},
  "places":  {"odawara": ["小田原", 35.2646, 139.1521, "概値"],
              "gokuraku": ["極楽", null, null, "仏教的世界観上の場所(座標なし)"]},
  "tags": [["pers", "良平", "ryohei"],
           ["place", "小田原", "odawara"],
           ["date", "二月の初旬", "--02"]]      // date の第3要素は @when 値
}
```

## 7. 検証・納品の手順

1. `python3 scripts/build_aozora_tei.py <slug>` — **警告ゼロ**を確認
2. 整形式 + スキーマ検証: `xmllint --noout --relaxng tei_all.rng data/aozora/<slug>.xml`
   (tei_all.rng は https://tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng)
3. タグ付け結果の文脈ダンプを目視(誤マッチ最終確認)
4. 検証レポートを残す: 選んだカードと理由、章数・文字数、表層形ごとの件数と代表文脈、
   除外した候補と理由、座標の確度
5. コミット & push(データは raw URL で教材・archivebase から参照される)
6. archivebase へ登録: `~/git/ab/archivebase-data` の `import-aozora-tei.ts` の WORKS に
   slug を足して `ARCHIVEBASE_API_BASE=https://archivebase.ldas.jp npm run import:aozora-tei
   -- --account=na-kamura-1263`(冪等 upsert。再実行で更新)
   - standOff の geo は取り込み時に `metadata.analysis.places[].lat/lon` へ転記され、
     分析ページ(`/text/aozora/analysis`)の地図に表示される

## 8. チェックリスト(納品前に全部 Yes にする)

- [ ] カードは新字新仮名優先で選び、著作権満了を確認した
- [ ] 〓(外字取りこぼし)が 0 件(あれば fixes で解決した)
- [ ] persName は固有名のみ / placeName の架空地は座標なし / date は明示表現のみ
- [ ] 全表層形の全出現文脈を確認し、誤マッチ 0 件(除外はレポートに記録)
- [ ] 表記揺れは @ref で同一台帳エントリに統合した
- [ ] ビルド警告 0 件・tei_all.rng 検証 valid
- [ ] 検証レポートを残した

## 9. 重複回避

- 夏目漱石は別コーパス(archivebase `na-kamura-1263/soseki`、digital-soseki 由来)が
  既にあるため、このコーパスには入れない
- 追加前に `data/aozora/` と `scripts/aozora_specs/` の既存 slug を確認する
