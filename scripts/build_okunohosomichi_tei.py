#!/usr/bin/env python3
"""『おくのほそ道』教材用 TEI/XML ビルダー

青空文庫の『おくのほそ道』(図書カード 61619、松尾芭蕉、杉浦正一郎校註、
底本「芭蕉 おくのほそ道」岩波版ほるぷ図書館文庫 1975)から本文を抽出し、
固有表現(人名 persName・地名 placeName・日付 date)のアノテーションを
付与した data/okunohosomichi.xml を生成する。

- 章段構成・見出しは底本(杉浦校註版)に従う。杉浦の脚註・解説・凡例は収録しない
- ルビ・返り点は省略、外字は Unicode 実字に置換
- 各章段の日付(@when、グレゴリオ暦)は本文中の日付表記と『曾良旅日記』に
  基づく推定行程による。旧暦→新暦の換算は元禄2年(1689)の各月朔日対照表による
- 地名は standOff/listPlace の地名台帳(座標つき)に @ref で紐づける。
  座標は教材用の概値
- 人名は standOff/listPerson の人物台帳に @ref で紐づける

使い方:
    python3 scripts/build_okunohosomichi_tei.py
    (リポジトリのどこから実行してもよい。data/okunohosomichi.xml を上書きする)
"""

import datetime
import io
import re
import sys
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

AOZORA_URL = "https://www.aozora.gr.jp/cards/002240/files/61619_78128.html"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "okunohosomichi.xml"

# ---------------------------------------------------------------------------
# 1. 青空文庫 XHTML の取得と本文抽出
# ---------------------------------------------------------------------------


def fetch_aozora_html():
    req = urllib.request.Request(AOZORA_URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req).read()
    return raw.decode("shift_jis", errors="replace")


def gaiji_char(desc):
    """外字注記(alt 属性や ［＃...］ 注記)から Unicode 実字を得る"""
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


def gaiji_sub(t):
    return re.sub(r"※［＃(「[^］]*)］", lambda m: gaiji_char(m.group(1)), t)


class AozoraWalker(HTMLParser):
    """main_text 部を線形走査して章段(title, blocks)を組み立てる。

    - <h4 class="dogyo-naka-midashi"> = 章段見出し
    - <div class="sho1">(脚註)・<sup>(注番号)・ルビの rt/rp は除外
    - 字下げ div 内の行 = 句(verse)
    - <div class="chitsuki_*"> = 直前の句の作者名(前書きの頁参照は脚註扱いで空になる)
    """

    SKIP_DIV = ("sho1", "jizume")
    SKIP_INLINE = {"rt", "rp", "sub", "sup"}

    def __init__(self):
        super().__init__()
        self.sections = []
        self.cur_sec = None
        self.stack = []
        self.buf = []
        self.in_h4 = False
        self.h4_buf = []
        self.verse_buf = []
        self.attr_buf = None

    def skipping(self):
        return any(s[2] for s in self.stack)

    def in_jisage(self):
        return any(("jisage" in s[1] or "burasage" in s[1]) for s in self.stack if s[0] == "div")

    def flush_p(self):
        t = "".join(self.buf)
        self.buf = []
        t = gaiji_sub(t)
        t = re.sub(r"［＃[^］]*］", "", t)
        t = re.sub(r"\n+", "\n", t).strip()
        if t and self.cur_sec is not None:
            for para in t.split("\n"):
                para = para.strip()
                if para:
                    self.cur_sec["blocks"].append({"type": "p", "text": para})

    def flush_verse_line(self):
        t = "".join(self.verse_buf).strip()
        self.verse_buf = []
        t = gaiji_sub(t)
        t = re.sub(r"［＃[^］]*］", "", t)
        if t and self.cur_sec is not None:
            blocks = self.cur_sec["blocks"]
            if blocks and blocks[-1]["type"] == "verse":
                blocks[-1]["lines"].append(t)
                blocks[-1]["by"].append(None)
            else:
                blocks.append({"type": "verse", "lines": [t], "by": [None]})

    def emit(self, ch):
        if self.attr_buf is not None:
            self.attr_buf.append(ch)
        elif self.in_h4:
            self.h4_buf.append(ch)
        elif self.in_jisage():
            self.verse_buf.append(ch)
        else:
            self.buf.append(ch)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "br":
            if not self.skipping():
                if self.in_jisage():
                    self.flush_verse_line()
                else:
                    self.buf.append("\n")
            return
        if tag == "img":
            if not self.skipping() and "gaiji" in cls:
                self.emit(gaiji_char(a.get("alt", "")))
            return
        skip = False
        if tag == "div" and any(k in cls for k in self.SKIP_DIV):
            skip = True
        if tag in self.SKIP_INLINE:
            skip = True
        if tag == "span" and ("sho1" in cls or "caption" in cls):
            skip = True
        if tag == "div" and "chitsuki" in cls and not self.skipping():
            if self.in_jisage():
                self.flush_verse_line()
            self.attr_buf = []
        if tag == "div" and ("jisage" in cls or "burasage" in cls) and not self.skipping():
            self.flush_p()
        if tag == "h4" and not self.skipping():
            self.flush_p()
            self.in_h4 = True
            self.h4_buf = []
        self.stack.append((tag, cls, skip))

    def handle_endtag(self, tag):
        if tag in ("br", "img"):
            return
        if tag == "h4" and self.in_h4:
            self.in_h4 = False
            title = gaiji_sub("".join(self.h4_buf)).strip()
            if title:
                if self.cur_sec is not None:
                    self.flush_verse_line()
                self.sections.append({"title": title, "blocks": []})
                self.cur_sec = self.sections[-1]
        while self.stack:
            t, cls, _ = self.stack.pop()
            if t == tag:
                if t == "div" and "chitsuki" in cls and self.attr_buf is not None:
                    at = gaiji_sub("".join(self.attr_buf))
                    at = re.sub(r"［＃[^］]*］", "", at).strip()
                    self.attr_buf = None
                    if at and self.cur_sec is not None and self.cur_sec["blocks"]:
                        last = self.cur_sec["blocks"][-1]
                        if last["type"] == "verse" and last["lines"]:
                            last["by"][-1] = at
                if t == "div" and ("jisage" in cls or "burasage" in cls):
                    self.flush_verse_line()
                break

    def handle_data(self, data):
        if self.skipping():
            return
        self.emit(data)


def extract_sections(html):
    m = re.search(
        r'<div class="main_text">(.*?)<div class="bibliographical_information">',
        html,
        re.S,
    )
    walker = AozoraWalker()
    walker.feed(m.group(1))
    walker.flush_p()
    secs = [s for s in walker.sections if s["blocks"]]
    titles = [s["title"] for s in secs]
    secs = secs[: titles.index("跋") + 1]  # 杉浦の解説等は除外
    # 「跋」末尾に紛れ込む次見出し由来の行を除去
    for s in secs:
        for b in s["blocks"]:
            if b["type"] == "verse":
                keep = [(l, by) for l, by in zip(b["lines"], b["by"]) if l != "解説"]
                b["lines"] = [l for l, _ in keep]
                b["by"] = [by for _, by in keep]
        s["blocks"] = [b for b in s["blocks"] if b["type"] != "verse" or b["lines"]]
    return secs


# ---------------------------------------------------------------------------
# 2. 暦の換算(元禄2年 旧暦 → グレゴリオ暦)
#    各月朔日の対照: 日本暦日にもとづく(元禄2年は閏1月あり)
# ---------------------------------------------------------------------------

