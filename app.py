import os, sqlite3, secrets, json, datetime, functools
from zoneinfo import ZoneInfo
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import config, plan, rewards, subprocess, hmac, hashlib, base64, sys
from itsdangerous import URLSafeTimedSerializer, BadSignature

BASE = plan.BASE
DB = os.path.join(BASE, "data/app.db")
TZ = ZoneInfo("America/New_York")
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
keyf = os.path.join(BASE, "data/secret.key")
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
else:
    if not os.path.exists(keyf): open(keyf, "w").write(secrets.token_hex(32))
    app.secret_key = open(keyf).read().strip()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=180))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0, created_at TEXT NOT NULL,
  phone TEXT UNIQUE, pin TEXT UNIQUE, audio_mode TEXT NOT NULL DEFAULT 'auto', account_type TEXT NOT NULL DEFAULT 'regular');
CREATE TABLE IF NOT EXISTS prizes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, tier TEXT NOT NULL,
  min_weeks INTEGER NOT NULL DEFAULT 0, weight INTEGER NOT NULL DEFAULT 1, bonus_entries INTEGER NOT NULL DEFAULT 0, value TEXT,
  sponsor TEXT, placeholder INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS scratch_cards(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, portion_idx INTEGER NOT NULL, created_at TEXT NOT NULL,
  win INTEGER NOT NULL, prize TEXT NOT NULL, prize_id INTEGER, bonus_entries INTEGER NOT NULL DEFAULT 0, revealed_at TEXT, UNIQUE(user_id, portion_idx));
CREATE TABLE IF NOT EXISTS completions(user_id INTEGER NOT NULL, portion_idx INTEGER NOT NULL, completed_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'app', PRIMARY KEY(user_id, portion_idx));
CREATE TABLE IF NOT EXISTS draws(id INTEGER PRIMARY KEY, title TEXT NOT NULL, prize TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  num_winners INTEGER NOT NULL DEFAULT 1, announced INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL, min_weeks INTEGER NOT NULL DEFAULT 0, prize_id INTEGER, drawn_at TEXT, total_entries INTEGER, total_participants INTEGER, entries_snapshot TEXT);
CREATE TABLE IF NOT EXISTS draw_winners(draw_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rank INTEGER NOT NULL, entries INTEGER NOT NULL,
  seen INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(draw_id, user_id));
