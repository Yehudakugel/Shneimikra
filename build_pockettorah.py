"""Build the read-along audio with the Hebrew pesukim from PocketTorah (real chanted Torah reading, with trop)
and the Targum (Onkelos) from our Hungarian-havara TTS (read plainly, no trop).

PocketTorah: https://github.com/rneiss/PocketTorah  (audio + word-timing labels, CC BY-SA - see NOTICE)
  data/audio/<Parsha>-<n>.mp3 : one aliyah;  data/torah/labels/<Parsha>-<n>.txt : start time of every word (WLC word division)
  data/torah/json/Genesis.json: Westminster Leningrad Codex, used to count words per pasuk.

Output per portion idx (same shape as gen_audio.py, so the site/hotline need no changes):
  static/audio_pt/<idx>.mp3 + <idx>.json  {"segments":[{"v","kind":"mikra1|mikra2|targum","start","end"}], ...}
Usage: build_pockettorah.py [/path/to/PocketTorah] [first_idx] [last_idx]
"""
import json, os, subprocess, sys
import numpy as np
import plan, gen_audio
SR = gen_audio.SR
PT = sys.argv[1] if len(sys.argv) > 1 else "/workspace/PocketTorah"
A = int(sys.argv[2]) if len(sys.argv) > 2 else 0
B = int(sys.argv[3]) if len(sys.argv) > 3 else 20
OUT = os.path.join(plan.BASE, "static/audio_pt"); os.makedirs(OUT, exist_ok=True)
NAMES = {"Bereishis": "Bereshit", "Noach": "Noach", "Lech Lecha": "Lech-Lecha"}
GAP_REPEAT, GAP_TARGUM, GAP_NEXT = 0.6, 0.6, 0.9     # after 1st reading / after 2nd reading / after Targum
VERSION = 3
wlc = json.load(open(os.path.join(PT, "data/torah/json/Genesis.json"), encoding="utf-8-sig"))["Tanach"]["tanach"]["book"]["c"]
aliyos = {p["_id"]: p["fullkriyah"]["aliyah"] for p in json.load(open(os.path.join(PT, "data/aliyah.json"), encoding="utf-8-sig"))["parshiot"]["parsha"]}
def nwords(c, v):
    w = wlc[c - 1]["v"][v - 1]["w"]; return len(w) if isinstance(w, list) else 1
def labels_path(name, n):
    for d in ("ios/data/torah/labels", "data/torah/labels"):
        for f in (f"{name}-{n}.txt", f"{name.lower()}-{n}.txt", f"{name[0]}{name[1:].lower()}-{n}.txt"):
            if os.path.exists(os.path.join(PT, d, f)): return os.path.join(PT, d, f)
    raise FileNotFoundError(name, n)
