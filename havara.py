"""Respell pointed Hebrew so an Israeli Hebrew TTS voice approximates Chassidish (Polish/Galician-style) havara.
Rules (each switchable): sav rafe -> ס ; kamatz gadol -> 'u' (kubutz) ; cholam -> 'oy' ; tzere -> 'ey' ; optional shuruk/kubutz -> 'i'."""
import re
DAGESH, KAMATZ, KAMATZ_KATAN, CHOLAM, CHOLAM_HASER, TZERE, KUBUTZ, CHIRIK = "\u05BC", "\u05B8", "\u05C7", "\u05B9", "\u05BA", "\u05B5", "\u05BB", "\u05B4"
MARKS = re.compile(r"[\u05B0-\u05C7]")
def chassidish(text, sav=True, kamatz="u", cholam_oy=True, tzere_ey=True, u_to_i=False):
    """kamatz: "u" (Polish/Galician), "o" (Hungarian), or None (leave as is)."""
    t = re.sub(r"[\u0591-\u05AF\u05BD\u05C0\u05C3]", "", text).replace("\u05BE", " ")
    out = []
    for w in t.split():
        # split into clusters: base letter + its marks
        cl = re.findall(r"[\u05D0-\u05EA][\u05B0-\u05C7]*", w)
        res = []
        for i, c in enumerate(cl):
            base, marks = c[0], c[1:]
            if sav and base == "ת" and DAGESH not in marks: base = "ס"
            if kamatz == "u" and KAMATZ in marks: marks = marks.replace(KAMATZ, KUBUTZ)
            if u_to_i and KUBUTZ in marks and KAMATZ not in c[1:]: marks = marks.replace(KUBUTZ, CHIRIK)
            add = ""
            if cholam_oy and (CHOLAM in marks or CHOLAM_HASER in marks):
                marks = marks.replace(CHOLAM, "").replace(CHOLAM_HASER, "")
                if base == "ו" and not res: base, marks = "ו", marks  # vav at word start keeps consonant
                if base == "ו" and res: base, marks = "", ""          # cholam male: vowel carrier only
                add = "וֹי"
                if base: add = "וֹי"
                if base == "": res.append("וֹי"); continue
            if tzere_ey and TZERE in marks and not (i + 1 < len(cl) and cl[i + 1][0] == "י"):
                add = "י"
            if kamatz == "o" and (KAMATZ in marks or KAMATZ_KATAN in marks):
                # write the "o" as cholam male (וֹ) – clearer for the TTS than a cholam dot (esp. on shin/sin)
                marks = marks.replace(KAMATZ, "").replace(KAMATZ_KATAN, "")
                add = "וֹ" + add
            res.append(base + marks + add)
        out.append("".join(res))
    return " ".join(out)
def hungarian(text):
    """Hungarian Chassidish: kamatz 'o', cholam 'oy', tzere 'ey', sav for tav without dagesh, shuruk/kubutz 'u'."""
    return chassidish(text, sav=True, kamatz="o", cholam_oy=True, tzere_ey=True, u_to_i=False)
LITVISH_CHOLAM = "\u05B5\u05D9"   # tzere + yud -> "ey" (Litvish cholam)
def litvish(text):
    """Litvish: kamatz 'o', cholam 'ey', tzere 'ey', sav."""
    t = re.sub(r"([\u05D0-\u05EA][\u05B0-\u05B8\u05BB-\u05C7]*)\u05D5[\u05B9\u05BA]", lambda m: m.group(1) + LITVISH_CHOLAM, text)
    t = t.replace(CHOLAM, LITVISH_CHOLAM).replace(CHOLAM_HASER, LITVISH_CHOLAM)
    return chassidish(t, sav=True, kamatz="o", cholam_oy=False, tzere_ey=True)
def polish(text):
    """Polish/Galician Chassidish: kamatz 'u', cholam 'oy', tzere 'ey', sav."""
    return chassidish(text, sav=True, kamatz="u", cholam_oy=True, tzere_ey=True)
def sephardi(text):
    """Sephardi/Teimani-lite: kamatz 'a', cholam 'o', tav always 't' - i.e. the text as pointed, taamim removed."""
    return chassidish(text, sav=False, kamatz=None, cholam_oy=False, tzere_ey=False)
STYLES = {"hungarian": ("Hungarian Chassidish (Boruch, oy, ey, sav)", hungarian),
          "polish": ("Polish/Galician Chassidish (Buruch, oy, ey, sav)", polish),
          "litvish": ("Litvish (Boruch, ey, sav)", litvish),
          "sephardi": ("Sephardi (Baruch, o, tav)", sephardi),
          "israeli": ("Israeli / modern (as the voice reads it)", None)}
def respell(text, style):
    f = STYLES.get(style, STYLES["hungarian"])[1]
    return f(text) if f else text
if __name__ == "__main__":
    import plan
    import gen_audio
    for v in plan.PORTIONS[0]["verses"][:3] + [plan.PORTIONS[1]["verses"][0]]: print(gen_audio.tts_text(v["heb"])); print("  T:", gen_audio.tts_text(v["onk"], True))
