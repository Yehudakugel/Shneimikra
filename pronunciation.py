"""Custom pronunciation dictionary + per-pasuk overrides (admin > Pronunciation).

pronunciation.json (in the repo, so it survives restarts):
  {"words": [{"word": "<Hebrew, pointed or not>", "say": "<spoken respelling>", "note": "", "scope": "all|pesukim|onkelos"}],
   "overrides": {"Genesis 1:1": {"heb": "<full text to speak>", "onk": "...", "note": ""}}}
Applied in gen_audio.tts_words() BEFORE havara.py, so every engine (Edge, Azure, Google, OpenAI, ElevenLabs, the clone)
gets the same respelled text. The respelling is spoken as written (havara is NOT applied on top of it).
For ElevenLabs we can also mirror the words as an alias pronunciation dictionary (elevenlabs_sync())."""
import json, os, re, base64, urllib.request
BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("PRONUNCIATION_FILE", os.path.join(BASE, "pronunciation.json"))
TAAMIM = re.compile(r"[\u0591-\u05AF\u05BD\u05C0\u05C3\u05C6]")
NIKUD = re.compile(r"[\u05B0-\u05C7]")
_cache = {"mtime": None, "data": None}
def norm(w, pointed=True):
    w = TAAMIM.sub("", w).replace("\u05BE", "").strip(" :׃,.")
    return w if pointed else NIKUD.sub("", w)
def load():
    try: m = os.path.getmtime(PATH)
    except OSError: return {"words": [], "overrides": {}}
    if _cache["mtime"] != m:
        d = json.load(open(PATH, encoding="utf-8")); d.setdefault("words", []); d.setdefault("overrides", {})
        _cache.update(mtime=m, data=d)
    return _cache["data"]
def save(d):
    tmp = PATH + ".tmp"; json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1); os.replace(tmp, PATH)
    _cache["mtime"] = None
def lookup(word, targum=False):
    """Spoken respelling for one word (taamim ignored), or None. Pointed entries match exactly; unpointed entries match any pointing."""
    scope = "onkelos" if targum else "pesukim"
    p, u = norm(word), norm(word, False)
    hit = None
    for e in load()["words"]:
        if e.get("scope", "all") not in ("all", scope): continue
        k = norm(e["word"])
        if k == p: return e["say"]
        if not NIKUD.search(k) and k == u: hit = hit or e["say"]
    return hit
def override_text(ref, kind):
    o = load()["overrides"].get(ref) or {}
    return (o.get(kind) or "").strip() or None
def override_by_source(text, targum=False):
    """gen_audio only sees the verse text, so overrides also store their source text for lookup."""
    kind = "onk" if targum else "heb"
    for o in load()["overrides"].values():
        if o.get(kind + "_src") == text and (o.get(kind) or "").strip(): return o[kind].strip()
    return None
# ---- persistence to the GitHub repo (free plan wipes local disk on restart) ----
def commit_to_github(message="Update pronunciation dictionary"):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO", "Yehudakugel/Shneimikra")
    if not tok: return "not saved to GitHub (no GITHUB_TOKEN on the server) - download the JSON to keep it"
    url = f"https://api.github.com/repos/{repo}/contents/pronunciation.json"
    h = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "User-Agent": "schneimaker-kp"}
    sha = None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r: sha = json.load(r)["sha"]
    except Exception: pass
    body = {"message": message + " [skip render]", "content": base64.b64encode(open(PATH, "rb").read()).decode(), "branch": "main"}
    if sha: body["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**h, "Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as r: r.read()
    return "saved to GitHub"
# ---- ElevenLabs alias dictionary (optional; text respelling already covers ElevenLabs) ----
def elevenlabs_sync():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key: raise RuntimeError("ELEVENLABS_API_KEY not set")
    rules = [{"type": "alias", "string_to_replace": norm(e["word"]), "alias": e["say"]} for e in load()["words"] if e.get("say")]
    req = urllib.request.Request("https://api.elevenlabs.io/v1/pronunciation-dictionaries/add-from-rules",
        data=json.dumps({"name": "schneimaker-kp", "rules": rules}).encode(), method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r: d = json.load(r)
    return {"pronunciation_dictionary_id": d["id"], "version_id": d["version_id"], "rules": len(rules)}
