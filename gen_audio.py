"""Generate PLACEHOLDER read-along audio (Hebrew TTS) for each daily portion.

Output per portion idx:
  static/audio/<idx>.mp3   - the whole portion: pasuk, pasuk again, Targum, next pasuk...
  static/audio/<idx>.json  - timing map: {"source": "...", "segments": [{"v": verse_index, "kind": "mikra1|mikra2|targum", "start": s, "end": s}]}
To swap in a real reader: drop his mp3 + a JSON with the same "segments" shape (timestamps in seconds) into static/audio/.
Usage: gen_audio.py [first_idx] [last_idx]
"""
import asyncio, json, os, re, subprocess, sys, io, wave, struct
import edge_tts
import plan
BASE = plan.BASE
HAVARA = os.environ.get("HAVARA", "hungarian")   # "hungarian" (Chassidish) or "israeli" (plain TTS reading)
CACHE = os.path.join(BASE, "data/tts_cache" + ({"hungarian": "_hu", "israeli": ""}.get(HAVARA, "_" + HAVARA))); os.makedirs(CACHE, exist_ok=True)
OUT = os.path.join(BASE, "static/audio"); os.makedirs(OUT, exist_ok=True)
VOICE = os.environ.get("TTS_VOICE", "he-IL-AvriNeural"); SR = 24000
if VOICE != "he-IL-AvriNeural": CACHE = CACHE + "_" + VOICE.replace("-", "")
# Reading speed: was "-12%" (v1); "+1%" is ~15% quicker (1.01/0.88 = 1.15x) and keeps the natural pitch.
RATE = os.environ.get("TTS_RATE", "+1%")
os.makedirs(CACHE, exist_ok=True); CACHE = CACHE + ("" if RATE == "-12%" else "_r" + RATE.replace("%", "").replace("+", "p").replace("-", "m")); os.makedirs(CACHE, exist_ok=True)
OUT = os.environ.get("AUDIO_OUT", OUT); os.makedirs(OUT, exist_ok=True)
GAP_SHORT, GAP_LONG = 0.6, 0.9      # after each Hebrew reading (sof pasuk) / after Targum
# Phrasing by the te'amim (trop): a pause is inserted after the word carrying the accent.
TROP_PAUSE = {"\u0591": 0.38,                     # etnachta (half-verse)
              "\u0592": 0.2, "\u0593": 0.2,       # segol(ta), shalshelet
              "\u0594": 0.18, "\u0595": 0.18,     # zakef katan, zakef gadol
              "\u0597": 0.12}                     # revia
NIKUD = re.compile(r"[\u0591-\u05C7]")
def tts_words(t, targum=False):
    """-> list of (tts_word, pause_after_seconds). Hebrew respelled per HAVARA; Hashem's name read as Adoynoy."""
    t = t.replace("\u05BE", " ").replace("\u05C3", " ").replace(":", " ")
    out = []
    for w in t.split():
        pause = max([TROP_PAUSE.get(ch, 0) for ch in w] + [0]) if not targum else 0
        w = re.sub(r"[\u0591-\u05AF\u05BD\u05C0\u05C6]", "", w)  # taamim, meteg, paseq
        cons = NIKUD.sub("", w)
        if not re.search(r"[\u05D0-\u05EA]", cons): continue
        if cons.endswith("יהוה") or (targum and cons.endswith("יי") and len(cons) <= 3):
            prefix_len = len(cons) - (4 if cons.endswith("יהוה") else 2)
            pre, n = "", 0     # keep prefix letters (with their vowels) e.g. ו/ב/ל
            for ch in w:
                if not NIKUD.match(ch):
                    if n == prefix_len: break
                    n += 1
                pre += ch
            name = "אֱלֹהִים" if ("\u05B4" in w and cons.endswith("יהוה")) else "אֲדֹנָי"
            w = pre + name
        if HAVARA != "israeli":
            import havara
            w = havara.respell(w, HAVARA)
        if w.strip(): out.append((w.strip(), pause))
    if out: out[-1] = (out[-1][0], 0)
    return out
def tts_text(t, targum=False): return " ".join(w for w, _ in tts_words(t, targum))
def key(p, v, kind): return os.path.join(CACHE, f"{p['book']}_{v['c']}_{v['v']}_{kind}.pcm")
async def synth(text, path, sem):
    """Synthesize to raw PCM + <path>.wb.json word boundaries (seconds)."""
    if os.path.exists(path) and os.path.exists(path + ".wb.json"): return
    async with sem:
        for attempt in range(6):
            try:
                buf = b""; wb = []
                async for chunk in edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary").stream():
                    if chunk["type"] == "audio": buf += chunk["data"]
                    elif chunk["type"] == "WordBoundary": wb.append([chunk["offset"] / 1e7, (chunk["offset"] + chunk["duration"]) / 1e7, chunk["text"]])
                pcm = subprocess.run(["ffmpeg","-v","error","-i","pipe:0","-f","s16le","-ac","1","-ar",str(SR),"pipe:1"], input=buf, capture_output=True, check=True).stdout
                open(path, "wb").write(pcm); json.dump(wb, open(path + ".wb.json", "w"), ensure_ascii=False); return
            except Exception as e:
                print("retry", path, e, file=sys.stderr); await asyncio.sleep(2 + attempt*3)
        raise RuntimeError("TTS failed " + path)