"""
def norm_phone(p):
    """US numbers -> E.164 (+1XXXXXXXXXX), which is what Twilio sends as From."""
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(d) == 10: return "+1" + d
    if len(d) == 11 and d.startswith("1"): return "+" + d
    return ("+" + d) if d else None
def fmt_phone(p):
    if p and p.startswith("+1") and len(p) == 12: return f"({p[2:5]}) {p[5:8]}-{p[8:]}"
    return p or ""
def now_utc(): return datetime.datetime.now(datetime.timezone.utc)
def iso(dt): return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
def local(s): return parse(s).astimezone(TZ)
app.jinja_env.filters["phone"] = lambda p: fmt_phone(p)
app.jinja_env.filters["local"] = lambda s, f="%a %d %b %Y, %H:%M": local(s).strftime(f) if s else ""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db
@app.teardown_appcontext
def close_db(e):
    d = g.pop("db", None)
    if d: d.close()
def init_db():
    con = sqlite3.connect(DB); con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    for c, d in (("player_voice", "''"), ("read_order", "''"), ("play_speed", "''")):
        if c not in cols: con.execute(f"ALTER TABLE users ADD COLUMN {c} TEXT NOT NULL DEFAULT {d}")
    con.commit(); con.close()

# ---------- auth helpers ----------
def current_user():
    uid = session.get("uid")
    if not uid: return None
    if "user" not in g:
        g.user = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return g.user
def login_required(f):
    @functools.wraps(f)
    def w(*a, **k):
        if not current_user(): return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w
def admin_required(f):
    @functools.wraps(f)
    def w(*a, **k):
        u = current_user()
        if not u: return redirect(url_for("login", next=request.path))
        if not u["is_admin"]: abort(403)
        return f(*a, **k)
    return w
@app.before_request
def csrf_protect():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(24)
    if request.path.startswith("/voice/"): return  # phone webhooks are server-to-server (signature-checked instead)
    sent = request.form.get("csrf") or request.headers.get("X-CSRF")
    if request.method == "POST" and sent != session["csrf"]:
        abort(400, "Form expired - please go back and try again.")
@app.context_processor
def inject():
    return {"me": current_user(), "csrf": session.get("csrf", ""), "active_draw": active_announced_draw(), "plan": plan, "rewards": rewards, "setting": setting, "BRAND": config.BRAND_NAME, "BRAND_HE": config.BRAND_HE, "TAGLINE": config.BRAND_TAGLINE, "AUDIO_CREDIT": config.AUDIO_CREDIT if hebrew_audio() == "pockettorah" else None}

# ---------- progress logic ----------
def done_set(uid):
    return {r[0] for r in db().execute("SELECT portion_idx FROM completions WHERE user_id=?", (uid,))}
def current_idx(done):
    for i in range(plan.TOTAL):
        if i not in done: return i
    return None
def streak(uid):
    days = {local(r[0]).date() for r in db().execute("SELECT completed_at FROM completions WHERE user_id=?", (uid,))}
    today = now_utc().astimezone(TZ).date(); d = today if today in days else today - datetime.timedelta(days=1); n = 0
    while d in days: n += 1; d -= datetime.timedelta(days=1)
    return n
def stats(uid):
    done = done_set(uid); today = now_utc().astimezone(TZ).date()
    sched = plan.scheduled_index(today) if today >= plan.START_DATE else -1
    return {"done": len(done), "total": plan.TOTAL, "pct": round(100*len(done)/plan.TOTAL, 1), "streak": streak(uid),
            "scheduled": sched, "scheduled_portion": plan.PORTIONS[sched] if sched >= 0 else None,
            "on_track": len(done) >= sched + 1, "behind": max(0, sched + 1 - len(done)), "done_set": done,
            "rw": rewards.summary(completion_times(uid)), "level": rewards.level_for(len(done))[0], "next_level": rewards.level_for(len(done))[1]}
def completion_times(uid):
    return [r[0] for r in db().execute("SELECT completed_at FROM completions WHERE user_id=?", (uid,))]
def active_announced_draw():
    n = iso(now_utc())
    return db().execute("SELECT * FROM draws WHERE status='open' AND announced=1 AND starts_at<=? AND ends_at>=? ORDER BY ends_at LIMIT 1", (n, n)).fetchone()
_VS_CACHE = {"t": 0, "v": []}
AUDIO_TOTAL = plan.TOTAL
def _manifest(d):
    mp = os.path.join(BASE, "static", d, "voice.json")
    try: return json.load(open(mp)) if os.path.exists(mp) else {}
    except Exception: return {}
def voice_sets(fresh=False):
    """Registry of audio builds: every static/audio*/ folder with a voice.json manifest or timing files.
    voice.json: {"label", "priority", "base_url"?}. base_url = permanent external storage for the mp3s
    (e.g. a GitHub Release: https://github.com/<owner>/<repo>/releases/download/<tag>/), timing JSONs stay in the repo.
    A new voice model (e.g. static/audio_clone/ from gen_full_clone.py) appears automatically - no code change."""
    import time
    if not fresh and time.time() - _VS_CACHE["t"] < 30: return _VS_CACHE["v"]
    out = []
    sd = os.path.join(BASE, "static")
    for d in sorted(os.listdir(sd)):
        full = os.path.join(sd, d)
        if not (d.startswith("audio") and os.path.isdir(full)): continue
        man = _manifest(d)
        jsons = [f for f in os.listdir(full) if f.endswith(".json") and f[:-5].isdigit()]
        if not man and not jsons: continue
        out.append({"id": d, "label": man.get("label") or d.replace("audio_", "").replace("_", " ").title(),
                    "priority": int(man.get("priority", 100 if d == "audio_clone" else 20)), "base_url": man.get("base_url", ""),
                    "count": sum(1 for f in jsons if _has(d, int(f[:-5]), man.get("base_url")))})
    out.sort(key=lambda v: -v["priority"]); _VS_CACHE.update(t=time.time(), v=out)
    return out
def _has(d, idx, base_url=None):
    p = os.path.join(BASE, "static", d, f"{idx}.json")
    if not os.path.exists(p): return False
    return bool(base_url if base_url is not None else _manifest(d).get("base_url")) or os.path.exists(p[:-5] + ".mp3")
def audio_sets_for(idx):
    """Voice sets that have this portion, best first. Admin 'Pesukim audio': clone > PocketTorah > computer voice by priority;
    'tts' pushes PocketTorah below the computer voice; 'clone' forces the clone first when it has this portion."""
    sets = [v for v in voice_sets() if _has(v["id"], idx, v["base_url"])]
    src = hebrew_audio()
    if src == "tts": sets.sort(key=lambda v: v["id"] == "audio_pt")
    elif src == "pockettorah": sets.sort(key=lambda v: v["id"] != "audio_pt" and v["id"] != "audio_clone")
    if src == "clone" or any(v["id"] == "audio_clone" for v in sets): sets.sort(key=lambda v: v["id"] != "audio_clone")
    return sets
def audio_meta(idx, prefer=None):
    """Timing map for a portion from the preferred voice set if it has this portion, else the best available.
    meta["src"] is the mp3 URL (served locally or proxied from permanent external storage)."""
    sets = audio_sets_for(idx)
    if prefer: sets.sort(key=lambda v: v["id"] != prefer)
    for v in sets:
        p = os.path.join(BASE, "static", v["id"], f"{idx}.json")
        try: m = json.load(open(p))
        except Exception: continue
        m["dir"] = v["id"]; m["voice_label"] = v["label"]
        m["src"] = url_for("audio_media", d=v["id"], idx=idx) + "?v=" + str(m.get("version", 1))
        return m
    return None
@app.route("/media/<d>/<int:idx>.mp3")
def audio_media(d, idx):
    """One mp3 for the player: the local file if the build is on disk, else a Range-aware proxy of the permanent copy
    (base_url) so phones get audio/mpeg + 206 seeking whatever the host sends."""
    from flask import send_from_directory, Response, stream_with_context
    import urllib.request
    if not (d.startswith("audio") and d.replace("_", "").isalnum()): abort(404)
    folder = os.path.join(BASE, "static", d)
    if os.path.exists(os.path.join(folder, f"{idx}.mp3")):
        r = send_from_directory(folder, f"{idx}.mp3", mimetype="audio/mpeg", conditional=True, max_age=86400)
        r.headers["Accept-Ranges"] = "bytes"; return r
    base = _manifest(d).get("base_url")
    if not base: abort(404)
    req = urllib.request.Request(base.rstrip("/") + f"/{idx}.mp3", headers={"User-Agent": "schneimaker-kp"})
    if request.headers.get("Range"): req.add_header("Range", request.headers["Range"])
    try: up = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as e:
        if e.code == 416: return Response(status=416)
        abort(404)
    except Exception: abort(502)
    hdr = {"Content-Type": "audio/mpeg", "Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"}
    rng = request.headers.get("Range", ""); import re as _re; mr = _re.match(r"bytes=(\d+)-(\d*)$", rng)
    if up.status == 200 and mr and up.headers.get("Content-Length"):   # host ignored Range: cut it here so phones can still seek
        total = int(up.headers["Content-Length"]); lo = int(mr.group(1)); hi = min(int(mr.group(2) or total - 1), total - 1)
        if lo >= total: up.close(); return Response(status=416, headers={"Content-Range": f"bytes */{total}"})
        def part():
            try:
                left = lo
                while left > 0: left -= len(up.read(min(65536, left)) or b"x" * left)
                need = hi - lo + 1
                while need > 0:
                    b = up.read(min(65536, need))
                    if not b: break
                    need -= len(b); yield b
            finally: up.close()
        return Response(stream_with_context(part()), status=206, headers={**hdr, "Content-Length": str(hi - lo + 1), "Content-Range": f"bytes {lo}-{hi}/{total}"})
    for k in ("Content-Length", "Content-Range"):
        if up.headers.get(k): hdr[k] = up.headers[k]
    def gen():
        try:
            while True:
                b = up.read(65536)
                if not b: break
                yield b
        finally: up.close()
    return Response(stream_with_context(gen()), status=up.status, headers=hdr)

# ---------- public pages ----------
@app.route("/healthz")
def healthz():
    db().execute("SELECT 1").fetchone()
    return {"ok": True}
@app.route("/")
def landing():
    if current_user() and not request.args.get("home"): return redirect(url_for("learn"))
    winners = recent_winners(5)
    return render_template("landing.html", winners=winners)
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip(); username = request.form.get("username", "").strip(); pw = request.form.get("password", "")
        err = None
        if not name or not username: err = "Please fill in your name and a username."
        elif len(pw) < 6: err = "Password must be at least 6 characters."
        elif db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone(): err = "That username is taken - please choose another."
        if err: flash(err, "error"); return render_template("auth.html", mode="signup", form=request.form)
        cur = db().execute("INSERT INTO users(username,name,pw_hash,created_at) VALUES(?,?,?,?)", (username, name, generate_password_hash(pw), iso(now_utc())))
        db().commit(); session.clear(); session.permanent = True; session["uid"] = cur.lastrowid
        flash(f"Welcome, {name}. Your first portion is below.", "ok")
        return redirect(url_for("learn"))
    return render_template("auth.html", mode="signup", form={})
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=?", (request.form.get("username", "").strip(),)).fetchone()
        if u and check_password_hash(u["pw_hash"], request.form.get("password", "")):
            session.clear(); session.permanent = True; session["uid"] = u["id"]
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("learn"))
        flash("Username or password is not correct.", "error")
        return render_template("auth.html", mode="login", form=request.form)
    return render_template("auth.html", mode="login", form={})
@app.route("/logout", methods=["POST"])
def logout():
    session.clear(); return redirect(url_for("landing"))

# ---------- learning ----------
@app.route("/learn")
@app.route("/learn/<int:idx>")
@login_required
def learn(idx=None):
    u = current_user(); st = stats(u["id"]); cur = current_idx(st["done_set"])
    if idx is None:
        if cur is None: return render_template("finished.html", st=st)
        idx = cur
    if not 0 <= idx < plan.TOTAL: abort(404)
    p = plan.PORTIONS[idx]
    wins = db().execute("SELECT d.title, d.prize, w.draw_id FROM draw_winners w JOIN draws d ON d.id=w.draw_id WHERE w.user_id=? AND w.seen=0", (u["id"],)).fetchall()
    jd = request.args.get("done", type=int); celebrate = None; card = None
    if jd is not None and jd in st["done_set"]:
        celebrate = rewards.milestone_hit(st["done"])
        card = db().execute("SELECT id, revealed_at FROM scratch_cards WHERE user_id=? AND portion_idx=?", (u["id"], jd)).fetchone()
        if card and card["revealed_at"]: card = None
    unrevealed = db().execute("SELECT id FROM scratch_cards WHERE user_id=? AND revealed_at IS NULL ORDER BY id", (u["id"],)).fetchall()
    chazal = rewards.chazal_for(idx)
    return render_template("learn.html", p=p, st=st, cur=cur, is_current=(idx == cur), is_done=(idx in st["done_set"]),
                           audio=audio_meta(idx, u["player_voice"] or None), voice_sets=audio_sets_for(idx), wins=wins, just_done=jd, celebrate=celebrate, card=card, unrevealed=unrevealed,
                           chazal=chazal, chazal_big=(rewards.CHAZAL[0] if celebrate and celebrate["big"] else rewards.chazal_for(st["done"] + 3)), next_p=plan.PORTIONS[cur] if cur is not None else None)
SETTINGS_DEFAULTS = {"tap_win_chance": "0.30", "program_name": "Shnayim Mikra Challenge 5787",
    "teaser": "Surprise draws open at unannounced times – sefarim, gift cards, and a car for the grand finale.", "hotline_number": "(not connected yet)",
    "share_text": "שנים מקרא ואחד תרגום – one aliyah a day, read along with a reader, and finish the whole Torah this year. Join me:"}
def setting(k):
    r = db().execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else SETTINGS_DEFAULTS.get(k, "")
def mark_done(uid, idx, source="app"):
    """Mark portion complete; returns True if newly completed. Creates one tap-to-win card per newly completed portion.
    The outcome is decided here, server-side, from the admin-editable prize catalog (tier='instant')."""
    cur = db().execute("INSERT OR IGNORE INTO completions(user_id,portion_idx,completed_at,source) VALUES(?,?,?,?)", (uid, idx, iso(now_utc()), source))
    if cur.rowcount:
        rng = secrets.SystemRandom()
        streak_w = rewards.summary(completion_times(uid))["streak_weeks"]
        pool = db().execute("SELECT * FROM prizes WHERE tier='instant' AND active=1 AND min_weeks<=?", (streak_w,)).fetchall()
        win = bool(pool) and rng.random() < float(setting("tap_win_chance") or 0.3)
        if win:
            pick = rng.choices(pool, weights=[max(1, p["weight"]) for p in pool])[0]
            prize, bonus, pid = pick["name"], pick["bonus_entries"], pick["id"]
        else:
            prize, bonus, pid = "Not this time. Every portion is another chance.", 0, None
        db().execute("INSERT OR IGNORE INTO scratch_cards(user_id,portion_idx,created_at,win,prize,prize_id,bonus_entries) VALUES(?,?,?,?,?,?,?)",
                     (uid, idx, iso(now_utc()), int(win), prize, pid, bonus))
    db().commit()
    return bool(cur.rowcount)
@app.route("/complete/<int:idx>", methods=["POST"])
@login_required
def complete(idx):
    u = current_user()
    if not 0 <= idx < plan.TOTAL: abort(404)
    new = mark_done(u["id"], idx, "sefer" if request.form.get("source") == "sefer" else "app")
    return redirect(url_for("learn", done=idx) if new else url_for("learn"))
@app.route("/scratch/<int:cid>/reveal", methods=["POST"])
@login_required
def scratch_reveal(cid):
    c = db().execute("SELECT * FROM scratch_cards WHERE id=? AND user_id=?", (cid, current_user()["id"])).fetchone()
    if not c: abort(404)
    if not c["revealed_at"]:
        db().execute("UPDATE scratch_cards SET revealed_at=? WHERE id=?", (iso(now_utc()), cid)); db().commit()
    return jsonify({"win": bool(c["win"]), "prize": c["prize"], "bonus": c["bonus_entries"]})
@app.route("/pref/audio-mode", methods=["POST"])
@login_required
def pref_audio_mode():
    m = request.form.get("mode")
    if m in ("tap", "auto", "listen"):
        db().execute("UPDATE users SET audio_mode=? WHERE id=?", (m, current_user()["id"])); db().commit()
    return jsonify({"ok": True, "mode": m})
READ_ORDERS = ("ppo", "po", "aao", "ao", "p")
@app.route("/pref/player", methods=["POST"])
@login_required
def pref_player():
    f = request.form; uid = current_user()["id"]; out = {}
    if f.get("voice") is not None and (f["voice"] == "" or f["voice"] in {v["id"] for v in voice_sets()}): out["player_voice"] = f["voice"]
    if f.get("order") in READ_ORDERS: out["read_order"] = f["order"]
    try:
        sp = float(f.get("speed", "x"))
        if 0.75 <= sp <= 1.5: out["play_speed"] = str(sp)
    except ValueError: pass
    for k, v in out.items(): db().execute(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
    db().commit(); return jsonify({"ok": True, **out})
@app.route("/win-seen/<int:draw_id>", methods=["POST"])
@login_required
def win_seen(draw_id):
    db().execute("UPDATE draw_winners SET seen=1 WHERE draw_id=? AND user_id=?", (draw_id, current_user()["id"])); db().commit()
    return redirect(url_for("learn"))
@app.route("/progress")
@login_required
def progress():
    st = stats(current_user()["id"])
    parshiyos = []
    for p in plan.PORTIONS:
        if p["aliyah"] == 0: parshiyos.append({"name": p["parsha"], "he": p["parsha_he"], "book_en": p["book_en"], "portions": []})
        parshiyos[-1]["portions"].append(p)
    return render_template("progress.html", st=st, parshiyos=parshiyos, cur=current_idx(st["done_set"]))
@app.route("/api/portion/<int:idx>")
@login_required
def api_portion(idx):
    if not 0 <= idx < plan.TOTAL: abort(404)
    return jsonify({"portion": plan.PORTIONS[idx], "audio": audio_meta(idx)})

# ---------- winners ----------
def recent_winners(limit=50):
    return db().execute("""SELECT d.id draw_id, d.title, d.prize, d.drawn_at, u.name, w.rank, w.entries FROM draw_winners w
        JOIN draws d ON d.id=w.draw_id JOIN users u ON u.id=w.user_id ORDER BY d.drawn_at DESC, w.rank LIMIT ?""", (limit,)).fetchall()
def public_name(n):
    parts = n.split()
    return parts[0] + (" " + parts[-1][0] + "." if len(parts) > 1 else "")
app.jinja_env.filters["public_name"] = public_name
@app.route("/winners")
def winners():
    draws = db().execute("SELECT * FROM draws WHERE status='drawn' ORDER BY drawn_at DESC").fetchall()
    ws = {}
    for r in recent_winners(500): ws.setdefault(r["draw_id"], []).append(r)
    prizes = db().execute("SELECT * FROM prizes WHERE active=1 ORDER BY min_weeks, id").fetchall()
    return render_template("winners.html", draws=draws, ws=ws, prizes=prizes)

# ---------- admin ----------
def entries_for(draw):
    """Raffle entries for a draw window: (portions completed in window x streak multiplier at window end) + revealed scratch-card bonus entries."""
    end_local = parse(draw["ends_at"]).astimezone(TZ).date()
    rows = db().execute("""SELECT u.id, u.name, u.username, COUNT(*) n FROM completions c JOIN users u ON u.id=c.user_id
        WHERE u.is_admin=0 AND c.completed_at>=? AND c.completed_at<=? GROUP BY u.id""", (draw["starts_at"], draw["ends_at"])).fetchall()
    out = []
    for r in rows:
        times = [t for t in completion_times(r["id"]) if t <= draw["ends_at"]]
        sm = rewards.summary(times, as_of=end_local)
        if sm["streak_weeks"] < (draw["min_weeks"] or 0): continue
        bonus = db().execute("SELECT COALESCE(SUM(bonus_entries),0) FROM scratch_cards WHERE user_id=? AND revealed_at IS NOT NULL AND created_at>=? AND created_at<=?",
                             (r["id"], draw["starts_at"], draw["ends_at"])).fetchone()[0]
        out.append({"id": r["id"], "name": r["name"], "username": r["username"], "portions": r["n"], "streak": sm["streak_weeks"],
                    "mult": sm["mult"], "bonus": bonus, "n": r["n"] * sm["mult"] + bonus})
    return sorted(out, key=lambda x: -x["n"])
@app.route("/admin")
@admin_required
def admin():
    draws = db().execute("SELECT * FROM draws ORDER BY created_at DESC").fetchall()
    info = {d["id"]: entries_for(d) for d in draws if d["status"] == "open"}
    ws = {}
    for r in recent_winners(500): ws.setdefault(r["draw_id"], []).append(r)
    users = db().execute("""SELECT u.*, (SELECT COUNT(*) FROM completions c WHERE c.user_id=u.id) done,
        (SELECT MAX(completed_at) FROM completions c WHERE c.user_id=u.id) last FROM users u ORDER BY u.created_at DESC""").fetchall()
    tot = db().execute("SELECT COUNT(*) FROM completions").fetchone()[0]
    nowl = now_utc().astimezone(TZ)
    prizes = db().execute("SELECT * FROM prizes WHERE active=1 AND tier!='instant' ORDER BY min_weeks, id").fetchall()
    return render_template("admin.html", prizes=prizes, draws=draws, info=info, ws=ws, users=users, total_completions=tot,
                           default_start=(nowl - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"), default_now=nowl.strftime("%Y-%m-%dT%H:%M"),
                           default_end=(nowl + datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"))
@app.route("/admin/draws", methods=["POST"])
@admin_required
def admin_new_draw():
    f = request.form
    try:
        s = datetime.datetime.fromisoformat(f["starts_at"]).replace(tzinfo=TZ); e = datetime.datetime.fromisoformat(f["ends_at"]).replace(tzinfo=TZ)
        n = max(1, min(100, int(f.get("num_winners", 1))))
    except Exception:
        flash("Please check the dates and number of winners.", "error"); return redirect(url_for("admin"))
    if e <= s: flash("End must be after start.", "error"); return redirect(url_for("admin"))
    mw = int(f.get("min_weeks") or 0); pid = f.get("prize_id", type=int); prize_name = f.get("prize", "").strip()
    pr = db().execute("SELECT * FROM prizes WHERE id=?", (pid,)).fetchone() if pid else None
    if pr:
        prize_name = prize_name or pr["name"]; mw = max(mw, pr["min_weeks"])   # prize tier sets the minimum streak to be eligible
    db().execute("INSERT INTO draws(title,prize,starts_at,ends_at,num_winners,announced,created_at,min_weeks,prize_id) VALUES(?,?,?,?,?,?,?,?,?)",
                 (f.get("title", "").strip() or "Surprise Week", prize_name or "A surprise prize", iso(s), iso(e), n, 1 if f.get("announced") else 0, iso(now_utc()), mw, pid))
    db().commit(); flash("Surprise week / prize draw created.", "ok"); return redirect(url_for("admin"))
@app.route("/admin/draws/<int:did>/run", methods=["POST"])
@admin_required
def admin_run_draw(did):
    d = db().execute("SELECT * FROM draws WHERE id=?", (did,)).fetchone()
    if not d or d["status"] != "open": abort(404)
    rows = entries_for(d)
    if not rows: flash("No one has completed a portion in this window yet - nothing to draw.", "error"); return redirect(url_for("admin"))
    rng = secrets.SystemRandom()   # cryptographically secure randomness
    pool = {r["id"]: r["n"] for r in rows}; winners = []
    while pool and len(winners) < d["num_winners"]:
        # weighted draw: each completed portion = one ticket; a person can win once per draw
        ticket = rng.randrange(sum(pool.values()))
        for uid, n in pool.items():
            if ticket < n: winners.append((uid, n)); del pool[uid]; break
            ticket -= n
    t = iso(now_utc())
    for rank, (uid, n) in enumerate(winners, 1):
        db().execute("INSERT INTO draw_winners(draw_id,user_id,rank,entries) VALUES(?,?,?,?)", (did, uid, rank, n))
    snap = json.dumps(rows)
    db().execute("UPDATE draws SET status='drawn', drawn_at=?, total_entries=?, total_participants=?, entries_snapshot=? WHERE id=?",
                 (t, sum(r["n"] for r in rows), len(rows), snap, did))
    db().commit(); flash(f"Drew {len(winners)} winner(s) from {sum(r['n'] for r in rows)} entries.", "ok")
    return redirect(url_for("admin"))
@app.route("/admin/draws/<int:did>/delete", methods=["POST"])
@admin_required
def admin_delete_draw(did):
    db().execute("DELETE FROM draws WHERE id=? AND status='open'", (did,)); db().commit(); return redirect(url_for("admin"))

TIER_LABEL = {"instant": "Instant win (tap-to-win)", "raffle": "Raffle prize", "grand": "Grand prize"}
app.jinja_env.globals["TIER_LABEL"] = TIER_LABEL
@app.route("/admin/prizes", methods=["GET", "POST"])
@admin_required
def admin_prizes():
    if request.method == "POST":
        f = request.form; pid = f.get("id", type=int)
        if f.get("action") == "toggle" and pid:
            db().execute("UPDATE prizes SET active=1-active WHERE id=?", (pid,))
        else:
            vals = (f.get("name", "").strip() or "Untitled prize", f.get("category", "Other"), f.get("tier", "raffle") if f.get("tier") in TIER_LABEL else "raffle",
                    f.get("min_weeks", 0, type=int), max(1, f.get("weight", 1, type=int)), f.get("bonus_entries", 0, type=int), f.get("value", ""), f.get("sponsor", ""),
                    1 if f.get("placeholder") else 0)
            if pid: db().execute("UPDATE prizes SET name=?,category=?,tier=?,min_weeks=?,weight=?,bonus_entries=?,value=?,sponsor=?,placeholder=? WHERE id=?", vals + (pid,))
            else: db().execute("INSERT INTO prizes(name,category,tier,min_weeks,weight,bonus_entries,value,sponsor,placeholder) VALUES(?,?,?,?,?,?,?,?,?)", vals)
        db().commit(); flash("Prize catalog saved.", "ok"); return redirect(url_for("admin_prizes"))
    prizes = db().execute("SELECT * FROM prizes ORDER BY CASE tier WHEN 'instant' THEN 0 WHEN 'raffle' THEN 1 ELSE 2 END, min_weeks, id").fetchall()
    return render_template("admin_prizes.html", prizes=prizes)
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        for k in SETTINGS_DEFAULTS:
            if k in request.form:
                v = request.form[k].strip()
                if k == "tap_win_chance":
                    try: v = str(min(1.0, max(0.0, float(v))))
                    except ValueError: continue
                db().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        db().commit(); flash("Settings saved.", "ok"); return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html", keys=SETTINGS_DEFAULTS)
# ---------- admin: voice / read-out engine ----------
VOICE_DEFAULTS = {"voice_engine": "edge", "voice_he": "he-IL-AvriNeural", "voice_onk": "he-IL-AvriNeural", "voice_speed": "1.0",
                  "voice_havara": "hungarian", "voice_pesukim_source": config.HEBREW_AUDIO, "voice_content": "mikra2_targum"}
VOICE_CONTENT = {"mikra2_targum": "Pasuk twice + Onkelos (Shnayim Mikra)", "mikra1_targum": "Pasuk once + Onkelos",
                 "mikra_only": "Pesukim only", "targum_only": "Onkelos only"}
def vsetting(k):
    r = db().execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else VOICE_DEFAULTS[k]
def voice_preview_text(havara_style, content):
    import gen_audio
    v = plan.PORTIONS[0]["verses"][0]
    old = gen_audio.HAVARA; gen_audio.HAVARA = havara_style
    try: heb, onk = gen_audio.tts_text(v["heb"]), gen_audio.tts_text(v["onk"], True)
    finally: gen_audio.HAVARA = old
    return {"mikra2_targum": [heb, heb, onk], "mikra1_targum": [heb, onk], "mikra_only": [heb], "targum_only": [onk]}.get(content, [heb, heb, onk])
@app.route("/admin/voice", methods=["GET", "POST"])
@admin_required
def admin_voice():
    import voice_engine, havara
    preview = None
    if request.method == "POST":
        f = request.form
        if f.get("voice_engine") not in voice_engine.ENGINES or f.get("voice_havara") not in havara.STYLES or f.get("voice_content") not in VOICE_CONTENT:
            flash("Please pick from the lists.", "error"); return redirect(url_for("admin_voice"))
        try: f_speed = str(min(1.5, max(0.6, float(f.get("voice_speed", "1")))))
        except ValueError: f_speed = "1.0"
        vals = {k: f.get(k, VOICE_DEFAULTS[k]).strip()[:80] for k in VOICE_DEFAULTS}; vals["voice_speed"] = f_speed
        if vals["voice_pesukim_source"] not in ("pockettorah", "tts", "clone"): vals["voice_pesukim_source"] = "pockettorah"
        for k, v in vals.items():
            db().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        db().commit()
        if f.get("action") == "preview":
            try:
                parts = voice_preview_text(vals["voice_havara"], vals["voice_content"])
                h = hashlib.sha1(json.dumps(vals, sort_keys=True).encode()).hexdigest()[:12]
                d = os.path.join(BASE, "data", "voice_preview"); os.makedirs(d, exist_ok=True)
                out = os.path.join(d, h + ".mp3")
                if not os.path.exists(out):
                    audio = b"".join(voice_engine.synth(t, vals["voice_engine"], vals["voice_onk" if (i == len(parts) - 1 and vals["voice_content"] != "mikra_only") else "voice_he"], vals["voice_speed"]) for i, t in enumerate(parts))
                    open(out, "wb").write(audio)
                session["voice_preview"] = h; flash("Saved – preview below (Bereishis 1:1).", "ok")
            except Exception as e:
                flash(f"Preview failed: {e}", "error")
        else:
            flash("Voice settings saved. They apply to the next audio build.", "ok")
        return redirect(url_for("admin_voice"))
    h = session.get("voice_preview")
    if h and os.path.exists(os.path.join(BASE, "data", "voice_preview", h + ".mp3")): preview = url_for("admin_voice_preview", h=h)
    return render_template("admin_voice.html", engines=voice_engine.status(), styles=havara.STYLES, contents=VOICE_CONTENT,
                           cur={k: vsetting(k) for k in VOICE_DEFAULTS}, preview=preview, sets=voice_sets(fresh=True), total=plan.TOTAL,
                           build=build_state(), clone_ready=clone_ready(), clone_est=session.get("clone_est"),
                           has_clone=any(v["id"] == "audio_clone" for v in voice_sets()))
@app.route("/admin/voice/preview/<h>.mp3")
@admin_required
def admin_voice_preview(h):
    from flask import send_from_directory
    if not h.isalnum(): abort(404)
    return send_from_directory(os.path.join(BASE, "data", "voice_preview"), h + ".mp3", mimetype="audio/mpeg")
# ---------- admin: audio builds (rebuild with the Voice settings, clone batch, status) ----------
BUILD_STATE = os.path.join(BASE, "data", "audio_build.json"); BUILD_LOG = os.path.join(BASE, "data", "audio_build.log")
def build_state():
    try: st = json.load(open(BUILD_STATE))
    except Exception: return None
    if st.get("status") == "running":
        try: os.kill(st["pid"], 0); alive = subprocess.run(["ps", "-o", "stat=", "-p", str(st["pid"])], capture_output=True, text=True).stdout.strip()[:1] not in ("Z", "")
        except Exception: alive = False
        if not alive: st["status"] = "finished"; json.dump(st, open(BUILD_STATE, "w"))
    try: st["log"] = open(BUILD_LOG).read().splitlines()[-12:]
    except Exception: st["log"] = []
    return st
def clone_ready():
    return bool(os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_VOICE_ID"))
def clone_estimate(a, b):
    r = subprocess.run([sys.executable, os.path.join(BASE, "gen_full_clone.py"), str(a), str(b), "--dry-run"], capture_output=True, text=True, cwd=BASE, timeout=120)
    return (r.stdout.strip().splitlines() or ["(no output)"])[0]
def start_build(cmd, env, info):
    st = build_state()
    if st and st.get("status") == "running": raise RuntimeError("A build is already running.")
    log = open(BUILD_LOG, "w")
    pr = subprocess.Popen(cmd, cwd=BASE, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    json.dump({**info, "pid": pr.pid, "status": "running", "started": iso(now_utc())}, open(BUILD_STATE, "w"))
@app.route("/admin/voice/build", methods=["POST"])
@admin_required
def admin_voice_build():
    import signal
    f = request.form; act = f.get("action")
    try: a, b = max(0, int(f.get("from", 0))), min(plan.TOTAL - 1, int(f.get("to", 0)))
    except ValueError: a, b = 0, 0
    if b < a: a, b = b, a
    try:
        if act == "rebuild":
            # computer-voice rebuild with the saved Voice settings (havara, voice, speed) -> static/audio_rebuild (free engine)
            if vsetting("voice_engine") != "edge": raise RuntimeError("Rebuild runs the free computer voice; for a paid engine or the clone use the clone build below.")
            out = os.path.join(BASE, "static", "audio_rebuild"); os.makedirs(out, exist_ok=True)
            for i in range(a, b + 1):
                for ext in ("mp3", "json"):
                    try: os.remove(os.path.join(out, f"{i}.{ext}"))
                    except FileNotFoundError: pass
            sp = float(vsetting("voice_speed") or 1); rate = f"{round((sp - 1) * 100):+d}%"
            json.dump({"label": f"Computer voice ({havara_label(vsetting('voice_havara'))}, rebuilt)", "priority": 15}, open(os.path.join(out, "voice.json"), "w"))
            start_build([sys.executable, "gen_audio.py", str(a), str(b)], {"AUDIO_OUT": out, "HAVARA": vsetting("voice_havara"), "TTS_RATE": rate, "TTS_VOICE": vsetting("voice_he")},
                        {"kind": "rebuild", "set": "audio_rebuild", "from": a, "to": b})
            flash(f"Rebuilding aliyos {a}-{b} with the saved Voice settings. Progress below.", "ok")
        elif act == "clone_estimate":
            session["clone_est"] = clone_estimate(a, b); flash("Cost estimate below. Nothing was generated.", "ok")
        elif act == "clone_rehearsal":
            start_build([sys.executable, "gen_full_clone.py", str(a), str(b)], {"CLONE_ENGINE": "edge"}, {"kind": "clone rehearsal (free)", "set": "audio_clone_rehearsal", "from": a, "to": b})
            flash("Free rehearsal of the clone pipeline started (computer voice, no cost).", "ok")
        elif act == "clone_build":
            if not clone_ready(): raise RuntimeError("Add ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID on the server first.")
            if f.get("confirm", "").strip().lower() != "i accept the cost": raise RuntimeError('Type "I accept the cost" to start the paid build.')
            start_build([sys.executable, "gen_full_clone.py", str(a), str(b)], {}, {"kind": "clone (paid)", "set": "audio_clone", "from": a, "to": b})
            flash("Clone build started.", "ok")
        elif act == "cancel":
            st = build_state()
            if st and st.get("status") == "running":
                try: os.killpg(st["pid"], signal.SIGTERM)
                except Exception: pass
                st["status"] = "cancelled"; st.pop("log", None); json.dump(st, open(BUILD_STATE, "w")); flash("Build stopped. Re-running resumes from the cache.", "ok")
        _VS_CACHE["t"] = 0
    except RuntimeError as e:
        flash(str(e), "error")
    return redirect(url_for("admin_voice") + "#builds")
def havara_label(k):
    import havara
    return havara.STYLES.get(k, (k,))[0]
def hebrew_audio():
    try: return vsetting("voice_pesukim_source")
    except Exception: return config.HEBREW_AUDIO
def account_tags(u, sm, done, last):
    tags = []
    if u["is_admin"]: tags.append("admin")
    if u["account_type"] in ("demo", "tour"): tags.append(u["account_type"])
    created = parse(u["created_at"])
    if (now_utc() - created).days < 7 or 0 < done <= 7: tags.append("first-week")
    if sm["streak_weeks"] >= 13 or sm["best_weeks"] >= 13: tags.append("3-month")
    if last and (now_utc() - parse(last)).days >= 7: tags.append("inactive")
    if sm["this_week"] > 0: tags.append("active-this-week")
    return tags
@app.route("/admin/accounts")
@admin_required
def admin_accounts():
    tag = request.args.get("tag", ""); q = request.args.get("q", "").strip().lower()
    rows = []
    for u in db().execute("SELECT * FROM users ORDER BY created_at DESC").fetchall():
        times = completion_times(u["id"]); sm = rewards.summary(times); last = max(times) if times else None
        tags = account_tags(u, sm, len(times), last)
        sefer = db().execute("SELECT COUNT(*) FROM completions WHERE user_id=? AND source='sefer'", (u["id"],)).fetchone()[0]
        rows.append({"u": u, "done": len(times), "sm": sm, "last": last, "tags": tags, "sefer": sefer})
    counts = {}
    for r in rows:
        for t in r["tags"]: counts[t] = counts.get(t, 0) + 1
    shown = [r for r in rows if (not tag or tag in r["tags"]) and (not q or q in r["u"]["name"].lower() or q in r["u"]["username"].lower())]
    return render_template("admin_accounts.html", rows=shown, counts=counts, tag=tag, q=q, total=len(rows))

# ---------- profile / trophy case ----------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u = current_user()
    if request.method == "POST":
        phone = norm_phone(request.form.get("phone", ""))
        pin = request.form.get("pin", "").strip() or None
        name = request.form.get("name", "").strip() or u["name"]
        err = None
        if phone and not (phone.startswith("+1") and len(phone) == 12): err = "Please enter a 10-digit US phone number, e.g. (718) 555-0123."
        elif pin and not (pin.isdigit() and 4 <= len(pin) <= 6): err = "PIN must be 4–6 digits."
        elif phone and db().execute("SELECT 1 FROM users WHERE phone=? AND id!=?", (phone, u["id"])).fetchone(): err = "That phone number is already linked to another account."
        elif pin and db().execute("SELECT 1 FROM users WHERE pin=? AND id!=?", (pin, u["id"])).fetchone(): err = "That PIN is taken – please choose another."
        if err: flash(err, "error")
        else:
            db().execute("UPDATE users SET phone=?, pin=?, name=? WHERE id=?", (phone, pin, name, u["id"])); db().commit()
            flash("Saved!", "ok")
        return redirect(url_for("profile"))
    st = stats(u["id"])
    cards = db().execute("SELECT * FROM scratch_cards WHERE user_id=? AND revealed_at IS NOT NULL ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall()
    return render_template("profile.html", st=st, cards=cards)

# ---------- audio clips (one line = one segment) ----------
def audio_source_path(d, idx):
    """Local mp3 path, or the permanent external URL (ffmpeg reads http) when the build lives off-box."""
    p = os.path.join(BASE, "static", d, f"{idx}.mp3")
    return p if os.path.exists(p) else _manifest(d).get("base_url", "").rstrip("/") + f"/{idx}.mp3"
@app.route("/audio/clip/<int:idx>/<int:seg>.mp3")
def audio_clip(idx, seg):
    meta = audio_meta(idx)
    if not meta or not 0 <= seg < len(meta["segments"]): abort(404)
    d = os.path.join(BASE, "static/audio/clips"); os.makedirs(d, exist_ok=True)
    ver = meta.get("version", 1)   # new audio version -> new clip files (old cached clips are never reused)
    name = f"{meta['dir']}_{idx}_{seg}_v{ver}.mp3"
    out = os.path.join(d, name)
    if not os.path.exists(out):
        sg = meta["segments"][seg]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(sg["start"]), "-to", str(sg["end"] + 0.15), "-i",
                        audio_source_path(meta["dir"], idx), "-c:a", "libmp3lame", "-b:a", "48k", out + ".tmp.mp3"], check=True)
        os.replace(out + ".tmp.mp3", out)
    return app.send_static_file(f"audio/clips/{name}")

# ---------- phone hotline (TwiML-style webhooks) ----------
# Ready for Twilio (or any TwiML-compatible provider): point the number's Voice webhook at POST /voice/incoming.
# If TWILIO_AUTH_TOKEN is set, X-Twilio-Signature is verified. The browser simulator (/hotline) drives the same endpoints.
tok = URLSafeTimedSerializer(app.secret_key, salt="hotline")
def twiml(body): return app.response_class('<?xml version="1.0" encoding="UTF-8"?>\n<Response>' + body + '</Response>', mimetype="text/xml")
def esc(t): return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
def say(t): return f'<Say voice="Polly.Matthew">{esc(t)}</Say>'
def absu(path): return request.url_root.rstrip("/") + path
def vurl(name, **kw): return esc(url_for(name, **kw))
def call_uid():
    try: return tok.loads(request.values.get("t", ""), max_age=6 * 3600)
    except BadSignature: abort(403)
@app.before_request
def twilio_verify():
    if request.path.startswith("/voice/") and os.environ.get("TWILIO_AUTH_TOKEN") and not request.headers.get("X-Hotline-Simulator"):
        data = request.url + "".join(k + v for k, v in sorted(request.form.items()))
        sig = base64.b64encode(hmac.new(os.environ["TWILIO_AUTH_TOKEN"].encode(), data.encode(), hashlib.sha1).digest()).decode()
        if not hmac.compare_digest(sig, request.headers.get("X-Twilio-Signature", "")): abort(403)
def portion_intro(uid):
    done = done_set(uid); i = current_idx(done)
    if i is None: return None, say("Mazel tov! You have completed the entire Torah. Chazak chazak v'nischazek!") + "<Hangup/>"
    p = plan.PORTIONS[i]
    return i, f"Today's portion is Parshas {p['parsha']}, {p['aliyah_en']} aliyah. "
@app.route("/voice/incoming", methods=["GET", "POST"])
def voice_incoming():
    frm = norm_phone(request.values.get("From", ""))
    u = db().execute("SELECT * FROM users WHERE phone=?", (frm,)).fetchone() if frm else None
    if u:
        return twiml(say(f"Shalom {u['name']}! Welcome to the {config.BRAND_NAME} hotline.") + f'<Redirect method="POST">{vurl("voice_menu", t=tok.dumps(u["id"]))}</Redirect>')
    return twiml(say(f"Shalom, and welcome to the {config.BRAND_NAME} hotline.") +
                 f'<Gather input="dtmf" finishOnKey="#" timeout="8" action="{vurl("voice_pin")}" method="POST">' + say("Please enter your PIN, then press the pound key.") + "</Gather>" +
                 say("We did not get a PIN. You can set your PIN on the website under My Profile. Goodbye!") + "<Hangup/>")
@app.route("/voice/pin", methods=["POST"])
def voice_pin():
    pin = request.values.get("Digits", "").strip("#")
    u = db().execute("SELECT * FROM users WHERE pin=?", (pin,)).fetchone() if pin else None
    if not u:
        return twiml(f'<Gather input="dtmf" finishOnKey="#" timeout="8" action="{vurl("voice_pin")}" method="POST">' + say("Sorry, that PIN was not found. Please try again, then press pound.") + "</Gather>" + say("Goodbye!") + "<Hangup/>")
    return twiml(say(f"Shalom {u['name']}!") + f'<Redirect method="POST">{vurl("voice_menu", t=tok.dumps(u["id"]))}</Redirect>')
@app.route("/voice/menu", methods=["POST"])
def voice_menu():
    uid = call_uid(); t = request.values.get("t")
    i, intro = portion_intro(uid)
    if i is None: return twiml(intro)
    has = audio_meta(i) is not None
    if not has: return twiml(say(intro + "The audio for this portion is still being prepared. Please try again later. Goodbye!") + "<Hangup/>")
    return twiml(f'<Gather input="dtmf" numDigits="1" timeout="8" action="{vurl("voice_menu_choice", t=t)}" method="POST">' +
                 say(intro + "To hear the whole portion straight through, press 1. To go pasuk by pasuk, line by line, press 2. To mark today's portion as done, press 9.") +
                 "</Gather>" + f'<Redirect method="POST">{vurl("voice_menu", t=t)}</Redirect>')
@app.route("/voice/menu-choice", methods=["POST"])
def voice_menu_choice():
    uid = call_uid(); t = request.values.get("t"); dg = request.values.get("Digits", "")
    i, _ = portion_intro(uid)
    if dg == "1":
        return twiml(say("Say it along with the reader.") + f'<Play>{esc(absu(audio_meta(i)["src"]))}</Play>' +
                     f'<Gather input="dtmf" numDigits="1" timeout="10" action="{vurl("voice_after_full", t=t)}" method="POST">' +
                     say("You reached the end of the portion. To mark it done, press 9. To hear it again, press 2. For the main menu, press 0.") + "</Gather>" + say("Goodbye!") + "<Hangup/>")
    if dg == "2": return twiml(f'<Redirect method="POST">{vurl("voice_line", t=t, i=0, help=1)}</Redirect>')
    if dg == "9": return twiml(f'<Redirect method="POST">{vurl("voice_done", t=t)}</Redirect>')
    return twiml(f'<Redirect method="POST">{vurl("voice_menu", t=t)}</Redirect>')
@app.route("/voice/after-full", methods=["POST"])
def voice_after_full():
    t = request.values.get("t"); call_uid(); dg = request.values.get("Digits", "")
    if dg == "9": return twiml(f'<Redirect method="POST">{vurl("voice_done", t=t)}</Redirect>')
    if dg == "2": return twiml(f'<Redirect method="POST">{vurl("voice_menu_choice", t=t, Digits="1")}</Redirect>')
    return twiml(f'<Redirect method="POST">{vurl("voice_menu", t=t)}</Redirect>')
KIND_SAY = {"mikra1": "", "mikra2": "", "targum": ""}
@app.route("/voice/line", methods=["POST"])
def voice_line():
    uid = call_uid(); t = request.values.get("t"); n = request.values.get("i", type=int) or 0
    i, _ = portion_intro(uid); meta = audio_meta(i); segs = meta["segments"]
    if n >= len(segs):
        return twiml(f'<Gather input="dtmf" numDigits="1" timeout="10" action="{vurl("voice_after_full", t=t)}" method="POST">' +
                     say("That was the last line. To mark today's portion done, press 9. For the main menu, press 0.") + "</Gather>" + say("Goodbye!") + "<Hangup/>")
    n = max(0, n); help_txt = say("Line by line. After each line: press 1 for the next line, 2 to repeat, 3 to go back, 9 to mark done.") if request.values.get("help") else ""
    return twiml(help_txt + f'<Gather input="dtmf" numDigits="1" timeout="6" action="{vurl("voice_line_choice", t=t, i=n)}" method="POST">' +
                 f'<Play>{esc(absu("/audio/clip/%d/%d.mp3" % (i, n)))}</Play></Gather>' +
                 f'<Redirect method="POST">{vurl("voice_line", t=t, i=n + 1)}</Redirect>')
@app.route("/voice/line-choice", methods=["POST"])
def voice_line_choice():
    call_uid(); t = request.values.get("t"); n = request.values.get("i", type=int) or 0; dg = request.values.get("Digits", "")
    if dg == "9": return twiml(f'<Redirect method="POST">{vurl("voice_done", t=t)}</Redirect>')
    if dg == "0": return twiml(f'<Redirect method="POST">{vurl("voice_menu", t=t)}</Redirect>')
    nxt = {"1": n + 1, "2": n, "3": max(0, n - 1)}.get(dg, n + 1)
    return twiml(f'<Redirect method="POST">{vurl("voice_line", t=t, i=nxt)}</Redirect>')
@app.route("/voice/done", methods=["POST"])
def voice_done():
    uid = call_uid(); t = request.values.get("t")
    i, _ = portion_intro(uid)
    if i is None: return twiml(say("You already finished the whole Torah. Mazel tov!") + "<Hangup/>")
    p = plan.PORTIONS[i]; mark_done(uid, i)
    i2, intro2 = portion_intro(uid)
    msg = f"Mazel tov! Parshas {p['parsha']}, {p['aliyah_en']} is marked done. A surprise card is waiting for you on the website. "
    if i2 is None: return twiml(say(msg) + intro2)
    return twiml(f'<Gather input="dtmf" numDigits="1" timeout="8" action="{vurl("voice_menu_choice", t=t)}" method="POST">' +
                 say(msg + "Next: " + intro2 + "To start it now, press 1 or 2. Otherwise, have a wonderful day!") + "</Gather>" + say("Goodbye!") + "<Hangup/>")
TUTORIAL = [
    ("Create an account", "Your name, a username and a password. Your first portion opens right away."),
    ("Today's portion", "One aliyah each day – Sunday is Rishon, Shabbos is Shevi'i – with your streak and progress through the Torah."),
    ("Three ways to listen", "Tap a line to hear only that line. Auto-read plays straight through, highlighting each line as it is read. Listen only hides the text – for children, or on the road. Your choice is remembered."),
    ("Mark it done", "Confirm and tap “I finished today's portion”. Learned from your own Chumash? Choose that option – it counts the same."),
    ("A small surprise", "Each completed portion reveals a card: an instant prize, a bonus entry, or another chance tomorrow."),
    ("Medals", "A gold mark for every perfect week of seven portions. A black mark – a rare honor – for ten perfect weeks in a row, or a completed Chumash."),
    ("Streaks", "Consecutive perfect weeks multiply your draw entries: ×2 at four weeks, ×3 at ten, ×4 at thirteen, ×5 at twenty – which also opens the grand-finale draw for a car. A missed week resets the streak."),
    ("Three weeks", "At twenty-one portions, a celebration and a word from Chazal on the greatness of Shnayim Mikra."),
    ("Surprise draws", "Draws open at unannounced times. Each portion in the window is an entry, multiplied by your streak. Winners appear under Prizes."),
    ("The hotline", "Call, enter your PIN, and say it along: 1 for the whole portion, 2 line by line, 9 to mark it done."),
]
@app.route("/how")
def how():
    return render_template("how.html", steps=TUTORIAL, has_video=os.path.exists(os.path.join(BASE, "static/tutorial.mp4")))
@app.route("/hotline")
def hotline_sim():
    return render_template("hotline.html")

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8095, debug=False)
