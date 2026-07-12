#!/usr/bin/env python3
"""青空文庫テキスト → 教材用 TEI/XML 変換(固有表現アノテーション付き)

WORKS に登録した各作品について、青空文庫の XHTML から本文を抽出し、
人名(persName)・地名(placeName)・日付(date)のアノテーションを付与した
TEI P5 文書を data/aozora/<slug>.xml に生成する。

- 底本情報は XHTML の bibliographical_information から自動転記する
- ルビは TEI P5 の <ruby><rb>親文字</rb><rt>読み</rt></ruby> として保持
  (固有表現とは入れ子にする)。外字は Unicode 実字に置換、注記(［＃...］)は除去
- 見出し(h3/h4)があれば章 div に分割、なければ全体を 1 つの div にする
- 固有表現は作品ごとの対応表(表層形 → 台帳 ID)による辞書マッチ。
  重なりは長い表層形を優先し、除外文脈(skip)で誤マッチを防ぐ

作成方針・固有表現の基準・検証手順は docs/aozora-tei-guidelines.md を参照。

使い方:
    python3 scripts/build_aozora_tei.py [slug ...]
    (slug 省略時は全作品。data/aozora/ に出力)
"""

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "aozora"

# ---------------------------------------------------------------------------
# 青空文庫 XHTML の抽出(汎用)
# ---------------------------------------------------------------------------


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req).read()
    return raw.decode("shift_jis", errors="replace")


def gaiji_char(desc):
    m = re.search(r"U\+([0-9A-Fa-f]{4,6})", desc)
    if m:
        return chr(int(m.group(1), 16))
    m = re.search(r"(\d)-(\d{1,2})-(\d{1,2})", desc)
    if m:
        men, ku, ten = map(int, m.groups())
        try:
            if men == 1:
                b = bytes([ku + 0xA0, ten + 0xA0])
            else:
                b = bytes([0x8F, ku + 0xA0, ten + 0xA0])
            return b.decode("euc_jis_2004")
        except (UnicodeDecodeError, ValueError):
            pass
    return "〓"


# ルビ(<ruby><rb>親文字</rb><rt>読み</rt></ruby>)を、本文に現れない私用領域の
# マーカーで一旦テキストに埋め込む。こうすると clean()(外字置換・注記除去で
# 文字列長が変わる)を通した後でも、親文字の位置をずらさずに読みを取り出せる。
RUBY_S, RB_E, RUBY_E = "\ue000", "\ue001", "\ue002"
RUBY_RE = re.compile(RUBY_S + "(.*?)" + RB_E + "(.*?)" + RUBY_E, re.S)
MARKERS = re.compile("[" + RUBY_S + RB_E + RUBY_E + "]")


# 外字はテキスト注記 ※［＃「さんずい＋冩」、U+3D7C…］ でも書かれる。青空文庫では
# ※ がルビの親文字の末尾に入り、注記が </ruby> の外に置かれることがある
# (<ruby><rb>象※</rb><rt>きさかた</rt></ruby><span>［＃…］</span>)。マーカーを
# 埋めた後でも解決できるよう、間のルビ区間をまたいで置換する。
GAIJI_NOTE = re.compile("※(" + RB_E + "[^" + RUBY_E + "]*" + RUBY_E + ")?［＃(「[^］]*)］")


def gaiji_sub(t):
    return GAIJI_NOTE.sub(lambda m: gaiji_char(m.group(2)) + (m.group(1) or ""), t)


def clean(t):
    t = gaiji_sub(t)
    t = re.sub(r"［＃[^］]*］", "", t)
    return t


def parse_ruby(t):
    """マーカー入りテキスト → (親文字のみのプレーンテキスト, [(開始, 終了, 読み)])"""
    parts, spans, pos, last = [], [], 0, 0
    for m in RUBY_RE.finditer(t):
        pre = MARKERS.sub("", t[last:m.start()])
        parts.append(pre)
        pos += len(pre)
        base = MARKERS.sub("", m.group(1))
        reading = MARKERS.sub("", m.group(2)).strip()
        if base:
            parts.append(base)
            if reading:
                spans.append((pos, pos + len(base), reading))
            pos += len(base)
        last = m.end()
    parts.append(MARKERS.sub("", t[last:]))
    return "".join(parts), spans


