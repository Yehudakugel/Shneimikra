"""Admin audio-builds panel + clone detection + /media route (Flask test client)."""
import os, re, shutil, json
os.environ.setdefault("SECRET_KEY", "t")
import app as A
c = A.app.test_client()
pw = open("data/ADMIN_PASSWORD.txt").read().split(": ")[-1].strip()
r = c.get("/login"); tok = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
c.post("/login", data={"username": "admin", "password": pw, "csrf": tok})
r = c.get("/admin/voice"); assert r.status_code == 200 and "Audio builds" in r.text, r.status_code
tok = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
r = c.post("/admin/voice/build", data={"csrf": tok, "action": "clone_estimate", "from": 0, "to": 377}, follow_redirects=True); assert "$119" in r.text
r = c.post("/admin/voice/build", data={"csrf": tok, "action": "clone_build", "from": 0, "to": 0, "confirm": "x"}, follow_redirects=True); assert "ELEVENLABS" in r.text
r = c.get("/media/audio_pt/0.mp3", headers={"Range": "bytes=0-99"}); assert r.status_code == 206 and r.mimetype == "audio/mpeg", r.status_code
# clone present -> default reader, in the menu
shutil.copytree("static/audio_clone_rehearsal", "static/audio_clone", dirs_exist_ok=True)
json.dump({"label": "Chassidish reader (licensed voice)", "priority": 100}, open("static/audio_clone/voice.json", "w"))
try:
    A._VS_CACHE["t"] = 0
    with A.app.test_request_context():
        m = A.audio_meta(0); assert m["dir"] == "audio_clone", m["dir"]
        assert A.audio_meta(0, "audio_pt")["dir"] == "audio_pt"
        assert A.audio_meta(5)["dir"] != "audio_clone"   # portion not built -> falls back
    print("clone default OK")
finally:
    shutil.rmtree("static/audio_clone"); A._VS_CACHE["t"] = 0
print("BUILDS OK")