def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32)
def active_rms(x, frame=0.05, th=300):
    n = int(frame * SR); f = x[: len(x) // n * n].reshape(-1, n); r = np.sqrt((f ** 2).mean(1)); a = r[r > th]
    return float(np.sqrt((a ** 2).mean())) if len(a) else 1.0
def trim(x, th=250, lead=0.06, tail=0.2):
    """trim silence at both ends (50 ms frames)"""
    n = int(0.01 * SR); f = x[: len(x) // n * n].reshape(-1, n); r = np.sqrt((f ** 2).mean(1)); idx = np.where(r > th)[0]
    if not len(idx): return x[:0], 0
    i = max(0, idx[0] * n - int(lead * SR)); j = min(len(x), (idx[-1] + 1) * n + int(tail * SR))
    return x[i:j], i
def snap(x, t, lo=-1.6, hi=0.8, th=220, frame=0.02, floor=None, ceil=None):
    """PocketTorah's word labels sit near, not exactly at, the gap between pesukim. Move the cut to the middle of the
    longest quiet stretch within [t+lo, t+hi]. Returns (time, quiet_seconds)."""
    lo_t = max(t + lo, floor if floor is not None else 0); hi_t = min(t + hi, ceil if ceil is not None else len(x) / SR)
    n = int(frame * SR); a = max(0, int(lo_t * SR)); b = min(len(x), int(hi_t * SR))
    f = x[a: a + (b - a) // n * n].reshape(-1, n); r = np.sqrt((f ** 2).mean(1)); q = r < th
    best, cur, bs = (0, 0), 0, 0
    for i, v in enumerate(q):
        if v:
            if cur == 0: bs = i
            cur += 1
            if cur > best[0]: best = (cur, bs)
        else: cur = 0
    if best[0] == 0: return t, 0.0
    mid = a + (best[1] + best[0] / 2) * n
    return mid / SR, best[0] * frame
REPORT = []
def build(idx, tts_rms):
    p = plan.PORTIONS[idx]; name = NAMES[p["parsha"]]; n = idx % 7 + 1; vs = p["verses"]
    a = aliyos[name][n - 1]
    ours = (f"{vs[0]['c']}:{vs[0]['v']}", f"{vs[-1]['c']}:{vs[-1]['v']}")
    assert ours == (a["_begin"], a["_end"]), ("aliyah mismatch", idx, ours, a)
    L = [float(t) for t in open(labels_path(name, n)).read().replace("\n", ",").split(",") if t.strip()]
    counts = [nwords(v["c"], v["v"]) for v in vs]
    assert sum(counts) == len(L), ("word/label count mismatch", idx, sum(counts), len(L))
    assert all(b >= a for a, b in zip(L, L[1:])), ("labels not monotonic", idx)
    x = decode(os.path.join(PT, f"data/audio/{name}-{n}.mp3"))
    gain = tts_rms / active_rms(x); x = np.clip(x * gain, -32767, 32767)    # match the Targum voice's loudness
    starts = np.cumsum([0] + counts)[:-1]
    clips, quiet_at_boundary, worst = [], 0, []
    # a cut must stay after the previous pasuk's last word label and before this pasuk's 2nd word label
    bounds = [(L[starts[k] - 1], L[starts[k] + 1] if counts[k] > 1 else L[starts[k]] + 0.8) for k in range(len(vs))]
    sn = [snap(x, L[starts[k]], floor=bounds[k][0], ceil=bounds[k][1]) for k in range(1, len(vs))]
    cuts = [max(0.0, L[0] - 0.3)] + [c for c, _ in sn] + [len(x) / SR]
    gaps = [g for _, g in sn]
    shifts = [(round(c - L[starts[k]], 2), f"{vs[k]['c']}:{vs[k]['v']}") for k, c in enumerate(cuts[1:-1], 1)]
    for k in range(1, len(vs)):
        lo_ok = L[starts[k] - 1]; hi_ok = L[starts[k] + 1] if counts[k] > 1 else L[starts[k]] + 0.8
        assert lo_ok < cuts[k] < hi_ok, ("cut outside word bounds", idx, vs[k]["c"], vs[k]["v"], lo_ok, cuts[k], hi_ok)
    for k, v in enumerate(vs):
        t0, t1 = cuts[k], cuts[k + 1]
        seg, off = trim(x[int(t0 * SR): int(t1 * SR)])
        dur = len(seg) / SR
        assert dur > 0.4 * counts[k] * 0.3, ("pasuk clip too short", idx, v["c"], v["v"], dur)
        # boundary check: the 100 ms at the label should be (near) silent if the label sits between pesukim
        if k == 0 or gaps[k - 1] >= 0.06: quiet_at_boundary += 1   # cut falls in >= 60 ms of silence
        worst.append((dur / counts[k], f"{v['c']}:{v['v']}"))
        clips.append(seg)
    pcm, segs, t = [], [], 0.0
    def add(arr, gap, vi, kind):
        nonlocal t
        segs.append({"v": vi, "kind": kind, "start": round(t, 3), "end": round(t + len(arr) / SR, 3)})
        pcm.append(arr); t += len(arr) / SR
        pcm.append(np.zeros(int(gap * SR), np.float32)); t += int(gap * SR) / SR
    for vi, v in enumerate(vs):
        onk = np.frombuffer(gen_audio.phrase(gen_audio.key(p, v, "onk"), gen_audio.tts_words(v["onk"], True)), dtype=np.int16).astype(np.float32)
        add(clips[vi], GAP_REPEAT, vi, "mikra1"); add(clips[vi], GAP_TARGUM, vi, "mikra2"); add(onk, GAP_NEXT, vi, "targum")
    out = np.concatenate(pcm).astype(np.int16).tobytes()
    mp3 = os.path.join(OUT, f"{idx}.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "48k", mp3 + ".tmp.mp3"], input=out, check=True)
    os.replace(mp3 + ".tmp.mp3", mp3)
    json.dump({"source": "pockettorah+tts", "version": VERSION, "hebrew": "PocketTorah (chanted with trop)", "targum": "placeholder TTS, Hungarian havara, no trop",
               "credit": {"text": "Torah audio: PocketTorah, CC BY-SA", "url": "https://github.com/rneiss/PocketTorah"},
               "pt_file": f"{name}-{n}.mp3", "duration": round(t, 3), "segments": segs}, open(os.path.join(OUT, f"{idx}.json"), "w"))
    wr = sorted(worst)
    REPORT.append({"idx": idx, "aliyah": f"{p['parsha']} {n}", "range": f"{ours[0]}-{ours[1]}", "pesukim": len(vs), "words": len(L),
                   "pt_sec": round(len(x) / SR, 1), "out_min": round(t / 60, 1), "quiet_boundaries": f"{quiet_at_boundary}/{len(vs)}", "min_gap_s": round(min(gaps), 2) if gaps else None,
                   "big_shifts": [s for s in shifts if abs(s[0]) > 0.8], "no_gap": [shifts[k][1] for k, g in enumerate(gaps) if g < 0.06],
                   "sec_per_word_min": [round(wr[0][0], 2), wr[0][1]], "sec_per_word_max": [round(wr[-1][0], 2), wr[-1][1]]})
    print(json.dumps(REPORT[-1]), flush=True)
if __name__ == "__main__":
    ref = np.concatenate([np.frombuffer(open(gen_audio.key(plan.PORTIONS[0], v, "onk"), "rb").read(), dtype=np.int16).astype(np.float32) for v in plan.PORTIONS[0]["verses"][:10]])
    tts_rms = active_rms(ref)
    for i in range(A, B + 1): build(i, tts_rms)
    json.dump(REPORT, open(os.path.join(plan.BASE, "data/pockettorah_check.json"), "w"), indent=1)
