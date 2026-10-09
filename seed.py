"""Seed demo data into a fresh DB. All sample people are fake ("Sample ..." names); all prizes are placeholders."""
import datetime, random, sqlite3
from werkzeug.security import generate_password_hash
import app, rewards
con = sqlite3.connect(app.DB)
NOW = app.now_utc(); TODAY = NOW.astimezone(app.TZ).date()
def user(username, name, pw, admin=0, phone=None, pin=None, kind="regular", created_days_ago=0):
    r = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if r: return r[0], False
    cur = con.execute("INSERT INTO users(username,name,pw_hash,is_admin,created_at,phone,pin,account_type) VALUES(?,?,?,?,?,?,?,?)",
        (username, name, generate_password_hash(pw), admin, app.iso(NOW - datetime.timedelta(days=created_days_ago)), phone, pin, kind))
    return cur.lastrowid, True
def at(day, hour=20):
    return app.iso(datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=app.TZ))
def fill_days(uid, days, rnd=None):
    """Complete portions in order, one per given date (list of local dates)."""
    idx = con.execute("SELECT COUNT(*) FROM completions WHERE user_id=?", (uid,)).fetchone()[0]
    for k, d in enumerate(days):
        if idx >= app.plan.TOTAL: break
        con.execute("INSERT OR IGNORE INTO completions(user_id,portion_idx,completed_at,source) VALUES(?,?,?,?)",
                    (uid, idx, at(d, 20 if d < TODAY else max(0, NOW.astimezone(app.TZ).hour - 1)), "sefer" if rnd and rnd.random() < 0.15 else "app")); idx += 1
def full_weeks_days(n_weeks, partial_this_week=True):
    ws = rewards.week_start(TODAY); days = []
    for w in range(n_weeks, 0, -1):
        s = ws - datetime.timedelta(days=7 * w); days += [s + datetime.timedelta(days=i) for i in range(7)]
    if partial_this_week: days += [ws + datetime.timedelta(days=i) for i in range((TODAY - ws).days)]
    return days
# --- core accounts ---
import os
user("admin", "Program Admin", os.environ.get("ADMIN_PASSWORD") or "admin123", admin=1, kind="regular", created_days_ago=30)
user("demo", "Demo Learner", "demo123", phone="+17185550123", pin="1234", kind="demo")
user("tour", "Guided Tour (demo)", "tour123", pin="8687", kind="tour")
uid, new = user("demo3", "Demo – Almost 3 Weeks", "demo123", pin="3333", kind="demo", created_days_ago=21)
if new:  # 20 portions over the last ~3 weeks -> the next completion triggers the big 3-week celebration
    fill_days(uid, [TODAY - datetime.timedelta(days=k) for k in range(20, 0, -1)])
rnd = random.Random(5787)
# --- 3-month reward accounts (13+ perfect weeks) ---
for i, nm in enumerate(["Sample Shimon Kahane", "Sample Leah Goldfarb", "Sample Ari Teitel"]):
    uid, new = user(f"sample3m{i+1}", nm, "sample123", kind="regular", created_days_ago=120)
    if new: fill_days(uid, full_weeks_days(13 + i * 2), rnd)
# --- mid-streak accounts ---
for i, (nm, w) in enumerate([("Sample Moshe Gold", 4), ("Sample Chaim Weiss", 6), ("Sample Dovid Stern", 10), ("Sample Rivka Klein", 2), ("Sample Yaakov Rosen", 5)]):
    uid, new = user(f"sample{i+1}", nm, "sample123", created_days_ago=w * 7 + 3)
    if new: fill_days(uid, full_weeks_days(w), rnd)
# --- first-week accounts ---
for i, nm in enumerate(["Sample Mendy Friedman", "Sample Avi Schwartz", "Sample Eli Berger", "Sample Sora Weber", "Sample Kid Yossi (age 10)"]):
    d = rnd.randint(1, 5)
    uid, new = user(f"newbie{i+1}", nm, "sample123", created_days_ago=d)
    if new: fill_days(uid, [TODAY - datetime.timedelta(days=x) for x in range(d, 0, -1) if rnd.random() < 0.85], rnd)
# --- inactive ---
uid, new = user("sample_inactive", "Sample Zevi Lapsed", "sample123", created_days_ago=40)
if new: fill_days(uid, [TODAY - datetime.timedelta(days=x) for x in range(40, 25, -1)])
# --- prize catalog (ALL PLACEHOLDERS) ---
if not con.execute("SELECT 1 FROM prizes").fetchone():
    P = [  # name, category, tier, min_weeks, weight, bonus_entries, value
        ("+1 bonus raffle entry", "Bonus entries", "instant", 0, 6, 1, "Counts in the next surprise draw"),
        ("+2 bonus raffle entries", "Bonus entries", "instant", 0, 3, 2, "Counts in the next surprise draw"),
        ("Kids' Parsha sticker pack", "Kids", "instant", 0, 3, 0, "Mailed / picked up at shul"),
        ("Kosher ice-cream cone voucher", "Family outing", "instant", 0, 2, 0, "~$5"),
        ("$5 seforim-store credit", "Seforim-store gift card", "instant", 0, 1, 0, "$5"),
        ("Free pocket Chumash (free sefer)", "Sefarim", "instant", 4, 1, 0, "~$15 – streak 4+ only"),
        ("$50 seforim-store gift card", "Seforim-store gift card", "raffle", 0, 1, 0, "$50"),
        ("Chumash with Onkelos & Rashi (free sefer)", "Sefarim", "raffle", 0, 1, 0, "~$40"),
        ("1-year Torah magazine subscription", "Subscription", "raffle", 0, 1, 0, "~$60"),
        ("Kids' Parsha board game", "Kids", "raffle", 0, 1, 0, "~$30"),
        ("Family outing voucher (bowling / trampoline park)", "Family outing", "raffle", 4, 1, 0, "~$100"),
        ("$100 seforim-store gift card", "Seforim-store gift card", "raffle", 4, 1, 0, "$100"),
        ("Mishnah Berurah set (free sefarim)", "Sefarim", "raffle", 10, 1, 0, "~$150"),
        ("Full Shas set (seforim library)", "Sefarim", "grand", 10, 1, 0, "~$800"),
        ("$1,000 gift card (top-winner prize)", "Cash / gift card", "grand", 13, 1, 0, "$1,000"),
        ("Home appliance (e.g. washer or Shabbos-mode oven)", "Home appliance", "grand", 13, 1, 0, "~$1,000"),
        ("A new car – grand-finale prize for the overall top winner", "Car", "grand", 20, 1, 0, "Grand-finale draw at the Siyum; 20+ week streak to enter"),
    ]
    for p in P:
        con.execute("INSERT INTO prizes(name,category,tier,min_weeks,weight,bonus_entries,value,sponsor,placeholder) VALUES(?,?,?,?,?,?,?,?,1)", p + ("TBD (placeholder)",))
con.commit(); print("seeded", con.execute("SELECT COUNT(*) FROM users").fetchone()[0], "users,", con.execute("SELECT COUNT(*) FROM prizes").fetchone()[0], "prizes")