GENROKU2_TSUITACHI = {
    3: datetime.date(1689, 4, 20),
    4: datetime.date(1689, 5, 19),
    5: datetime.date(1689, 6, 17),
    6: datetime.date(1689, 7, 17),
    7: datetime.date(1689, 8, 15),
    8: datetime.date(1689, 9, 14),
    9: datetime.date(1689, 10, 13),
}

KANSUJI = "〇一二三四五六七八九"


def kanji_num(n):
    if n <= 10:
        return "十" if n == 10 else KANSUJI[n]
    tens, ones = divmod(n, 10)
    s = ("" if tens == 1 else KANSUJI[tens]) + "十"
    return s + (KANSUJI[ones] if ones else "")


def genroku2(month, day):
    """(旧暦月, 日) → (ISO日付文字列, 和暦ラベル)"""
    g = GENROKU2_TSUITACHI[month] + datetime.timedelta(days=day - 1)
    label = f"元祿二年{kanji_num(month)}月{kanji_num(day)}日"
    return g.isoformat(), label

# ---------------------------------------------------------------------------
# 3. 人物台帳 (standOff/listPerson)
# ---------------------------------------------------------------------------

PERSONS = {
    "sora": ("曾良", "河合曾良(1649–1710)。芭蕉の門人で本旅の随行者"),
    "sampu": ("杉風", "杉山杉風(1647–1732)。江戸の門人・魚問屋"),
    "sodo": ("素堂", "山口素堂(1642–1716)。俳人"),
    "anteki": ("原安適", "江戸の歌人・医師"),
    "jokushi": ("濁子", "中川濁子。江戸の門人"),
    "kukai": ("空海", "空海(774–835)。真言宗開祖"),
    "konohana": ("木の花さくや姫", "木花咲耶姫。記紀神話の女神"),
    "hohodemi": ("火々出見のみこと", "彦火火出見尊。記紀神話の神"),
    "gozaemon": ("佛五左衞門", "日光の宿の主人"),
    "buccho": ("佛頂和尚", "仏頂禅師(1642–1715)。芭蕉参禅の師"),
    "myozenji": ("妙禪師", "高峰原妙(1238–1295)。中国宋代の禅僧"),
    "houn": ("法雲法師", "法雲(467–529)。中国梁代の僧"),
    "tamamo": ("玉藻の前", "伝説上の妖狐"),
    "yoichi": ("與市", "那須与一宗隆。屋島の合戦で扇の的を射た武士"),
    "josui": ("淨坊寺何がし", "浄法寺高勝(桃雪)。黒羽城代家老・俳人"),
    "tosui": ("桃翠", "鹿子畑翠桃。桃雪の弟・俳人"),
    "kiyosuke": ("清輔", "藤原清輔(1104–1177)。歌人・歌学者"),
    "tokyu": ("等窮", "相楽等窮(1638–1715)。須賀川の俳人"),
    "noin": ("能因法師", "能因(988–?)。平安中期の歌人"),
    "yoshitsune": ("義經", "源義経(1159–1189)"),
    "benkei": ("辨慶", "武蔵坊弁慶。義経の郎党"),
    "satoshoji": ("佐藤庄司", "佐藤基治。義経に仕えた佐藤継信・忠信兄弟の父"),
    "sanekata": ("藤中將實方", "藤原実方(?–999)。歌人。陸奥に左遷され客死"),
    "kaemon": ("加右衞門", "北野屋加右衛門。仙台の画工・俳人"),
    "azumahito": ("大野朝臣東人", "大野東人(?–742)。多賀城を築いた将軍"),
    "asakari": ("惠美朝臣𤢥", "恵美朝狩。藤原仲麻呂の子。多賀城を修造"),
    "shomu": ("聖武皇帝", "聖武天皇(701–756)"),
    "izumisaburo": ("和泉三郎", "藤原忠衡(1167–1189)。秀衡の三男"),
    "ungo": ("雲居禪師", "雲居希膺(1582–1659)。瑞巌寺中興の禅僧"),
    "heishiro": ("眞壁の平四郎", "法身性西。鎌倉期の禅僧、瑞巌寺開山と伝わる"),
    "kenbutsu": ("見仏聖", "見仏上人。松島雄島で法華経を読誦した聖"),
    "hidehira": ("秀衡", "藤原秀衡(?–1187)。奥州藤原氏三代"),
    "yasuhira": ("康衡", "藤原泰衡(1155–1189)。本文の表記は「康衡」"),
    "kanefusa": ("兼房", "増尾十郎兼房。義経最期の伝説上の老臣"),
    "jikaku": ("慈覺大師", "円仁(794–864)。天台宗三世座主・立石寺開基"),
    "seifu": ("清風", "鈴木清風(1651–1721)。尾花沢の紅花問屋・俳人"),
    "sakichi": ("圖司左吉", "図司呂丸(?–1693)。羽黒山麓手向の染物屋・俳人"),
    "egaku": ("會覺阿闍利", "会覚。羽黒山別当代"),
    "nojo": ("能除大師", "能除太子。羽黒山開祖と伝わる"),
    "gyoson": ("行尊僧正", "行尊(1055–1135)。天台座主・歌人"),
    "kansho": ("干將", "干将。中国春秋時代の刀工"),
    "bakuya": ("莫耶", "莫耶。干将の妻"),
    "shigeyuki": ("長山氏重行", "長山重行。鶴岡藩士・俳人"),
    "fugyoku": ("淵庵不玉", "伊東不玉(1648–1697)。酒田の医師・俳人"),
    "saigyo": ("西行", "西行法師(1118–1190)。歌人。本旅は西行五百回忌の年"),
    "jingu": ("神功后宮", "神功皇后"),
    "seishi": ("西施", "中国春秋時代の越の美女"),
    "teiji": ("低耳", "宮部低耳。美濃の商人・俳人"),
    "issho": ("一笑", "小杉一笑(1653–1688)。金沢の俳人。前年に没"),
    "kasho": ("何處", "金沢に通う大坂の商人・俳人"),
    "sanemori": ("眞盛", "斎藤別当実盛(?–1183)。篠原の合戦で討死"),
    "yoshitomo": ("義朝公", "源義朝(1123–1160)"),
    "yoshinaka": ("木曾義仲", "源義仲(1154–1184)"),
    "higuchi": ("樋口の次郎", "樋口兼光。義仲四天王の一人"),
    "kazan": ("花山の法皇", "花山法皇(968–1008)。西国三十三所巡礼を再興"),
    "kumenosuke": ("久米之助", "泉屋久米之助。山中温泉の宿の主人(俳号桃妖)"),
    "teishitsu": ("貞室", "安原貞室(1610–1673)。京の俳人"),
    "teitoku": ("貞徳", "松永貞徳(1571–1654)。貞門俳諧の祖"),
    "dogen": ("道元禪師", "道元(1200–1253)。曹洞宗開祖・永平寺開山"),
    "tosai": ("等栽", "神戸洞哉。福井の隠士・俳人"),
    "hokushi": ("北枝", "立花北枝(?–1718)。金沢の門人"),
    "chuai": ("仲哀天皇", "仲哀天皇。氣比神宮祭神"),
    "yugyo": ("遊行二世の上人", "他阿真教(1237–1319)。時宗遊行二世"),
    "amaya": ("天屋何某", "天屋五郎右衛門。敦賀の廻船問屋・俳人"),
    "rotsu": ("露通", "八十村路通(1649?–1738)。門人"),
    "etsujin": ("越人", "越智越人(1656–?)。尾張の門人"),
    "joko": ("如行", "近藤如行(?–1708)。大垣の門人"),
    "zensen": ("前川子", "津田前川。大垣藩士・俳人"),
    "keiko": ("荊口", "宮崎荊口。大垣藩士・俳人"),
    "kasane": ("かさね", "那須野で馬の後を追ってきた農家の少女"),
    "tonobe": ("戸部某", "芦野資俊(俳号桃酔)。芦野の領主"),
    "oyamazumi": ("大山ずみ", "大山祇神。記紀神話の山の神"),
    "soryu": ("素龍", "柏木素龍(?–1716)。能書家。素龍清書本の筆者"),
}

