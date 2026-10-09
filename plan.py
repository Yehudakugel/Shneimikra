"""Shared plan logic: 54 parshiyos x 7 aliyos = 378 daily portions (Sunday..Shabbos)."""
import json, re, datetime, os
BASE = os.path.dirname(os.path.abspath(__file__))
TORAH = json.load(open(os.path.join(BASE, "data/torah.json")))
# Ashkenazi transliteration + Hebrew names, in Sefaria order
NAMES = """Bereishis|בראשית;Noach|נח;Lech Lecha|לך לך;Vayeira|וירא;Chayei Sarah|חיי שרה;Toldos|תולדות;Vayeitzei|ויצא;Vayishlach|וישלח;Vayeishev|וישב;Mikeitz|מקץ;Vayigash|ויגש;Vayechi|ויחי;Shemos|שמות;Va'eira|וארא;Bo|בא;Beshalach|בשלח;Yisro|יתרו;Mishpatim|משפטים;Terumah|תרומה;Tetzaveh|תצוה;Ki Sisa|כי תשא;Vayakhel|ויקהל;Pekudei|פקודי;Vayikra|ויקרא;Tzav|צו;Shemini|שמיני;Tazria|תזריע;Metzora|מצורע;Acharei Mos|אחרי מות;Kedoshim|קדושים;Emor|אמור;Behar|בהר;Bechukosai|בחוקתי;Bamidbar|במדבר;Nasso|נשא;Beha'aloscha|בהעלתך;Shelach|שלח;Korach|קרח;Chukas|חקת;Balak|בלק;Pinchas|פינחס;Matos|מטות;Masei|מסעי;Devarim|דברים;Va'eschanan|ואתחנן;Eikev|עקב;Re'eh|ראה;Shoftim|שופטים;Ki Seitzei|כי תצא;Ki Savo|כי תבוא;Nitzavim|נצבים;Vayeilech|וילך;Ha'azinu|האזינו;Vezos Haberachah|וזאת הברכה""".split(";")
BOOK_HE = {"Genesis":"בראשית","Exodus":"שמות","Leviticus":"ויקרא","Numbers":"במדבר","Deuteronomy":"דברים"}
BOOK_EN = {"Genesis":"Bereishis","Exodus":"Shemos","Leviticus":"Vayikra","Numbers":"Bamidbar","Deuteronomy":"Devarim"}
ALIYAH_EN = ["Rishon","Sheni","Shlishi","Revi'i","Chamishi","Shishi","Shevi'i"]
ALIYAH_HE = ["ראשון","שני","שלישי","רביעי","חמישי","שישי","שביעי"]
DAY_EN = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Shabbos"]
START_DATE = datetime.date(2026, 10, 4)  # Sunday of the week of Parshas Bereishis 5787
HEB_LETTERS = "אבגדהוזחטיכלמנסעפצקרשת"
def gematria(n):
    vals=[(400,'ת'),(300,'ש'),(200,'ר'),(100,'ק'),(90,'צ'),(80,'פ'),(70,'ע'),(60,'ס'),(50,'נ'),(40,'מ'),(30,'ל'),(20,'כ'),(10,'י'),(9,'ט'),(8,'ח'),(7,'ז'),(6,'ו'),(5,'ה'),(4,'ד'),(3,'ג'),(2,'ב'),(1,'א')]
    s=""
    while n>0:
        if n in (15,16):
            s += 'טו' if n==15 else 'טז'; break
        for v,c in vals:
            if v<=n:
                s+=c; n-=v; break
    return s
def parse_ref(ref):
    # "Genesis 1:1-2:3" or "Genesis 4:19-4:22" or "Genesis 4:19-22"
    ref = ref.replace("\u2013", "-")
    m = re.match(r"(\w+) (\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$", ref)
    b, c1, v1, c2, v2 = m.groups()
    c1, v1 = int(c1), int(v1)
    c2 = int(c2) if c2 else c1
    v2 = int(v2) if v2 else v1
    return b, c1, v1, c2, v2
PORTIONS = []
for pi, p in enumerate(TORAH["parshiyos"]):
    en, he = NAMES[pi].split("|")
    for ai, ref in enumerate(p["aliyot"]):
        b, c1, v1, c2, v2 = parse_ref(ref)
        verses = []
        book = TORAH["books"][b]
        c, v = c1, v1
        while (c, v) <= (c2, v2):
            heb = book["heb"][c-1]
            if v > len(heb):
                c, v = c+1, 1; continue
            verses.append({"c": c, "v": v, "label": f"{gematria(c)}:{gematria(v)}", "heb": heb[v-1], "onk": book["onk"][c-1][v-1] if v-1 < len(book["onk"][c-1]) else ""})
            v += 1
        idx = len(PORTIONS)
        PORTIONS.append({"idx": idx, "parsha": en, "parsha_he": he, "parsha_num": pi, "aliyah": ai, "aliyah_en": ALIYAH_EN[ai], "aliyah_he": ALIYAH_HE[ai],
                         "book": b, "book_en": BOOK_EN[b], "book_he": BOOK_HE[b], "ref": ref, "verses": verses,
                         "day_en": DAY_EN[ai], "date": (START_DATE + datetime.timedelta(days=idx)).isoformat()})
TOTAL = len(PORTIONS)
def scheduled_index(today):
    d = (today - START_DATE).days
    return max(0, min(TOTAL-1, d))
END_DATE = START_DATE + datetime.timedelta(days=TOTAL-1)
