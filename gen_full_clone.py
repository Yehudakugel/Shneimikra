"""Batch-build the WHOLE Torah + Onkelos once with the ElevenLabs Professional clone (NOT run yet - costs money).

  ELEVENLABS_API_KEY=... ELEVENLABS_VOICE_ID=... python gen_full_clone.py [first_idx] [last_idx] [--dry-run]

- Text: gen_audio.tts_text() -> havara.py Hungarian respelling, Hashem = Adoynoy (same as the site).
- Each pasuk and each Onkelos line is synthesized ONCE and cached in data/clone_cache/<book>_<c>_<v>_<heb|onk>.mp3
  (the second Mikra reading reuses the same clip), so a crash or re-run never pays twice: it is fully resumable.
- Output per aliyah: static/audio_clone/<idx>.mp3 + <idx>.json in the same "segments" shape the player uses.
- --dry-run prints the character count and estimated API cost and calls nothing.
- CLONE_ENGINE=edge runs a FREE rehearsal of the whole pipeline with the computer voice (into CLONE_OUT, default
  static/audio_clone_rehearsal) - proves timing maps, caching and the player before paying for a single character.
- After the build: python publish_audio.py audio_clone  -> uploads the mp3s to a GitHub Release (free, permanent)
  and writes base_url into static/audio_clone/voice.json; commit only the small .json files.
Store data/clone_cache and static/audio_clone permanently (commit to the repo or a Render disk) - they ARE the finished audio."""
import json, os, subprocess, sys, time
os.environ.setdefault("HAVARA", "hungarian")
import plan, gen_audio, voice_engine
BASE = plan.BASE
ENGINE = os.environ.get("CLONE_ENGINE", "elevenlabs")          # "edge" = free rehearsal
REHEARSAL = ENGINE != "elevenlabs"
CACHE = os.path.join(BASE, "data", "clone_cache" + ("_" + ENGINE if REHEARSAL else ""))
OUT = os.environ.get("CLONE_OUT") or os.path.join(BASE, "static", "audio_clone_rehearsal" if REHEARSAL else "audio_clone")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID") if not REHEARSAL else os.environ.get("REHEARSAL_VOICE", "he-IL-AvriNeural")
PRICE_PER_1K = 0.08          # ElevenLabs API, multilingual models (elevenlabs.io/pricing/api)
SPEED = float(os.environ.get("CLONE_SPEED", "0.95"))
GAP_SHORT, GAP_LONG, SR = 0.6, 0.9, 44100
def clip(p, v, kind):
    path = os.path.join(CACHE, f"{p['book']}_{v['c']}_{v['v']}_{kind}.mp3")
    if os.path.exists(path) and os.path.getsize(path) > 1000: return path
    text = gen_audio.tts_text(v["heb"]) if kind == "heb" else gen_audio.tts_text(v["onk"], True)
    for attempt in range(6):
        try:
            data = voice_engine.synth(text, ENGINE, VOICE_ID, SPEED)
            open(path + ".tmp", "wb").write(data); os.replace(path + ".tmp", path); return path
        except Exception as e:
            print("retry", path, e, file=sys.stderr); time.sleep(3 + attempt * 5)
    raise RuntimeError("failed " + path)
def pcm(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"], capture_output=True, check=True).stdout
def build(idx):
    p = plan.PORTIONS[idx]; mp3, js = os.path.join(OUT, f"{idx}.mp3"), os.path.join(OUT, f"{idx}.json")
    if os.path.exists(mp3) and os.path.exists(js): return
    buf = bytearray(); segs = []; t = 0.0
    def add(d, gap, vi, kind):
        nonlocal t
        s = t; buf.extend(d); t += len(d) / 2 / SR; segs.append({"v": vi, "kind": kind, "start": round(s, 3), "end": round(t, 3)})
        buf.extend(b"\0\0" * int(gap * SR)); t += gap
    for vi, v in enumerate(p["verses"]):
        h, o = pcm(clip(p, v, "heb")), pcm(clip(p, v, "onk"))
        add(h, GAP_SHORT, vi, "mikra1"); add(h, GAP_SHORT, vi, "mikra2"); add(o, GAP_LONG, vi, "targum")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "64k", mp3 + ".tmp.mp3"], input=bytes(buf), check=True)
    os.replace(mp3 + ".tmp.mp3", mp3)
    json.dump({"source": "elevenlabs-clone" if not REHEARSAL else "rehearsal-" + ENGINE, "voice": VOICE_ID, "havara": "hungarian", "speed": SPEED,
               "label": "Chassidish reader voice (licensed clone)", "duration": round(t, 3), "segments": segs}, open(js, "w"))
    print("built", idx, p["parsha"], p["aliyah_en"], f"{t/60:.1f} min", flush=True)
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    a = int(args[0]) if args else 0; b = int(args[1]) if len(args) > 1 else plan.TOTAL - 1
    todo = [(p, v, k) for p in plan.PORTIONS[a:b + 1] for v in p["verses"] for k in ("heb", "onk")
            if not os.path.exists(os.path.join(CACHE, f"{p['book']}_{v['c']}_{v['v']}_{k}.mp3"))]
    chars = sum(len(gen_audio.tts_text(v["heb"]) if k == "heb" else gen_audio.tts_text(v["onk"], True)) for p, v, k in todo)
    print(f"aliyos {a}-{b}: {len(todo)} clips still to generate, {chars:,} characters, est. ${chars / 1000 * PRICE_PER_1K:,.2f}")
    if REHEARSAL: print(f"REHEARSAL with free engine '{ENGINE}' -> {OUT} (no cost)")
    if "--dry-run" in sys.argv: return
    if not REHEARSAL:
        for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
            if not os.environ.get(k): sys.exit(f"set {k} first")
    os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    man = os.path.join(OUT, "voice.json")
    if not os.path.exists(man):
        json.dump({"label": "Chassidish reader (licensed voice)" if not REHEARSAL else "Clone rehearsal (computer voice)",
                   "priority": 100 if not REHEARSAL else 5}, open(man, "w"))
    for i in range(a, b + 1): build(i)
if __name__ == "__main__":
    main()