# ---------------------------------------------------------------------------
# 4. 地名台帳 (standOff/listPlace)  座標は教材用の概値
# ---------------------------------------------------------------------------

PLACES = {
    "fuji": ("富士山", 35.3606, 138.7274, None),
    "ueno": ("上野", 35.7141, 139.7774, "江戸・上野"),
    "yanaka": ("谷中", 35.7280, 139.7672, None),
    "senju": ("千住", 35.7496, 139.8046, "旅立ちの地"),
    "soka": ("草加", 35.8254, 139.8055, None),
    "muronoyashima": ("室の八島", 36.4066, 139.7401, "大神神社(栃木市)"),
    "nikko": ("日光", 36.7580, 139.5986, "日光山・東照宮"),
    "kurokamiyama": ("黒髪山", 36.7652, 139.4906, "男体山の古名"),
    "urami": ("裏見の滝", 36.7412, 139.5629, None),
    "kurobane": ("黒羽", 36.8557, 140.1211, None),
    "nasuno": ("那須野", 36.93, 140.05, "那須野が原・概値"),
    "nasuhachiman": ("那須神社", 36.8785, 140.1258, "本文の「八幡宮」"),
    "komyoji": ("修験光明寺跡", 36.87, 140.13, "大田原市余瀬・概値"),
    "unganji": ("雲巌寺", 36.7891, 140.2103, None),
    "sesshoseki": ("殺生石", 37.0967, 139.9758, None),
    "ashino": ("芦野", 36.9958, 140.1683, "遊行柳の里"),
    "shirakawaseki": ("白河の関", 36.9367, 140.2280, None),
    "kyoto": ("京都", 35.0116, 135.7681, "本文の「都」「洛」"),
    "abukuma": ("阿武隈川", 36.98, 140.44, "渡河点付近・概値"),
    "bandai": ("磐梯山", 37.6014, 140.0728, "本文の「會津根」"),
    "iwaki": ("岩城", 37.0505, 140.8877, "磐城平・概値"),
    "soma": ("相馬", 37.7967, 140.9195, None),
    "miharu": ("三春", 37.4411, 140.4931, None),
    "hitachi": ("常陸", 36.3418, 140.4468, "国名・概値(水戸)"),
    "shimotsuke": ("下野", 36.5551, 139.8828, "国名・概値(宇都宮)"),
    "kagenuma": ("かげ沼", 37.30, 140.36, "須賀川近郊・概値"),
    "sukagawa": ("須賀川", 37.2861, 140.3728, None),
    "hiwada": ("日和田", 37.4586, 140.3711, "本文の「檜皮の宿」"),
    "asakayama": ("安積山", 37.4614, 140.3672, None),
    "nihonmatsu": ("二本松", 37.5847, 140.4314, None),
    "kurozuka": ("黒塚", 37.5772, 140.4468, "安達ヶ原"),
    "fukushima": ("福島", 37.7608, 140.4747, None),
    "shinobu": ("信夫の里", 37.7702, 140.5150, "文知摺石の所在地"),
    "tsukinowa": ("月の輪の渡し", 37.79, 140.49, "阿武隈川の渡し・概値"),
    "senoue": ("瀬の上", 37.8092, 140.4858, None),
    "iizuka": ("飯塚", 37.8236, 140.4453, "現在の飯坂温泉"),
    "sabano": ("鯖野", 37.8095, 140.4432, "医王寺"),
    "maruyama": ("丸山", 37.8219, 140.4369, "大鳥城跡"),
    "koori": ("桑折", 37.8471, 140.5183, None),
    "dateokido": ("伊達の大木戸", 37.877, 140.553, "国見町・概値"),
    "abumizuri": ("鐙摺", 37.94, 140.65, "白石市斎川付近・概値"),
    "shiroishi": ("白石", 38.0025, 140.6197, None),
    "kasajima": ("笠島", 38.156, 140.837, "名取市愛島笠島・概値"),
    "minowa": ("箕輪", 38.16, 140.83, "概値"),
    "iwanuma": ("岩沼", 38.1043, 140.8702, None),
    "takekuma": ("武隈の松", 38.1096, 140.8661, "二木の松"),
    "natorigawa": ("名取川", 38.17, 140.88, "渡河点付近・概値"),
    "sendai": ("仙台", 38.2682, 140.8694, None),
    "miyagino": ("宮城野", 38.263, 140.91, "概値"),
    "tamada": ("玉田", 38.272, 140.935, "歌枕・概値"),
    "yokono": ("横野", 38.268, 140.925, "歌枕・概値"),
    "tsutsujigaoka": ("榴ヶ岡", 38.2603, 140.8935, None),
    "konoshita": ("木の下", 38.2528, 140.9284, None),
    "yakushido": ("薬師堂", 38.2531, 140.9289, "陸奥国分寺薬師堂"),
    "tenjin": ("榴岡天満宮", 38.2648, 140.8952, "本文の「天神の御社」"),
    "okuhosomichi": ("おくの細道", 38.298, 140.958, "岩切付近の歌枕・概値"),
    "tagajo": ("多賀城", 38.3061, 140.9887, None),
    "tsubonoishibumi": ("壺の碑", 38.3044, 140.9886, "多賀城碑"),
    "nodatamagawa": ("野田の玉川", 38.297, 141.006, "概値"),
    "okinoishi": ("沖の石", 38.2958, 141.0064, None),
    "suenomatsuyama": ("末の松山", 38.2972, 141.0087, None),
    "shiogama": ("塩竈", 38.3178, 141.0217, None),
    "magakigashima": ("籬が島", 38.3169, 141.0355, None),
    "shiogamajinja": ("鹽竈神社", 38.3196, 141.0107, None),
    "matsushima": ("松島", 38.3688, 141.0632, None),
    "ojima": ("雄島", 38.3639, 141.0608, None),
    "dotei": ("洞庭湖", 29.32, 112.90, "中国湖南省"),
    "seiko": ("西湖", 30.243, 120.150, "中国杭州"),
    "sekko": ("浙江", 30.25, 120.17, "銭塘江"),
    "matsugaurashima": ("松が浦島", 38.31, 141.06, "七ヶ浜・概値"),
    "zuiganji": ("瑞巌寺", 38.3721, 141.0597, None),
    "hiraizumi": ("平泉", 38.9856, 141.1140, None),
    "anewa": ("姉歯の松", 38.85, 141.02, "栗原市金成・概値"),
    "odae": ("緒絶えの橋", 38.5771, 140.9551, "大崎市古川"),
    "ishinomaki": ("石巻", 38.4344, 141.3028, None),
    "kinkasan": ("金華山", 38.2999, 141.5641, None),
    "sode": ("袖の渡り", 38.428, 141.303, "石巻・概値"),
    "obuchi": ("尾ぶちの牧", 38.52, 141.30, "伝承地・概値"),
    "mano": ("真野の萱原", 38.45, 141.33, "石巻市真野・概値"),
    "naganuma": ("長沼", 38.70, 141.10, "概値"),
    "toima": ("登米", 38.6919, 141.1876, "本文の「戸伊广」"),
    "takadachi": ("高館", 38.9903, 141.1218, "義経堂"),
    "kitakami": ("北上川", 38.99, 141.13, "平泉付近"),
    "nanbu": ("南部", 39.7036, 141.1527, "地域名・概値(盛岡)"),
    "koromogawa": ("衣川", 38.998, 141.104, None),
    "izumigajo": ("和泉が城", 38.995, 141.12, "概値"),
    "koromogaseki": ("衣が関", 38.993, 141.10, "概値"),
    "kinkeizan": ("金鶏山", 38.9878, 141.1046, None),
    "konjikido": ("光堂", 38.9942, 141.0998, "中尊寺金色堂"),
    "kyodo": ("経堂", 38.9941, 141.1001, "中尊寺経蔵"),
    "iwadeyama": ("岩出山", 38.6494, 140.8711, "本文の「岩手の里」"),
    "ogurosaki": ("小黒崎", 38.638, 140.771, "概値"),
    "mizunokojima": ("美豆の小島", 38.636, 140.765, "概値"),
    "naruko": ("鳴子温泉", 38.7383, 140.7167, None),
    "shitomae": ("尿前の関", 38.727, 140.697, None),
    "dewa": ("出羽", 38.7, 140.1, "国名・概値"),
    "mogamisho": ("最上の庄", 38.76, 140.30, "新庄付近・概値"),
    "obanazawa": ("尾花沢", 38.6006, 140.4064, None),
    "yamagata": ("山形", 38.2554, 140.3396, None),
    "risshakuji": ("立石寺", 38.3126, 140.4358, "山寺"),
    "mogamigawa": ("最上川", 38.59, 140.37, "大石田付近"),
    "oishida": ("大石田", 38.5906, 140.3722, None),
    "goten": ("碁点", 38.477, 140.385, "村山・概値"),
    "hayabusa": ("隼", 38.49, 140.39, "概値"),
    "itajikiyama": ("板敷山", 38.68, 140.12, "概値"),
    "sakata": ("酒田", 38.9146, 139.8364, None),
    "shiraito": ("白糸の滝", 38.755, 140.062, "最上峡・概値"),
    "sennindo": ("仙人堂", 38.757, 140.075, "概値"),
    "haguro": ("羽黒山", 38.7057, 139.9843, None),
    "minamidani": ("南谷", 38.700, 139.982, "羽黒山南谷・概値"),
    "gassan": ("月山", 38.5489, 140.0272, None),
    "yudono": ("湯殿山", 38.5350, 139.9891, None),
    "kaneiji": ("東叡山寛永寺", 35.7203, 139.7745, "本文の「武江東叡」"),
    "ryusen": ("龍泉", 28.08, 119.14, "中国浙江省"),
    "tsuruoka": ("鶴岡", 38.7275, 139.8267, None),
    "atsumiyama": ("温海岳", 38.60, 139.62, "本文の「あつみ山」・概値"),
    "fukura": ("吹浦", 39.055, 139.868, None),
    "kisakata": ("象潟", 39.2028, 139.9028, None),
    "chokai": ("鳥海山", 39.0989, 140.0489, None),
    "noinjima": ("能因島", 39.205, 139.905, "概値"),
    "kanmanju": ("蚶満寺", 39.2036, 139.9064, "本文の「干滿珠寺」"),
    "uyamuya": ("有耶無耶の関", 39.135, 139.875, "三崎峠・概値"),
    "akita": ("秋田", 39.7186, 140.1024, None),
    "shiogoshi": ("汐越", 39.21, 139.90, "象潟・概値"),
    "nezu": ("鼠ヶ関", 38.5569, 139.5486, "念珠関"),
    "echigo": ("越後", 37.5, 138.8, "国名・概値"),
    "etchu": ("越中", 36.70, 137.21, "国名・概値(富山)"),
    "ichiburi": ("市振", 36.9636, 137.6389, None),
    "sado": ("佐渡", 38.0186, 138.3672, None),
    "oyashirazu": ("親不知", 37.0228, 137.7139, "子不知等を含む難所"),
    "niigata": ("新潟", 37.9161, 139.0364, None),
    "ise": ("伊勢", 34.4551, 136.7254, "伊勢神宮"),
    "kurobe": ("黒部川", 36.87, 137.44, "黒部四十八ヶ瀬・概値"),
    "nago": ("那古の浦", 36.778, 137.067, "奈呉の浦(射水市)"),
    "tako": ("担籠の藤波", 36.83, 136.99, "氷見・概値"),
    "kaga": ("加賀", 36.45, 136.55, "国名・概値"),
    "ariso": ("有磯海", 36.85, 137.25, "富山湾・概値"),
    "kanazawa": ("金沢", 36.5613, 136.6562, None),
    "unohanayama": ("卯の花山", 36.67, 136.88, "概値"),
    "kurikara": ("倶利伽羅峠", 36.6706, 136.8636, None),
    "osaka": ("大坂", 34.6937, 135.5023, None),
    "komatsu": ("小松", 36.4086, 136.4453, None),
    "tadajinja": ("多太神社", 36.402, 136.452, "実盛の甲を蔵する"),
    "yamanaka": ("山中温泉", 36.2519, 136.3786, None),
    "hakusan": ("白山", 36.1553, 136.7715, "本文の「白根が嶽」"),
    "nata": ("那谷寺", 36.3161, 136.4256, None),
    "nachi": ("那智", 33.6687, 135.8900, None),
    "tanigumi": ("谷汲", 35.66, 136.60, "華厳寺。本文の「谷組」"),
    "nagashima": ("伊勢長島", 35.077, 136.735, "桑名市長島町"),
    "daishoji": ("大聖寺", 36.3054, 136.3122, None),
    "zenshoji": ("全昌寺", 36.303, 136.310, "概値"),
    "echizen": ("越前", 35.95, 136.18, "国名・概値"),
    "yoshizaki": ("吉崎", 36.2554, 136.2262, None),
    "shiogoshimatsu": ("汐越の松", 36.247, 136.235, "北潟湖畔・概値"),
    "tenryuji": ("天龍寺", 36.093, 136.297, "本文は「丸岡」とするが松岡(永平寺町)"),
    "eiheiji": ("永平寺", 36.0561, 136.3555, None),
    "fukui": ("福井", 36.0641, 136.2196, None),
    "edo": ("江戸", 35.6812, 139.7671, None),
    "tsuruga": ("敦賀", 35.6544, 136.0637, None),
    "hinagatake": ("日野山", 35.855, 136.205, "本文の「比那が嵩」・概値"),
    "asamuzu": ("浅水", 35.985, 136.215, "概値"),
    "tamae": ("玉江", 36.058, 136.208, "概値"),
    "uguisu": ("鶯の関", 35.80, 136.19, "概値"),
    "yunoo": ("湯尾峠", 35.79, 136.19, "概値"),
    "hiuchi": ("燧ヶ城", 35.7702, 136.2022, "今庄"),
    "kaeruyama": ("かへる山", 35.75, 136.17, "鹿蒜山・概値"),
    "kehi": ("氣比神宮", 35.6547, 136.0745, "本文の「けいの明神」"),
    "ironohama": ("色ヶ浜", 35.6879, 135.9557, "本文の「種の濱」"),
    "suma": ("須磨", 34.6394, 135.1133, None),
    "mino": ("美濃", 35.55, 136.75, "国名・概値"),
    "ogaki": ("大垣", 35.3671, 136.6184, "むすびの地"),
    "futami": ("二見", 34.5100, 136.7900, "二見浦"),
    "michinoku": ("みちのく", 39.0, 141.0, "地域名・概値"),
}