STATS = {"verses": 0, "phrased": 0, "pauses": 0, "mismatch": []}
def trim(pcm, lead=0.05, tail=0.15, th=500):
    """Drop the TTS engine's built-in silence (~0.23 s before, ~0.9 s after each line); the gaps below set the pacing."""
    a = memoryview(pcm).cast("h"); n = len(a)
    i = next((k for k in range(n) if abs(a[k]) > th), 0)
    j = next((k for k in range(n - 1, -1, -1) if abs(a[k]) > th), n - 1)
    i = max(0, i - int(lead * SR)); j = min(n, j + 1 + int(tail * SR))
    return bytes(pcm[i * 2:j * 2])
def phrase(path, words):
    return trim(_phrase(path, words))
def _phrase(path, words):
    """Insert the trop pauses into the synthesized line, cutting at the TTS word boundaries."""
    pcm = open(path, "rb").read()
    wb = json.load(open(path + ".wb.json"))
    if not any(p for _, p in words): return pcm
    STATS["verses"] += 1
    if len(wb) != len(words):
        STATS["mismatch"].append(os.path.basename(path)); return pcm
    STATS["phrased"] += 1
    cuts = []
    for i, (_, p) in enumerate(words[:-1]):
        if p:
            t = (wb[i][1] + wb[i + 1][0]) / 2          # between end of this word and start of the next
            cuts.append((int(t * SR) * 2, p))
    out = bytearray(); last = 0; fade = int(0.006 * SR)
    for pos, p in cuts:
        pos = min(max(pos, last), len(pcm))
        seg = bytearray(pcm[last:pos])
        n = len(seg) // 2
        for k in range(min(fade, n)):   # tiny fade-out at the cut, avoids clicks
            j = n - 1 - k; v = struct.unpack_from("<h", seg, j * 2)[0]; struct.pack_into("<h", seg, j * 2, int(v * k / fade))
        out += seg; out += b"\0\0" * int(p * SR); last = pos; STATS["pauses"] += 1
        nxt = bytearray(pcm[last:last + fade * 2])
        for k in range(len(nxt) // 2):  # tiny fade-in after the pause
            v = struct.unpack_from("<h", nxt, k * 2)[0]; struct.pack_into("<h", nxt, k * 2, int(v * k / fade))
        out += nxt; last += len(nxt)
    out += pcm[last:]
    return bytes(out)
async def build(idx, sem):
    p = plan.PORTIONS[idx]
    mp3, js = os.path.join(OUT, f"{idx}.mp3"), os.path.join(OUT, f"{idx}.json")
    if os.path.exists(mp3) and os.path.exists(js): return
    tasks = []
    for v in p["verses"]:
        tasks.append(synth(tts_text(v["heb"]), key(p, v, "heb"), sem))
        tasks.append(synth(tts_text(v["onk"], True), key(p, v, "onk"), sem))
    await asyncio.gather(*tasks)
    pcm = bytearray(); segs = []; t = 0.0
    def add(data, gap, vi, kind):
        nonlocal t
        start = t; pcm.extend(data); t += len(data)/2/SR
        segs.append({"v": vi, "kind": kind, "start": round(start, 3), "end": round(t, 3)})
        pcm.extend(b"\0\0" * int(gap*SR)); t += gap
    for vi, v in enumerate(p["verses"]):
        h = phrase(key(p, v, "heb"), tts_words(v["heb"])); o = phrase(key(p, v, "onk"), tts_words(v["onk"], True))
        add(h, GAP_SHORT, vi, "mikra1"); add(h, GAP_SHORT, vi, "mikra2"); add(o, GAP_LONG, vi, "targum")
    subprocess.run(["ffmpeg","-v","error","-y","-f","s16le","-ac","1","-ar",str(SR),"-i","pipe:0","-c:a","libmp3lame","-b:a","48k",mp3+".tmp.mp3"], input=bytes(pcm), check=True)
    os.replace(mp3+".tmp.mp3", mp3)
    json.dump({"source": "placeholder-tts", "version": 2, "havara": HAVARA, "voice": VOICE, "rate": RATE, "trop_pauses": True, "label": "Placeholder computer voice (TTS, Hungarian Chassidish havara respelling) - to be replaced with a real reader", "duration": round(t,3), "segments": segs}, open(js, "w"))
    print("built", idx, p["parsha"], p["aliyah_en"], f"{t/60:.1f} min", flush=True)
async def main():
    a = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    b = int(sys.argv[2]) if len(sys.argv) > 2 else plan.TOTAL - 1
    sem = asyncio.Semaphore(8)
    for i in range(a, b+1):
        await build(i, sem)
    print("trop phrasing:", {k: (v if k != "mismatch" else len(v)) for k, v in STATS.items()}, flush=True)
if __name__ == "__main__":
    asyncio.run(main())