def strip_edges(text, spans):
    """前後の空白を落とし、ルビ位置をその分ずらす"""
    lead = len(text) - len(text.lstrip())
    stripped = text.strip()
    out = []
    for s, e, r in spans:
        s2, e2 = s - lead, e - lead
        if 0 <= s2 < e2 <= len(stripped):
            out.append((s2, e2, r))
    return stripped, out


class AozoraProse(HTMLParser):
    """main_text 部を「見出し + 段落列」に線形化する汎用ウォーカー(ルビ保持)"""

    def __init__(self):
        super().__init__()
        self.sections = [{"head": None, "paras": []}]
        self.buf = []
        self.head_buf = None
        self.skip = 0        # <rp>(ルビの括弧)は捨てる
        self.in_ruby = False
        self.rb_closed = False

    def flush_para(self):
        text, spans = parse_ruby(clean("".join(self.buf)))
        self.buf = []
        text, spans = strip_edges(text, spans)
        if text:
            self.sections[-1]["paras"].append({"text": text, "ruby": spans})

    def emit(self, ch):
        """見出しの最中なら見出しバッファへ、そうでなければ本文バッファへ"""
        if self.head_buf is not None:
            self.head_buf.append(ch)
        else:
            self.buf.append(ch)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "rp":
            self.skip += 1
        elif tag == "ruby":
            self.emit(RUBY_S)
            self.in_ruby, self.rb_closed = True, False
        elif tag == "rt":
            # <rb> が省かれている場合も、ここで親文字の終わりとみなす
            if self.in_ruby and not self.rb_closed:
                self.emit(RB_E)
                self.rb_closed = True
        elif tag == "br":
            if not self.skip:
                self.flush_para()
        elif tag == "img":
            if not self.skip and "gaiji" in cls:
                self.emit(gaiji_char(a.get("alt", "")))
        elif tag in ("h3", "h4") and "midashi" in cls:
            self.flush_para()
            self.head_buf = []

    def handle_endtag(self, tag):
        if tag == "rp":
            if self.skip:
                self.skip -= 1
        elif tag == "rb":
            if self.in_ruby and not self.rb_closed:
                self.emit(RB_E)
                self.rb_closed = True
        elif tag == "ruby":
            if self.in_ruby:
                self.emit(RUBY_E)
                self.in_ruby = False
        elif tag in ("h3", "h4") and self.head_buf is not None:
            head, _ = parse_ruby(clean("".join(self.head_buf)))
            self.head_buf = None
            if head.strip():
                self.sections.append({"head": head.strip(), "paras": []})
        elif tag == "div":
            if not self.skip:
                self.flush_para()

    def handle_data(self, data):
        if self.skip:
            return
        if self.head_buf is not None:
            self.head_buf.append(data)
        else:
            self.buf.append(data)


def extract_rich(html):
    """(sections, 底本情報の行リスト) を返す。
    sections[i]["paras"] は {"text": 親文字のみの本文, "ruby": [(開始, 終了, 読み)]}。
    """
    m = re.search(r'<div class="main_text">(.*?)</div>\s*<div class="bibliographical_information">',
                  html, re.S)
    if not m:
        m = re.search(r'<div class="main_text">(.*)<div class="bibliographical_information">',
                      html, re.S)
    walker = AozoraProse()
    walker.feed(m.group(1))
    walker.flush_para()
    sections = [s for s in walker.sections if s["paras"]]

    bib = re.search(r'<div class="bibliographical_information">(.*?)</div>', html, re.S)
    bib_lines = []
    if bib:
        text = re.sub(r"<br\s*/?>", "\n", bib.group(1))
        text = re.sub(r"<[^>]+>", "", text)
        bib_lines = [l.strip() for l in text.splitlines() if l.strip()]
    return sections, bib_lines


