"""Streaks, medals, levels, raffle multipliers. All dates are US Eastern (America/New_York); weeks run Sunday..Shabbos."""
import datetime, json, os
from zoneinfo import ZoneInfo
import plan
TZ = ZoneInfo("America/New_York")
CHAZAL = json.load(open(os.path.join(plan.BASE, "data/chazal.json")))
PER_WEEK = 7  # a "full week" = 7 portions (a whole parsha) completed during that Sunday-Shabbos week

# Streak tiers: consecutive full weeks -> raffle multiplier + prize level
TIERS = [
    {"weeks": 0,  "name": "Starter",        "he": "מתחיל",  "mult": 1, "prize_level": "Instant wins + regular raffles",              "icon": "🌱"},
    {"weeks": 4,  "name": "Chizuk Tier",    "he": "מתחזק",  "mult": 2, "prize_level": "Silver raffle prizes",     "icon": "🔥"},
    {"weeks": 10, "name": "Masmid Tier",    "he": "מתמיד",  "mult": 3, "prize_level": "Gold prizes (Mishnah Berurah, full Shas set)",        "icon": "💎"},
    {"weeks": 13, "name": "3-Month Champion", "he": "אלוף",   "mult": 4, "prize_level": "Big top-winner prizes ($1,000 gift card, appliance)", "icon": "🏆"},
    {"weeks": 20, "name": "Gibor Tier",     "he": "גיבור",  "mult": 5, "prize_level": "Grand-finale CAR draw unlocked",  "icon": "👑"},
]
def book_end_counts():
    ends = {}
    for p in plan.PORTIONS: ends[p["book"]] = p["idx"] + 1
    return ends
BOOK_ENDS = book_end_counts()
# Levels / phases by number of portions completed (big celebration at week 3)
LEVELS = [
    {"count": 1,   "title": "Your first portion",        "he": "התחלתם",           "msg": "The first step is behind you. Tomorrow’s portion is ready when you are.", "big": False},
    {"count": 7,   "title": "Week one", "he": "שבוע ראשון", "msg": "A complete parsha, Shnayim Mikra v'Echad Targum.", "big": False},
    {"count": 21,  "title": "Three weeks", "he": "שלושה שבועות", "msg": "Three weeks of Shnayim Mikra. It has become a habit – a chazakah. The whole Torah is within reach.", "big": True},
    {"count": 42,  "title": "Six weeks", "he": "שישה שבועות", "msg": "Six weeks, steady and consistent.", "big": False},
    {"count": BOOK_ENDS["Genesis"],    "title": "Sefer Bereishis", "he": "חזק חזק ונתחזק", "msg": "You have completed Sefer Bereishis.", "big": True},
    {"count": BOOK_ENDS["Exodus"],     "title": "Sefer Shemos", "he": "חזק חזק ונתחזק", "msg": "You have completed Sefer Shemos.", "big": True},
    {"count": BOOK_ENDS["Leviticus"],  "title": "Sefer Vayikra", "he": "חזק חזק ונתחזק", "msg": "You have completed Sefer Vayikra – past the halfway mark.", "big": True},
    {"count": BOOK_ENDS["Numbers"],    "title": "Sefer Bamidbar", "he": "חזק חזק ונתחזק", "msg": "You have completed Sefer Bamidbar. One Chumash remains.", "big": True},
    {"count": plan.TOTAL,              "title": "Siyum HaTorah", "he": "סיום התורה", "msg": "You have completed the entire Torah, Shnayim Mikra v'Echad Targum. Mazel tov.", "big": True},
]
def level_for(count):
    reached = [l for l in LEVELS if count >= l["count"]]
    nxt = next((l for l in LEVELS if count < l["count"]), None)
    return (reached[-1] if reached else None), nxt
def milestone_hit(count):
    return next((l for l in LEVELS if l["count"] == count), None)
def week_start(d):
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)
def local_date(iso_s):
    return datetime.datetime.strptime(iso_s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).astimezone(TZ).date()
def summary(completion_times, as_of=None, revealed_bonus=0):
    """completion_times: list of ISO UTC strings. Returns streak/medals/tier info as of a date."""
    today = as_of or datetime.datetime.now(TZ).date()
    per_week = {}
    for t in completion_times:
        d = local_date(t)
        if d <= today: per_week[week_start(d)] = per_week.get(week_start(d), 0) + 1
    full_weeks = sorted(w for w, n in per_week.items() if n >= PER_WEEK)
    fullset = set(full_weeks)
    cur = week_start(today)
    w = cur if cur in fullset else cur - datetime.timedelta(days=7)  # current week still in progress doesn't break the streak
    streak = 0
    while w in fullset: streak += 1; w -= datetime.timedelta(days=7)
    # longest run (for black medals)
    best, run, prev = 0, 0, None
    runs_black = 0
    for w in full_weeks:
        run = run + 1 if prev and w - prev == datetime.timedelta(days=7) else 1
        if run % 10 == 0: runs_black += 1
        best = max(best, run); prev = w
    tier = [t for t in TIERS if streak >= t["weeks"]][-1]
    next_tier = next((t for t in TIERS if streak < t["weeks"]), None)
    count = len([t for t in completion_times])
    gold = [{"week": w.isoformat(), "label": f"Perfect week of {w.strftime('%d %b %Y')}"} for w in full_weeks]
    black = []
    for i in range(runs_black): black.append({"label": f"10 full weeks in a row (#{i+1})"})
    for b, n in BOOK_ENDS.items():
        if count >= n: black.append({"label": f"Completed Sefer {plan.BOOK_EN[b]}"})
    this_week = per_week.get(cur, 0)
    return {"streak_weeks": streak, "best_weeks": best, "tier": tier, "next_tier": next_tier, "mult": tier["mult"],
            "gold": gold, "black": black, "this_week": this_week, "week_goal": PER_WEEK}
def chazal_for(n):
    return CHAZAL[n % len(CHAZAL)]
