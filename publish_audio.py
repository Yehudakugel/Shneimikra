"""Make a generated audio build PERMANENT for free: upload its mp3s to a GitHub Release and point the site at them.

  GITHUB_TOKEN=... python publish_audio.py audio_clone [--repo Yehudakugel/Shneimikra] [--tag audio-clone-v1] [--dry-run]

- Release assets are free, permanent, up to 2 GB each, unlimited count; a whole-Torah build (~378 files, ~1-2 GB) fits.
- Writes static/<set>/voice.json "base_url" = https://github.com/<repo>/releases/download/<tag>/ ; the site's
  /media/<set>/<idx>.mp3 route then proxies with audio/mpeg + Range so iPhone/Android seeking works.
- Commit only static/<set>/*.json (timing maps + voice.json); the mp3s are in .gitignore for that set.
- Re-running skips assets already uploaded (resumable)."""
import json, os, sys, urllib.request, urllib.error
args = [a for a in sys.argv[1:] if not a.startswith("--")]
opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
if not args: sys.exit(__doc__)
SET = args[0]; REPO = opt("--repo", "Yehudakugel/Shneimikra"); TAG = opt("--tag", SET.replace("_", "-") + "-v1"); DRY = "--dry-run" in sys.argv
BASE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(BASE, "static", SET)
files = sorted([f for f in os.listdir(D) if f.endswith(".mp3") and f[:-4].isdigit()], key=lambda f: int(f[:-4]))
size = sum(os.path.getsize(os.path.join(D, f)) for f in files)
print(f"{SET}: {len(files)} mp3 files, {size / 1e6:,.0f} MB -> github.com/{REPO} release '{TAG}'")
if DRY: sys.exit(0)
TOK = os.environ.get("GITHUB_TOKEN") or sys.exit("set GITHUB_TOKEN (repo scope)")
def api(method, url, data=None, ctype="application/json"):
    body = json.dumps(data).encode() if isinstance(data, dict) else data
    r = urllib.request.Request(url, data=body, method=method, headers={"Authorization": "Bearer " + TOK, "Accept": "application/vnd.github+json", "Content-Type": ctype})
    with urllib.request.urlopen(r, timeout=600) as resp: return json.loads(resp.read() or b"{}")
try: rel = api("GET", f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}")
except urllib.error.HTTPError:
    rel = api("POST", f"https://api.github.com/repos/{REPO}/releases", {"tag_name": TAG, "name": f"Audio build: {SET}", "body": "Generated read-along audio (mp3). Timing maps live in the repo under static/%s/." % SET})
have = {a["name"] for a in api("GET", rel["url"] + "/assets?per_page=100")}
page = 2
while len(have) >= 100 * (page - 1):
    more = api("GET", rel["url"] + f"/assets?per_page=100&page={page}")
    if not more: break
    have |= {a["name"] for a in more}; page += 1
up = rel["upload_url"].split("{")[0]
for f in files:
    if f in have: continue
    api("POST", f"{up}?name={f}", open(os.path.join(D, f), "rb").read(), "audio/mpeg"); print("uploaded", f, flush=True)
man_p = os.path.join(D, "voice.json"); man = json.load(open(man_p)) if os.path.exists(man_p) else {}
man["base_url"] = f"https://github.com/{REPO}/releases/download/{TAG}/"; json.dump(man, open(man_p, "w"), indent=1)
gi = os.path.join(BASE, ".gitignore"); line = f"static/{SET}/*.mp3\n"
if not os.path.exists(gi) or line not in open(gi).read(): open(gi, "a").write(line)
print("done. voice.json base_url set; commit static/%s/*.json and .gitignore" % SET)