def extract(html):
    """extract_rich のプレーンテキスト版。paras は文字列のリスト
    (固有表現の候補調査・文脈確認はこちらを使う。ルビは親文字のみ残る)。"""
    sections, bib_lines = extract_rich(html)
    plain = [{"head": s["head"], "paras": [p["text"] for p in s["paras"]]} for s in sections]
    return plain, bib_lines

# ---------------------------------------------------------------------------
# 作品レジストリ
#   persons: id -> (表示名, 注記)
#   places:  id -> (表示名, 緯度 or None, 経度 or None, 注記)   ※座標は教材用の概値
#   tags:    (種別, 表層形, 参照先)。長い表層形を優先してマッチ
#   fixes:   抽出後の文字列置換(外字の手当てなど)
# ---------------------------------------------------------------------------

P, PR, D = "place", "pers", "date"

WORKS = {
    "melos": {
        "url": "https://www.aozora.gr.jp/cards/000035/files/1567_14913.html",
        "card": "https://www.aozora.gr.jp/cards/000035/card1567.html",
        "title": "走れメロス",
        "author": "太宰治",
        "persons": {
            "melos": ("メロス", "シラクスに住む妹思いの牧人。主人公"),
            "selinuntius": ("セリヌンティウス", "シラクスの石工。メロスの竹馬の友"),
            "dionys": ("ディオニス", "シラクスの王。人間不信の暴君"),
            "philostratus": ("フィロストラトス", "セリヌンティウスの弟子"),
            "zeus": ("ゼウス", "ギリシア神話の主神"),
            "schiller": ("シルレル", "フリードリヒ・フォン・シラー(1759–1805)。本作の典拠となった詩の作者"),
        },
        "places": {
            "syracuse": ("シラクス", 37.0755, 15.2866, "イタリア・シチリア島のシラクサ"),
        },
        "tags": [
            (PR, "メロス", "melos"), (PR, "セリヌンティウス", "selinuntius"),
            (PR, "ディオニス", "dionys"), (PR, "フィロストラトス", "philostratus"),
            (PR, "ゼウス", "zeus"), (PR, "シルレル", "schiller"),
            (P, "シラクス", "syracuse"),
        ],
    },
    "rashomon": {
        "url": "https://www.aozora.gr.jp/cards/000879/files/127_15260.html",
        "card": "https://www.aozora.gr.jp/cards/000879/card127.html",
        "title": "羅生門",
        "author": "芥川龍之介",
        "persons": {},
        "places": {
            "rashomon": ("羅生門", 34.9772, 135.7418, "平安京の羅城門。現在の京都市南区に跡碑"),
            "kyoto": ("京都", 35.0116, 135.7681, None),
            "suzakuoji": ("朱雀大路", 34.99, 135.744, "平安京の中央大路・概値"),
        },
        "tags": [
            (P, "羅生門", "rashomon"), (P, "京都", "kyoto"),
            (P, "洛中", "kyoto"), (P, "朱雀大路", "suzakuoji"),
        ],
    },
    "kumonoito": {
        "url": "https://www.aozora.gr.jp/cards/000879/files/92_14545.html",
        "card": "https://www.aozora.gr.jp/cards/000879/card92.html",
        "title": "蜘蛛の糸",
        "author": "芥川龍之介",
        "persons": {
            "shaka": ("御釈迦様", "釈迦。極楽から地獄を見下ろす"),
            "kandata": ("犍陀多", "生前に蜘蛛を助けた大泥棒(カンダタ)"),
        },
        "places": {
            "gokuraku": ("極楽", None, None, "仏教的世界観上の場所(座標なし)"),
            "jigoku": ("地獄", None, None, "仏教的世界観上の場所(座標なし)"),
            "chinoike": ("血の池", None, None, "地獄の血の池(座標なし)"),
            "harinoyama": ("針の山", None, None, "地獄の針の山(座標なし)"),
            "sanzu": ("三途の河", None, None, "仏教的世界観上の場所(座標なし)"),
        },
        "tags": [
            (PR, "御釈迦様", "shaka"), (PR, "犍陀多", "kandata"),
            (P, "極楽", "gokuraku"), (P, "地獄", "jigoku"),
            (P, "血の池", "chinoike"), (P, "針の山", "harinoyama"),
            (P, "三途の河", "sanzu"),
        ],
    },
    "sangetsuki": {
        "url": "https://www.aozora.gr.jp/cards/000119/files/624_14544.html",
        "card": "https://www.aozora.gr.jp/cards/000119/card624.html",
        "title": "山月記",
        "author": "中島敦",
        "persons": {
            "richo": ("李徴", "隴西出身の詩人。虎に変じる主人公"),
            "ensan": ("袁傪", "李徴の旧友。監察御史"),
        },
        "places": {
            "rosei": ("隴西", 35.00, 104.63, "中国甘粛省・概値"),
            "konan": ("江南", 31.0, 120.0, "中国長江下流南岸の地域・概値"),
            "kakuryaku": ("虢略", 34.52, 110.88, "中国河南省霊宝付近・概値"),
            "josui": ("汝水", 33.0, 114.0, "中国河南省の川(汝河)・概値"),
            "reinan": ("嶺南", 23.13, 113.26, "中国南部(広東方面)・概値"),
            "choan": ("長安", 34.26, 108.94, "唐の都。現在の西安"),
        },
        "tags": [
            (PR, "李徴", "richo"), (PR, "袁傪", "ensan"),
            (P, "隴西", "rosei"), (P, "江南", "konan"),
            (P, "虢略", "kakuryaku"), (P, "汝水", "josui"),
            (P, "嶺南", "reinan"), (P, "長安", "choan"),
            (D, "天宝の末年", "0755"),
        ],
    },
    "maihime": {
        "url": "https://www.aozora.gr.jp/cards/000129/files/2078_15963.html",
        "card": "https://www.aozora.gr.jp/cards/000129/card2078.html",
        "title": "舞姫",
        "author": "森鴎外",
        "fixes": {"〓": "鍤"},
        "persons": {
            "toyotaro": ("太田豊太郎", "語り手。ベルリンに留学した官吏"),
            "elise": ("エリス", "エリス・ワイゲルト。ヰクトリア座の踊り子"),
            "aizawa": ("相沢謙吉", "豊太郎の親友。天方伯の秘書官"),
            "amakata": ("天方伯", "天方大臣。豊太郎を再び登用する"),
            "schaumberg": ("シヤウムベルヒ", "ヰクトリア座の座頭"),
            "weigert": ("エルンスト・ワイゲルト", "エリスの亡父。仕立物師"),
        },
        "places": {
            "berlin": ("ベルリン", 52.5200, 13.4050, "表記は「伯林」とも"),
            "unterdenlinden": ("ウンテル・デン・リンデン", 52.5170, 13.3889, "ベルリンの大通り"),
            "brandenburg": ("ブランデンブルク門", 52.5163, 13.3777, None),
            "tiergarten": ("獣苑", 52.5145, 13.3501, "ティーアガルテン"),
            "monbijou": ("モンビシユウ街", 52.5236, 13.3986, "モンビジュー・概値"),
            "kaiserhof": ("カイゼルホオフ", 52.5115, 13.3820, "ホテル・カイザーホーフ"),
            "kloster": ("クロステル街", 52.5177, 13.4114, "クロスター通り。表記は「巷」とも"),
            "victoria": ("ヰクトリア座", 52.512, 13.417, "ヴィクトリア劇場・概値"),
            "tokyo": ("東京", 35.6812, 139.7671, None),
            "yokohama": ("横浜", 35.4437, 139.6380, None),
            "saigon": ("セイゴン", 10.7626, 106.6602, "サイゴン(現ホーチミン)"),
            "brindisi": ("ブリンヂイシイ", 40.6327, 17.9418, "イタリア・ブリンディジ"),
            "paris": ("巴里", 48.8566, 2.3522, "パリ"),
            "russia": ("魯西亜", 55.7558, 37.6173, "ロシア・概値(モスクワ)"),
            "germany": ("独逸", 51.0, 10.0, "国名・概値"),
            "prussia": ("普魯西", 52.4, 13.0, "プロイセン・概値"),
            "japan": ("日本", 36.0, 138.0, "国名・概値"),
            "europe": ("欧羅巴", 50.0, 15.0, "地域名・概値"),
            "stettin": ("ステツチン", 53.4285, 14.5528, "シュチェチン(現ポーランド)"),
        },
        "tags": [
            (PR, "太田豊太郎", "toyotaro"), (PR, "豊太郎", "toyotaro"),
            (PR, "太田", "toyotaro"),
            (PR, "エリス", "elise"),
            (PR, "相沢謙吉", "aizawa"), (PR, "相沢", "aizawa"),
            (PR, "天方", "amakata"),
            (PR, "シヤウムベルヒ", "schaumberg"),
            (PR, "エルンスト、ワイゲルト", "weigert"),
            (P, "ベルリン", "berlin"), (P, "伯林", "berlin"),
            (P, "ウンテル、デン、リンデン", "unterdenlinden"),
            (P, "ブランデンブルク門", "brandenburg"),
            (P, "獣苑", "tiergarten"),
            (P, "モンビシユウ街", "monbijou"),
            (P, "カイゼルホオフ", "kaiserhof"),
            (P, "クロステル巷", "kloster"), (P, "クロステル街", "kloster"),
            (P, "ヰクトリア", "victoria"),
            (P, "東京", "tokyo"), (P, "横浜", "yokohama"),
            (P, "セイゴン", "saigon"), (P, "ブリンヂイシイ", "brindisi"),
            (P, "巴里", "paris"), (P, "魯西亜", "russia"),
            (P, "独逸", "germany"), (P, "普魯西", "prussia"),
            (P, "日本", "japan"),
            (P, "欧羅巴", "europe"), (P, "欧洲", "europe"),
            (P, "ステツチン", "stettin"),
            (D, "明治廿一年", "1888"), (D, "一月上旬", "1889-01"),
            (D, "明治二十三年一月", "1890-01"),
        ],
    },
    "toshishun": {
        "url": "https://www.aozora.gr.jp/cards/000879/files/43015_17432.html",
        "card": "https://www.aozora.gr.jp/cards/000879/card43015.html",
        "title": "杜子春",
        "author": "芥川龍之介",
        "persons": {
            "toshishun": ("杜子春", "洛陽の若者。主人公"),
            "tekkanshi": ("鉄冠子", "峨眉山に住む仙人"),
            "enma": ("閻魔大王", "地獄の王"),
        },
        "places": {
            "rakuyo": ("洛陽", 34.62, 112.45, "唐の都のひとつ。中国河南省"),
            "nishimon": ("洛陽の西の門", 34.62, 112.42, "概値"),
            "gabisan": ("峨眉山", 29.52, 103.33, "中国四川省の霊山"),
            "taizan": ("泰山", 36.26, 117.10, "中国山東省の霊山"),
            "tang": ("唐", 34.26, 108.94, "王朝名・概値(都の長安)"),
            "jigoku": ("地獄", None, None, "仏教的世界観上の場所(座標なし)"),
            "shinraden": ("森羅殿", None, None, "閻魔大王の御殿(座標なし)"),
            "chikushodo": ("畜生道", None, None, "仏教的世界観上の場所(座標なし)"),
        },
        "tags": [
            (PR, "杜子春", "toshishun"), (PR, "鉄冠子", "tekkanshi"),
            (PR, "閻魔大王", "enma"),
            (P, "洛陽", "rakuyo"), (P, "西の門", "nishimon"),
            (P, "峨眉山", "gabisan"), (P, "泰山", "taizan"),
            (P, "唐", "tang"), (P, "地獄", "jigoku"),
            (P, "森羅殿", "shinraden"), (P, "畜生道", "chikushodo"),
        ],
    },
    "takasebune": {
        "url": "https://www.aozora.gr.jp/cards/000129/files/45245_22007.html",
        "card": "https://www.aozora.gr.jp/cards/000129/card45245.html",
        "title": "高瀬舟",
        "author": "森鴎外",
        "persons": {
            "kisuke": ("喜助", "弟殺しの罪で遠島になる罪人"),
            "shobei": ("羽田庄兵衛", "高瀬舟を護送する同心"),
            "sadanobu": ("白河楽翁", "松平定信(1759–1829)。寛政の改革を主導"),
        },
        "places": {
            "takasegawa": ("高瀬川", 35.005, 135.770, "京都の運河"),
            "kyoto": ("京都", 35.0116, 135.7681, None),
            "shimogyo": ("下京", 34.99, 135.76, "概値"),
            "kamogawa": ("加茂川", 35.00, 135.772, "鴨川・概値"),
            "osaka": ("大阪", 34.6937, 135.5023, None),
            "chionin": ("智恩院", 35.0056, 135.7844, "知恩院"),
        },
        "tags": [
            (PR, "喜助", "kisuke"),
            (PR, "羽田庄兵衛", "shobei"), (PR, "庄兵衛", "shobei"), (PR, "羽田", "shobei"),
            (PR, "白河楽翁", "sadanobu"),
            (P, "高瀬川", "takasegawa"), (P, "京都", "kyoto"),
            (P, "下京", "shimogyo"), (P, "加茂川", "kamogawa"),
            (P, "大阪", "osaka"), (P, "智恩院", "chionin"),
            (D, "寛政", "1789"),
        ],
    },
}