# ---------------------------------------------------------------------------
# 5. 章段メタデータとアノテーション対応表
#    date: (旧暦月, 日)。タグは (種別, 表層形, 参照先) で、表層形は底本の表記のまま。
#    occs を指定した場合はその出現(0はじまり)だけをタグ付けする。
# ---------------------------------------------------------------------------

P, PR, D = "place", "pers", "date"

SECTION_META = [
    {"title": "冒頭", "date": (3, 27), "note": "旅立の日を付す", "tags": [
        (P, "白川の關", "shirakawaseki"), (P, "松嶋", "matsushima"),
        (PR, "杉風", "sampu"),
    ]},
    {"title": "旅立", "date": (3, 27), "tags": [
        (D, "彌生も末の七日", "1689-05-16"),
        (P, "不二の峯", "fuji"), (P, "上野", "ueno"), (P, "谷中", "yanaka"),
        (P, "千じゆ", "senju"),
    ]},
    {"title": "草加", "date": (3, 27), "tags": [
        (D, "元祿二とせ", "1689"), (P, "早加", "soka"),
    ]},
    {"title": "室の八嶋", "date": (3, 29), "tags": [
        (P, "室の八嶋", "muronoyashima"), (P, "冨士", "fuji"),
        (PR, "曾良", "sora"), (PR, "木の花さくや姫", "konohana"),
        (PR, "火〻出見のみこと", "hohodemi"),
    ]},
    {"title": "佛五左衞門", "date": (3, 29), "note": "本文は「卅日」とする", "tags": [
        (D, "卅日", "1689-05-18"), (P, "日光山", "nikko"),
        (PR, "佛五左衞門", "gozaemon"),
    ]},
    {"title": "日光", "date": (4, 1), "tags": [
        (D, "卯月朔日", "1689-05-19"),
        (P, "二荒山", "nikko"), (P, "日光", "nikko"),
        (P, "黒髮山", "kurokamiyama"), (P, "墨髮山", "kurokamiyama"),
        (P, "松しま", "matsushima"), (P, "象㵼", "kisakata"),
        (P, "うらみの瀧", "urami"),
        (PR, "空海大師", "kukai"), (PR, "惣五郎", "sora"), (PR, "宗悟", "sora"),
        (PR, "惣五", "sora"), (PR, "曾良", "sora"),
    ]},
    {"title": "那須", "date": (4, 2), "tags": [
        (P, "那須の黒ばね", "kurobane"), (PR, "かさね", "kasane"),
    ]},
    {"title": "黒羽", "date": (4, 3), "tags": [
        (P, "黒羽", "kurobane"), (P, "那須の篠原", "nasuno"),
        (P, "八幡宮", "nasuhachiman"), (P, "修驗光明寺", "komyoji"),
        (PR, "淨坊寺何がし", "josui"), (PR, "桃翠", "tosui"),
        (PR, "玉藻の前", "tamamo"), (PR, "與市", "yoichi"),
    ]},
    {"title": "雲岩寺", "date": (4, 5), "tags": [
        (D, "卯月", "1689-05"),
        (P, "雲岸寺", "unganji"),
        (PR, "佛頂和尚", "buccho"), (PR, "妙禪師", "myozenji"),
        (PR, "法雲法師", "houn"),
    ]},
    {"title": "殺生石・遊行柳", "date": (4, 19), "tags": [
        (P, "殺生石", "sesshoseki"), (P, "蘆野の里", "ashino"),
        (PR, "戸部某", "tonobe"),
    ]},
    {"title": "白川の關", "date": (4, 20), "tags": [
        (P, "白川の關", "shirakawaseki"), (P, "都", "kyoto"),
        (PR, "清輔", "kiyosuke"),
    ]},
    {"title": "須賀川", "date": (4, 22), "tags": [
        (P, "あぶくま川", "abukuma"), (P, "會津根", "bandai"),
        (P, "岩城", "iwaki"), (P, "相馬", "soma"), (P, "三春", "miharu"),
        (P, "常陸", "hitachi"), (P, "下野", "shimotsuke"),
        (P, "かげ沼", "kagenuma"), (P, "すか川", "sukagawa"),
        (P, "白河の關", "shirakawaseki"),
        (PR, "等窮", "tokyu"),
    ]},
    {"title": "あさか沼", "date": (5, 1), "tags": [
        (P, "檜皮の宿", "hiwada"), (P, "あさか山", "asakayama"),
        (P, "二本松", "nihonmatsu"), (P, "黒塚", "kurozuka"),
        (P, "福嶋", "fukushima"),
        (PR, "等窮", "tokyu"),
    ]},
    {"title": "しのぶの里", "date": (5, 2), "tags": [
        (P, "忍ぶのさと", "shinobu"),
    ]},
    {"title": "佐藤庄司の舊跡", "date": (5, 2), "tags": [
        (D, "五月朔日", "1689-06-17"),
        (P, "月の輪のわたし", "tsukinowa"), (P, "瀬の上", "senoue"),
        (P, "飯塚の里", "iizuka"), (P, "鯖野", "sabano"), (P, "丸山", "maruyama"),
        (PR, "佐藤庄司", "satoshoji"), (PR, "庄司", "satoshoji"),
        (PR, "義經", "yoshitsune"), (PR, "辨慶", "benkei"),
    ]},
    {"title": "飯塚", "date": (5, 2), "tags": [
        (P, "飯塚", "iizuka"), (P, "桑折", "koori"),
        (P, "伊達の大木戸", "dateokido"),
    ]},
    {"title": "笠嶋", "date": (5, 4), "tags": [
        (P, "鐙摺", "abumizuri"), (P, "白石", "shiroishi"),
        (P, "笠嶋", "kasajima"), (P, "みのわ", "minowa"), (P, "蓑輪", "minowa"),
        (P, "岩沼", "iwanuma"),
        (PR, "藤中將實方", "sanekata"),
    ]},
    {"title": "武隈", "date": (5, 4), "tags": [
        (P, "武隈の松", "takekuma"), (P, "名取川", "natorigawa"),
        (PR, "能因法師", "noin"),
    ]},
    {"title": "宮城野", "date": (5, 5), "tags": [
        (P, "名取川", "natorigawa"), (P, "仙臺", "sendai"),
        (P, "宮城野", "miyagino"), (P, "玉田", "tamada"), (P, "よこ野", "yokono"),
        (P, "つゝじが岡", "tsutsujigaoka"), (P, "木の下", "konoshita"),
        (P, "藥師堂", "yakushido"), (P, "天神の御社", "tenjin"),
        (P, "松嶋", "matsushima"), (P, "塩がま", "shiogama"),
        (PR, "加右衞門", "kaemon"),
    ]},
    {"title": "壺の碑", "date": (5, 8), "tags": [
        (D, "神龜元年", "0724"), (D, "天平宝字六年", "0762"),
        (P, "おくの細道", "okuhosomichi"), (P, "多賀城", "tagajo"),
        (P, "壺碑", "tsubonoishibumi"), (P, "つぼの石ぶみ", "tsubonoishibumi"),
        (PR, "大野朝臣東人", "azumahito"), (PR, "惠美朝臣𤢥", "asakari"),
        (PR, "聖武皇帝", "shomu"),
    ]},
    {"title": "末の松山", "date": (5, 8), "tags": [
        (P, "野田の玉川", "nodatamagawa"), (P, "沖の石", "okinoishi"),
        (P, "末の松山", "suenomatsuyama"), (P, "末松山", "suenomatsuyama"),
        (P, "塩がまの浦", "shiogama"), (P, "籬が嶋", "magakigashima"),
    ]},
    {"title": "鹽釜", "date": (5, 9), "tags": [
        (D, "文治三年", "1187"),
        (P, "塩がまの明神", "shiogamajinja"),
        (PR, "和泉三郎", "izumisaburo"),
    ]},
    {"title": "松嶋", "date": (5, 9), "tags": [
        (P, "松嶋", "matsushima"), (P, "雄嶋の磯", "ojima"),
        (P, "雄嶋が磯", "ojima"), (P, "洞庭", "dotei"), (P, "西湖", "seiko"),
        (P, "浙江", "sekko"), (P, "松がうらしま", "matsugaurashima"),
        (PR, "大山ずみ", "oyamazumi"), (PR, "雲居禪師", "ungo"),
        (PR, "素堂", "sodo"), (PR, "原安適", "anteki"),
        (PR, "杉風", "sampu"), (PR, "濁子", "jokushi"),
    ]},
    {"title": "瑞巖寺", "date": (5, 11), "tags": [
        (D, "十一日", "1689-06-27"),
        (P, "瑞岩寺", "zuiganji"),
        (PR, "眞壁の平四郎", "heishiro"), (PR, "雲居禪師", "ungo"),
        (PR, "見仏聖", "kenbutsu"),
    ]},
    {"title": "平泉", "date": (5, 13), "tags": [
        (D, "十二日", "1689-06-28"),
        (P, "平和泉", "hiraizumi"), (P, "平泉", "hiraizumi"),
        (P, "あねはの松", "anewa"), (P, "緒だえの橋", "odae"),
        (P, "石の卷", "ishinomaki"), (P, "金花山", "kinkasan"),
        (P, "袖のわたり", "sode"), (P, "尾ぶちの牧", "obuchi"),
        (P, "まのゝ萱はら", "mano"), (P, "長沼", "naganuma"),
        (P, "戸伊广", "toima"), (P, "高舘", "takadachi"),
        (P, "北上川", "kitakami"), (P, "南部", "nanbu"),
        (P, "衣川", "koromogawa"), (P, "和泉が城", "izumigajo"),
        (P, "衣が關", "koromogaseki"), (P, "金鷄山", "kinkeizan"),
        (P, "光堂", "konjikido"), (P, "經堂", "kyodo"),
        (PR, "秀衡", "hidehira"), (PR, "康衡", "yasuhira"),
        (PR, "兼房", "kanefusa"),
    ]},
    {"title": "尿前の關", "date": (5, 15), "tags": [
        (P, "岩手の里", "iwadeyama"), (P, "小黒崎", "ogurosaki"),
        (P, "みづの小嶋", "mizunokojima"), (P, "なるごの湯", "naruko"),
        (P, "尿前の關", "shitomae"), (P, "出羽の國", "dewa"),
        (P, "最上の庄", "mogamisho"),
    ]},
    {"title": "尾花澤", "date": (5, 17), "tags": [
        (P, "尾花澤", "obanazawa"), (P, "都", "kyoto"),
        (PR, "清風", "seifu"),
    ]},
    {"title": "立石寺", "date": (5, 27), "tags": [
        (P, "山形", "yamagata"), (P, "立石寺", "risshakuji"),
        (P, "尾花澤", "obanazawa"),
        (PR, "慈覺大師", "jikaku"),
    ]},
    {"title": "最上川", "date": (5, 28), "tags": [
        (P, "最上川", "mogamigawa"), (P, "大石田", "oishida"),
        (P, "みちのく", "michinoku"), (P, "山形", "yamagata"),
        (P, "ごてん", "goten"), (P, "はやぶさ", "hayabusa"),
        (P, "板敷山", "itajikiyama"), (P, "酒田", "sakata"),
        (P, "白糸の瀧", "shiraito"), (P, "仙人堂", "sennindo"),
    ]},
    {"title": "羽黒", "date": (6, 3), "tags": [
        (D, "六月三日", "1689-07-19"), (D, "四日", "1689-07-20"),
        (D, "五日", "1689-07-21"), (D, "八日", "1689-07-24"),
        (P, "羽黒山", "haguro"), (P, "羽州里山", "haguro"),
        (P, "羽州黒山", "haguro"), (P, "南谷", "minamidani"),
        (P, "出羽", "dewa"),
        (P, "月山", "gassan", [0, 1]), (P, "月の山", "gassan"),
        (P, "湯殿山", "yudono"), (P, "湯殿", "yudono"),
        (P, "武江東叡", "kaneiji"), (P, "龍泉", "ryusen"),
        (PR, "圖司左吉", "sakichi"), (PR, "會覺阿闍利", "egaku"),
        (PR, "阿闍𮤠", "egaku"), (PR, "能除大師", "nojo"),
        (PR, "干將", "kansho"), (PR, "莫耶", "bakuya"),
        (PR, "行尊僧正", "gyoson"),
    ]},
    {"title": "酒田", "date": (6, 13), "tags": [
        (P, "羽黒", "haguro"), (P, "鶴が岡", "tsuruoka"),
        (P, "酒田", "sakata"), (P, "あつみ山", "atsumiyama"),
        (P, "吹浦", "fukura"), (P, "最上川", "mogamigawa"),
        (PR, "長山氏重行", "shigeyuki"), (PR, "左吉", "sakichi"),
        (PR, "淵庵不玉", "fugyoku"),
    ]},
    {"title": "象㵼", "date": (6, 16), "tags": [
        (P, "象㵼", "kisakata"), (P, "酒田", "sakata"),
        (P, "鳥海", "chokai"), (P, "能因嶌", "noinjima"),
        (P, "干滿珠寺", "kanmanju"), (P, "むや／＼の關", "uyamuya"),
        (P, "秋田", "akita"), (P, "汐ごし", "shiogoshi"),
        (P, "汐越", "shiogoshi"), (P, "松嶋", "matsushima"),
        (PR, "西行法師", "saigyo"), (PR, "神功后宮", "jingu"),
        (PR, "西施", "seishi"),
    ]},
    {"title": "越後路", "date": (7, 4), "tags": [
        (D, "文月", "1689-08"),
        (P, "酒田", "sakata"), (P, "加賀の府", "kanazawa"),
        (P, "鼠の關", "nezu"), (P, "越後", "echigo"),
        (P, "越中の國", "etchu"), (P, "一ぶりの關", "ichiburi"),
        (P, "佐渡", "sado"),
    ]},
    {"title": "市振", "date": (7, 12), "tags": [
        (P, "親しらず", "oyashirazu"), (P, "子しらず", "oyashirazu"),
        (P, "犬もどり", "oyashirazu"), (P, "駒返し", "oyashirazu"),
        (P, "越後の國", "echigo"), (P, "新潟", "niigata"), (P, "伊勢", "ise"),
        (PR, "曾良", "sora"),
    ]},
    {"title": "加賀の國", "date": (7, 14), "tags": [
        (P, "くろべ四十八か瀬", "kurobe"), (P, "那古", "nago"),
        (P, "擔籠の藤浪", "tako"), (P, "かゞの國", "kaga"),
        (P, "有磯海", "ariso"),
    ]},
    {"title": "金澤", "date": (7, 15), "tags": [
        (D, "七月中の五日", "1689-08-29"),
        (P, "卯の花山", "unohanayama"), (P, "くりからが谷", "kurikara"),
        (P, "金澤", "kanazawa"), (P, "大坂", "osaka"), (P, "小松", "komatsu"),
        (PR, "何處", "kasho"), (PR, "一笑", "issho"),
    ]},
    {"title": "太田神社", "date": (7, 25), "tags": [
        (P, "太田の神社", "tadajinja"),
        (PR, "眞盛", "sanemori"), (PR, "義朝公", "yoshitomo"),
        (PR, "木曾義仲", "yoshinaka"), (PR, "樋口の次郎", "higuchi"),
    ]},
    {"title": "那谷", "date": (7, 27), "tags": [
        (P, "山中の温泉", "yamanaka"), (P, "白根が嶽", "hakusan"),
        (P, "那谷", "nata"), (P, "那智", "nachi"), (P, "谷組", "tanigumi"),
        (PR, "花山の法皇", "kazan"),
    ]},
    {"title": "山中", "date": (7, 27), "tags": [
        (P, "山中", "yamanaka"), (P, "洛", "kyoto"),
        (P, "伊勢の國長嶋", "nagashima"),
        (PR, "久米之助", "kumenosuke"), (PR, "貞室", "teishitsu"),
        (PR, "貞徳", "teitoku"), (PR, "曾良", "sora"),
    ]},
    {"title": "全昌寺", "date": (8, 6), "note": "推定", "tags": [
        (P, "大聖持", "daishoji"), (P, "全昌寺", "zenshoji"),
        (P, "加賀", "kaga"), (P, "越前の國", "echizen"),
        (PR, "曾良", "sora"),
    ]},
    {"title": "汐越の松・天龍寺・永平寺", "date": (8, 8), "note": "推定", "tags": [
        (P, "越前", "echizen"), (P, "吉崎", "yoshizaki"),
        (P, "汐越の松", "shiogoshimatsu"), (P, "丸岡天龍寺", "tenryuji"),
        (P, "金澤", "kanazawa"), (P, "永平寺", "eiheiji"),
        (PR, "北枝", "hokushi"), (PR, "道元禪師", "dogen"),
    ]},
    {"title": "福井", "date": (8, 10), "note": "推定", "tags": [
        (P, "福井", "fukui"), (P, "江戸", "edo"), (P, "つるが", "tsuruga"),
        (PR, "等栽", "tosai"),
    ]},
    {"title": "敦賀", "date": (8, 14), "tags": [
        (D, "十四日", "1689-09-27"), (D, "十五日", "1689-09-28"),
        (D, "十六日", "1689-09-29"),
        (P, "白根が嶽", "hakusan"), (P, "比那が嵩", "hinagatake"),
        (P, "あさむづ", "asamuzu"), (P, "玉江", "tamae"),
        (P, "鶯の關", "uguisu"), (P, "湯尾峠", "yunoo"),
        (P, "燧が城", "hiuchi"), (P, "かへるやま", "kaeruyama"),
        (P, "つるが", "tsuruga"), (P, "けいの明神", "kehi"),
        (P, "種の濱", "ironohama"), (P, "須广", "suma"),
        (PR, "仲哀天皇", "chuai"), (PR, "遊行二世の上人", "yugyo"),
        (PR, "遊行", "yugyo"), (PR, "天屋何某", "amaya"),
        (PR, "等栽", "tosai"),
    ]},
    {"title": "大垣", "date": (8, 21), "note": "推定", "tags": [
        (D, "長月六日", "1689-10-18"),
        (P, "みのゝ國", "mino"), (P, "大垣", "ogaki"), (P, "伊勢", "ise"),
        (P, "ふたみ", "futami"),
        (PR, "露通", "rotsu"), (PR, "曾良", "sora"), (PR, "越人", "etsujin"),
        (PR, "如行", "joko"), (PR, "前川子", "zensen"), (PR, "荊口", "keiko"),
    ]},
    {"title": "跋", "date": None, "tags": []},
]

