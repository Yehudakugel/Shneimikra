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
CACHE = os.path.join(BASE, "data/tts_cache" + ("_hu" if HAVARA == "hungarian" else "")); os.makedirs(CACHE, exist_ok=True)
OUT = os.path.join(BASE, "static/audio"); os.makedirs(OUT, exist_ok=True)
VOICE = "he-IL-AvriNeural"; RATE = "-12%"; SR = 24000
GAP_SHORT, GAP_LONG = 0.55, 0.9
NIKUD = re.compile(r"[\u0591-\u05C7]")
def tts_text(t, targum=False):
    t = re.sub(r"[\u0591-\u05AF\u05BD\u05C0\u05C3\u05C6]", "", t)  # taamim, meteg, paseq, sof pasuk
    t = t.replace("\u05BE", " ").replace(":", "").replace("׃", "")
    words = []
    for w in t.split():
        cons = NIKUD.sub("", w)
        if cons.endswith("יהוה") or (targum and cons.endswith("יי") and len(cons) <= 3):
            prefix_len = len(cons) - (4 if cons.endswith("יהוה") else 2)
            # keep prefix letters (with their vowels) e.g. ו/ב/ל
            out, n = "", 0
            for ch in w:
                if not NIKUD.match(ch):
                    if n == prefix_len: break
                    n += 1
                out += ch
            name = "אֱלֹהִים" if ("\u05B4" in w and cons.endswith("יהוה")) else "אֲדֹנָי"
            if out and not name.startswith("א"): pass
            words.append(out + name)
        else:
            words.append(w)
    out = " ".join(words)
    if HAVARA == "hungarian":
        import havara
        out = havara.hungarian(out)
    return out
def key(p, v, kind): return os.path.join(CACHE, f"{p['book']}_{v['c']}_{v['v']}_{kind}.pcm")
async def synth(text, path, sem):
    if os.path.exists(path): return
    async with sem:
        for attempt in range(5):
            try:
                buf = b""
                async for chunk in edge_tts.Communicate(text, VOICE, rate=RATE).stream():
                    if chunk["type"] == "audio": buf += chunk["data"]
                pcm = subprocess.run(["ffmpeg","-v","error","-i","pipe:0","-f","s16le","-ac","1","-ar",str(SR),"pipe:1"], input=buf, capture_output=True, check=True).stdout
                open(path, "wb").write(pcm); return
            except Exception as e:
                print("retry", path, e, file=sys.stderr); await asyncio.sleep(2 + attempt*3)
        raise RuntimeError("TTS failed " + path)
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
        h = open(key(p, v, "heb"), "rb").read(); o = open(key(p, v, "onk"), "rb").read()
        add(h, GAP_SHORT, vi, "mikra1"); add(h, GAP_SHORT, vi, "mikra2"); add(o, GAP_LONG, vi, "targum")
    subprocess.run(["ffmpeg","-v","error","-y","-f","s16le","-ac","1","-ar",str(SR),"-i","pipe:0","-c:a","libmp3lame","-b:a","48k",mp3+".tmp.mp3"], input=bytes(pcm), check=True)
    os.replace(mp3+".tmp.mp3", mp3)
    json.dump({"source": "placeholder-tts", "havara": HAVARA, "voice": VOICE, "label": "Placeholder computer voice (TTS, Hungarian Chassidish havara respelling) - to be replaced with a real reader", "duration": round(t,3), "segments": segs}, open(js, "w"))
    print("built", idx, p["parsha"], p["aliyah_en"], f"{t/60:.1f} min", flush=True)
async def main():
    a = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    b = int(sys.argv[2]) if len(sys.argv) > 2 else plan.TOTAL - 1
    sem = asyncio.Semaphore(8)
    for i in range(a, b+1):
        await build(i, sem)
if __name__ == "__main__":
    asyncio.run(main())