# 追加作品は scripts/aozora_specs/<slug>.json でも登録できる(WORKS と同スキーマ。
# persons/places の値は配列、tags は [種別, 表層形, 参照] の配列)。
SPEC_DIR = Path(__file__).resolve().parent / "aozora_specs"


def load_json_specs():
    if not SPEC_DIR.is_dir():
        return
    for f in sorted(SPEC_DIR.glob("*.json")):
        spec = json.loads(f.read_text(encoding="utf-8"))
        slug = spec["slug"]
        entry = {
            "url": spec["url"],
            "card": spec["card"],
            "title": spec["title"],
            "author": spec["author"],
            "persons": {k: (v[0], v[1] if len(v) > 1 else None)
                        for k, v in spec.get("persons", {}).items()},
            "places": {k: (v[0], v[1], v[2], v[3] if len(v) > 3 else None)
                       for k, v in spec.get("places", {}).items()},
            "tags": [tuple(t) for t in spec.get("tags", [])],
        }
        if spec.get("fixes"):
            entry["fixes"] = spec["fixes"]
        WORKS[slug] = entry


# ---------------------------------------------------------------------------
# タグ付け(build_okunohosomichi_tei.py と同方式)
# ---------------------------------------------------------------------------


def xml_escape(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_ne(kind, ref, inner):
    """既に XML 断片になっている内容を固有表現要素で包む"""
    if kind == P:
        return f'<placeName ref="#{ref}">{inner}</placeName>'
    if kind == PR:
        return f'<persName ref="#{ref}">{inner}</persName>'
    return f'<date when="{ref}">{inner}</date>'


def _render(text, ranges, lo, hi):
    """[lo, hi) を、入れ子になった範囲(固有表現・ルビ)つきで XML 化する。

    ranges は (開始, 終了, 優先度, ペイロード)。優先度は固有表現=0・ルビ=1 で、
    同じ範囲なら固有表現が外側になる(persName > ruby)。範囲同士は入れ子か
    重なりなしのいずれか(交差するものは呼び出し側で落としてある)。
    """
    ranges = sorted(ranges, key=lambda r: (r[0], -r[1], r[2]))
    out, pos, i = [], lo, 0
    while i < len(ranges):
        s, e, prio, payload = ranges[i]
        if s < pos:  # 外側の範囲の内部として処理済み
            i += 1
            continue
        out.append(xml_escape(text[pos:s]))
        inner_ranges = [r for r in ranges[i + 1:] if r[0] >= s and r[1] <= e]
        inner = _render(text, inner_ranges, s, e)
        if prio == 0:
            kind, ref = payload
            out.append(wrap_ne(kind, ref, inner))
        else:
            out.append(f"<ruby><rb>{inner}</rb><rt>{xml_escape(payload)}</rt></ruby>")
        pos = e
        i += 1
    out.append(xml_escape(text[pos:hi]))
    return "".join(out)


def tag_string(text, tags, stats, ruby_spans=()):
    """本文にタグを付ける。表層形の全出現を長い順にマッチさせ(重なりは不可)、
    ルビ範囲と入れ子にして XML 断片を返す。"""
    ne = []

    def overlaps(s, e):
        return any(not (e <= cs or s >= ce) for cs, ce, _, _ in ne)

    for kind, surface, ref in sorted(tags, key=lambda t: -len(t[1])):
        start = 0
        while True:
            i = text.find(surface, start)
            if i < 0:
                break
            start = i + len(surface)
            if overlaps(i, i + len(surface)):
                continue
            ne.append((i, i + len(surface), kind, ref))
            stats[kind] += 1
            stats["used"].add((kind, surface, ref))

    # 固有表現の境界をまたぐルビ(交差)は入れ子にできないので、そのルビは落とす
    kept = []
    for rs, re_, reading in ruby_spans:
        crossing = any(
            rs < ne_e and ns < re_ and not ((ns <= rs and re_ <= ne_e) or (rs <= ns and ne_e <= re_))
            for ns, ne_e, _, _ in ne
        )
        if crossing:
            stats["ruby_dropped"] += 1
        else:
            kept.append((rs, re_, reading))
            stats["ruby"] += 1

    ranges = [(s, e, 0, (kind, ref)) for s, e, kind, ref in ne]
    ranges += [(s, e, 1, reading) for s, e, reading in kept]
    return _render(text, ranges, 0, len(text))


# ---------------------------------------------------------------------------
# TEI 生成
# ---------------------------------------------------------------------------


def build_header(spec, bib_lines):
    bib_note = "。".join(bib_lines[:4])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>{xml_escape(spec["title"])}</title>
        <author>{xml_escape(spec["author"])}</author>
        <editor>中村覚（教材用マークアップ）</editor>
        <respStmt>
          <resp>固有表現アノテーション（AI 支援・編者確認）</resp>
          <name>Claude (Anthropic) / 中村覚</name>
        </respStmt>
      </titleStmt>
      <editionStmt>
        <edition>デジタル人文学セミナー教材版（2026年7月）</edition>
      </editionStmt>
      <publicationStmt>
        <publisher>デジタル人文学セミナー「データ分析と可視化入門」</publisher>
        <pubPlace>東京</pubPlace>
        <date when="2026-07-12">2026年7月12日</date>
        <availability status="free">
          <licence>本文はパブリックドメイン（著作権保護期間満了）。マークアップ・台帳等の付加部分は CC0 1.0 で提供する。</licence>
        </availability>
      </publicationStmt>
      <notesStmt>
        <note>人名・地名・日付の固有表現アノテーションは教材用に付与したもの。地名台帳（standOff/listPlace）の座標は概値であり、架空・宗教的世界観上の場所には座標を付けない。</note>
      </notesStmt>
      <sourceDesc>
        <bibl>
          <title>{xml_escape(spec["title"])}</title><author>{xml_escape(spec["author"])}</author>
          <note>青空文庫。{xml_escape(bib_note)}。ルビ・注記は省略し、外字は Unicode 実字に置換した。<ref target="{spec["card"]}">{spec["card"]}</ref></note>
        </bibl>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <langUsage>
        <language ident="ja">日本語</language>
      </langUsage>
    </profileDesc>
    <revisionDesc>
      <change when="2026-07-12">青空文庫版から教材用 TEI を生成し、固有表現アノテーションを付与</change>
    </revisionDesc>
  </teiHeader>"""


def build_standoff(spec):
    if not spec["persons"] and not spec["places"]:
        return ""  # 空の standOff は TEI で不可
    lines = ["  <standOff>"]
    if spec["persons"]:
        lines.append("    <listPerson>")
        for pid, (name, note) in spec["persons"].items():
            lines.append(f'      <person xml:id="{pid}">')
            lines.append(f"        <persName>{xml_escape(name)}</persName>")
            if note:
                lines.append(f"        <note>{xml_escape(note)}</note>")
            lines.append("      </person>")
        lines.append("    </listPerson>")
    if spec["places"]:
        lines.append("    <listPlace>")
        for plid, (name, lat, lon, note) in spec["places"].items():
            lines.append(f'      <place xml:id="{plid}">')
            lines.append(f"        <placeName>{xml_escape(name)}</placeName>")
            if lat is not None:
                lines.append(f"        <location><geo>{lat} {lon}</geo></location>")
            if note:
                lines.append(f"        <note>{xml_escape(note)}</note>")
            lines.append("      </place>")
        lines.append("    </listPlace>")
    lines.append("  </standOff>")
    return "\n".join(lines)


def build_work(slug):
    spec = WORKS[slug]
    print(f"== {slug}: fetching {spec['url']}")
    html = fetch(spec["url"])
    sections, bib_lines = extract_rich(html)
    for old, new in spec.get("fixes", {}).items():
        # ルビ位置は本文のオフセットで持つので、長さの変わる置換は使えない
        if len(old) != len(new):
            raise SystemExit(f"fixes {old!r} -> {new!r}: 置換前後の文字数が違うとルビ位置がずれる")
        for sec in sections:
            for para in sec["paras"]:
                para["text"] = para["text"].replace(old, new)

    stats = {P: 0, PR: 0, D: 0, "ruby": 0, "ruby_dropped": 0, "used": set()}
    body_lines = ["  <text>", "    <body>"]
    for i, sec in enumerate(sections):
        head = sec["head"] or (spec["title"] if len(sections) == 1 else "")
        body_lines.append(f'      <div type="chapter" xml:id="sec{i + 1:02d}" n="{i + 1}">')
        if head:
            body_lines.append(f"        <head>{xml_escape(head)}</head>")
        for para in sec["paras"]:
            body_lines.append(
                f"        <p>{tag_string(para['text'], spec['tags'], stats, para['ruby'])}</p>")
        body_lines.append("      </div>")
    body_lines += ["    </body>", "  </text>", "</TEI>"]

    parts = [build_header(spec, bib_lines)]
    standoff = build_standoff(spec)
    if standoff:
        parts.append(standoff)
    xml = "\n".join(parts + body_lines) + "\n"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{slug}.xml"
    out.write_text(xml, encoding="utf-8")

    unused = {(k, s, r) for k, s, r in spec["tags"]} - stats["used"]
    for k, s, r in sorted(unused):
        print(f"  [警告] 一度もマッチしなかったタグ: {k} {s!r} -> {r}")
    for k, s, r in spec["tags"]:
        table = spec["places"] if k == P else spec["persons"] if k == PR else None
        if table is not None and r not in table:
            print(f"  [警告] 台帳に無い参照: {k} {s} -> {r}")
    total = sum(len(p["text"]) for s in sections for p in s["paras"])
    dropped = f" (交差のため不採用 {stats['ruby_dropped']})" if stats["ruby_dropped"] else ""
    print(f"  -> {out.relative_to(REPO_ROOT)}: {len(sections)}章 {total}字 / "
          f"persName {stats[PR]} / placeName {stats[P]} / date {stats[D]} / "
          f"ruby {stats['ruby']}{dropped}")


def main():
    load_json_specs()
    slugs = sys.argv[1:] or list(WORKS)
    for slug in slugs:
        build_work(slug)


if __name__ == "__main__":
    main()