# 句・歌の前書き(lg の head として扱う)
KOTOBAGAKI = {"祭禮", "ある草庵にいざなはれて", "途中唫", "小松と云所にて",
              "岩上に睢鳩の巣をみる"}
KOTOBAGAKI_PREFIX = ("壺碑",)

# 上句・下句で 1 首をなす和歌(連続する 2 行を 1 つの lg にまとめる)
WAKA_PAIRS = {("終宵嵐に波をはこばせて", "月をたれたる汐越の松")}

# 句の作者名 → 人物台帳
ATTRIB_PERSON = {"曾良": "sora", "低耳": "teiji", "西行": "saigyo"}

# ---------------------------------------------------------------------------
# 6. インラインタグ付け
# ---------------------------------------------------------------------------


def xml_escape(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_tag(kind, surface, ref):
    inner = xml_escape(surface)
    if kind == P:
        return f'<placeName ref="#{ref}">{inner}</placeName>'
    if kind == PR:
        return f'<persName ref="#{ref}">{inner}</persName>'
    return f'<date when="{ref}">{inner}</date>'


def tag_string(text, tags, counters, stats):
    """text 中の表層形をタグ付けして XML 文字列を返す。

    counters は章段内で表層形ごとの出現番号を数える(occs 指定の判定用)。
    先に長い表層形から処理し、重複領域はタグ付けしない。
    """
    claimed = []  # (start, end, xml)

    def overlaps(s, e):
        return any(not (e <= cs or s >= ce) for cs, ce, _ in claimed)

    for tag in sorted(tags, key=lambda t: -len(t[1])):
        kind, surface, ref = tag[0], tag[1], tag[2]
        occs = tag[3] if len(tag) > 3 else None
        start = 0
        while True:
            i = text.find(surface, start)
            if i < 0:
                break
            start = i + len(surface)
            if overlaps(i, i + len(surface)):
                continue
            n = counters.setdefault((kind, surface, ref), 0)
            counters[(kind, surface, ref)] = n + 1
            if occs is not None and n not in occs:
                continue
            claimed.append((i, i + len(surface), render_tag(kind, surface, ref)))
            stats[kind] += 1
            stats["used"].add((kind, surface, ref))
    claimed.sort()
    out = []
    pos = 0
    for s, e, xml in claimed:
        out.append(xml_escape(text[pos:s]))
        out.append(xml)
        pos = e
    out.append(xml_escape(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# 7. TEI 生成
# ---------------------------------------------------------------------------

TEI_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>おくのほそ道</title>
        <author>松尾芭蕉</author>
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
        <note>各章段の日付（date/@when、グレゴリオ暦）は、本文中の日付表記と『曾良旅日記』に基づく推定行程から教材用に付与したものである。旧暦から新暦への換算は元禄2年（1689）の各月朔日対照表による。本作品は文学作品であり、日付や叙述順は実際の行程と一致しない場合がある。</note>
        <note>地名台帳（standOff/listPlace）の座標は教材用の概値であり、歌枕など所在に諸説ある地は伝承地等によった。</note>
      </notesStmt>
      <sourceDesc>
        <bibl>
          <title>おくのほそ道</title><author>松尾芭蕉</author><editor>杉浦正一郎（校註）</editor>
          <note>青空文庫（図書カード No.61619）。底本:「芭蕉　おくのほそ道」岩波版ほるぷ図書館文庫、岩波書店、1975年。底本の親本:「おくのほそ道」岩波文庫。素龍清書本の翻刻にあたる本文のみを収録し、脚註・解説・凡例・ルビ・返り点は収録しない。外字は Unicode 実字に置換した。<ref target="https://www.aozora.gr.jp/cards/002240/card61619.html">https://www.aozora.gr.jp/cards/002240/card61619.html</ref></note>
        </bibl>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <langUsage>
        <language ident="ja">日本語（近世の文語。旧字・歴史的仮名遣い）</language>
      </langUsage>
    </profileDesc>
    <revisionDesc>
      <change when="2026-07-12">青空文庫版から教材用 TEI を生成し、固有表現アノテーションを付与</change>
    </revisionDesc>
  </teiHeader>"""


def build_standoff():
    lines = ["  <standOff>", "    <listPerson>"]
    for pid, (name, note) in PERSONS.items():
        lines.append(f'      <person xml:id="{pid}">')
        lines.append(f"        <persName>{xml_escape(name)}</persName>")
        if note:
            lines.append(f"        <note>{xml_escape(note)}</note>")
        lines.append("      </person>")
    lines.append("    </listPerson>")
    lines.append("    <listPlace>")
    for plid, (name, lat, lon, note) in PLACES.items():
        lines.append(f'      <place xml:id="{plid}">')
        lines.append(f"        <placeName>{xml_escape(name)}</placeName>")
        lines.append(f"        <location><geo>{lat} {lon}</geo></location>")
        if note:
            lines.append(f"        <note>{xml_escape(note)}</note>")
        lines.append("      </place>")
    lines.append("    </listPlace>")
    lines.append("  </standOff>")
    return "\n".join(lines)


def is_kotobagaki(line):
    return line in KOTOBAGAKI or line.startswith(KOTOBAGAKI_PREFIX)


def render_verse_block(block, tags, counters, stats):
    """verse ブロックを <cit><quote><lg>...</lg></quote>[<bibl>]</cit> 列に変換"""
    out = []
    lines = block["lines"]
    bys = block["by"]
    i = 0
    pending_head = None
    while i < len(lines):
        line, by = lines[i], bys[i]
        if is_kotobagaki(line):
            pending_head = tag_string(line, tags, counters, stats)
            i += 1
            continue
        unit_lines = [line]
        unit_by = by
        if (i + 1 < len(lines)
                and (line, lines[i + 1]) in WAKA_PAIRS):
            unit_lines.append(lines[i + 1])
            unit_by = bys[i + 1] or by
            i += 1
        lg_type = "waka" if len(unit_lines) > 1 else "hokku"
        parts = [f'<cit><quote><lg type="{lg_type}">']
        if pending_head:
            parts.append(f"<head>{pending_head}</head>")
            pending_head = None
        for ul in unit_lines:
            parts.append(f"<l>{tag_string(ul, tags, counters, stats)}</l>")
        parts.append("</lg></quote>")
        if unit_by:
            pid = ATTRIB_PERSON.get(unit_by)
            if pid:
                stats[PR] += 1
                parts.append(f'<bibl><persName ref="#{pid}">{xml_escape(unit_by)}</persName></bibl>')
            else:
                parts.append(f"<bibl>{xml_escape(unit_by)}</bibl>")
        parts.append("</cit>")
        out.append("".join(parts))
        i += 1
    return out


def build_body(sections, stats):
    lines = ["  <text>", "    <body>"]
    for idx, (sec, meta) in enumerate(zip(sections, SECTION_META)):
        assert sec["title"] == meta["title"], (sec["title"], meta["title"])
        tags = meta["tags"]
        counters = {}
        if meta["title"] == "跋":
            lines.append(f'      <div type="postscript" xml:id="sec{idx:02d}" n="{idx}">')
            lines.append(f"        <head>{xml_escape(sec['title'])}</head>")
            paras = [b["text"] for b in sec["blocks"] if b["type"] == "p"]
            body_xml = "<lb/>".join(tag_string(t, tags, counters, stats) for t in paras)
            lines.append(f"        <p>{body_xml}</p>")
            lines.append("        <closer><date when=\"1694-05\">元祿七年初夏</date>"
                         "　<signed><persName ref=\"#soryu\">素龍</persName>書</signed></closer>")
            stats[D] += 1
            stats[PR] += 1
            lines.append("      </div>")
            continue
        when, wareki = genroku2(*meta["date"])
        lines.append(f'      <div type="entry" xml:id="sec{idx:02d}" n="{idx}">')
        note = meta.get("note")
        suffix = f"（{note}）" if note else ""
        lines.append(f"        <head>{xml_escape(sec['title'])}　"
                     f'<date when="{when}">{wareki}{suffix}</date></head>')
        stats[D] += 1
        chunks = []
        for block in sec["blocks"]:
            if block["type"] == "p":
                chunks.append(tag_string(block["text"], tags, counters, stats))
            else:
                chunks.extend(render_verse_block(block, tags, counters, stats))
        lines.append(f"        <p>{'<lb/>'.join(chunks)}</p>")
        lines.append("      </div>")
    lines.append("    </body>")
    lines.append("  </text>")
    lines.append("</TEI>")
    return "\n".join(lines)


def main():
    print("青空文庫から取得中...")
    html = fetch_aozora_html()
    sections = extract_sections(html)
    assert len(sections) == len(SECTION_META), (len(sections), len(SECTION_META))

    stats = {P: 0, PR: 0, D: 0, "used": set()}
    body = build_body(sections, stats)
    xml = "\n".join([TEI_HEADER, build_standoff(), body]) + "\n"
    OUT_PATH.write_text(xml, encoding="utf-8")

    # 検証: 台帳参照の整合と未使用タグの警告
    all_tags = {(t[0], t[1], t[2]) for m in SECTION_META for t in m["tags"]}
    unused = all_tags - stats["used"]
    for kind, surface, ref in sorted(unused):
        print(f"  [警告] 一度もマッチしなかったタグ: {kind} {surface!r} -> {ref}")
    for m in SECTION_META:
        for t in m["tags"]:
            if t[0] == P and t[2] not in PLACES:
                print(f"  [警告] 地名台帳に無い参照: {t}")
            if t[0] == PR and t[2] not in PERSONS:
                print(f"  [警告] 人物台帳に無い参照: {t}")
    print(f"出力: {OUT_PATH}")
    print(f"  章段: {len(sections)} / persName: {stats[PR]} / "
          f"placeName: {stats[P]} / date: {stats[D]}")
    print(f"  人物台帳: {len(PERSONS)} 人 / 地名台帳: {len(PLACES)} 箇所")


if __name__ == "__main__":
    main()
