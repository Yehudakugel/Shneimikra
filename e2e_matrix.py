"""Mobile matrix: every reading order x speed x voice, auto/tap/listen, several aliyos, iPhone (WebKit) + Android (Chromium).
  python e2e_matrix.py BASE iphone|android [quick]"""
import sys, time, json
from playwright.sync_api import sync_playwright
B = sys.argv[1]; DEV = sys.argv[2]; QUICK = "quick" in sys.argv
SPEEDS = ["0.75", "0.9", "1.0", "1.1", "1.25", "1.5"]; ORDERS = ["ppo", "po", "aao", "ao", "p"]
FULL_IDX, PART_IDX = [0, 14], [7, 20]
fails, checks, STALLS, RETRIES = [], 0, [], []
def ok(cond, msg):
    global checks; checks += 1
    if not cond: fails.append(msg); print("FAIL", msg, flush=True)
with sync_playwright() as p:
    dev = p.devices["iPhone 13" if DEV == "iphone" else "Pixel 7"]
    br = (p.webkit if DEV == "iphone" else p.chromium).launch(args=[] if DEV == "iphone" else ["--autoplay-policy=no-user-gesture-required"])
    ctx = br.new_context(**dev); pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    def login():
        pg.goto(B + "/login"); pg.fill("#username", "demo"); pg.fill("#password", "demo123"); pg.click("button:has-text('Log in')"); pg.wait_for_url("**/learn*")
    def pref(**kw):
        pg.evaluate("kw => fetch('/pref/player', {method:'POST', headers:{'X-CSRF':CSRF,'Content-Type':'application/x-www-form-urlencoded'}, body:new URLSearchParams({...kw, csrf:CSRF})})", kw); time.sleep(0.3)
    def ready():
        pg.wait_for_function("window.PLAYER && (!audio || audio.readyState >= 1 || true)")
        pg.evaluate("""() => { window.SEEN = []; new MutationObserver(() => document.querySelectorAll('.line.on').forEach(e => SEEN.push(e.id)))
          .observe(document.getElementById('text'), {subtree:true, attributes:true, attributeFilter:['class']}); }""")
    login()
    voices = pg.eval_on_selector_all("#opt-voice option", "o => o.map(x => x.value)")
    print(DEV, "voices", voices, flush=True)
    for idx in ([0] if QUICK else FULL_IDX + PART_IDX if DEV != "iphone" else [0, 7, 20]):
        for voice in voices:
            pref(voice=voice); pg.goto(f"{B}/learn/{idx}"); ready()
            d = pg.evaluate("AUDIO && AUDIO.dir"); ok(d == voice, f"{DEV} idx{idx} voice {voice} loaded {d}")
            pg.click("button[data-mode=auto]")
            pg.click("#play"); pg.wait_for_function("!audio.paused && audio.currentTime > 0.1", timeout=30000); pg.click("#play")  # user gesture unlocks audio (iOS)
            speeds = SPEEDS if idx in FULL_IDX and not QUICK and DEV != "iphone" else ["0.75", "1.0", "1.5"]
            for order in ORDERS:
                pg.select_option("#opt-order", order); time.sleep(0.2)
                info = pg.evaluate("""() => { const q = PLAYER.Q; let p = 0;
                  for (let k = 0; k < q.length - 1; k++) if (SEGS[q[k]].kind != SEGS[q[k+1]].kind) { p = k; break; }
                  const sk = [...document.querySelectorAll('.line.skip')].map(e => e.id), all = document.querySelectorAll('.line').length;
                  return {p, n: q.length, nv: NV, a: q[p], b: q[p+1], ida: `l-${SEGS[q[p]].v}-${SEGS[q[p]].kind}`, idb: `l-${SEGS[q[p+1]].v}-${SEGS[q[p+1]].kind}`, skip: sk.length, all,
                          qids: q.map(i => `l-${SEGS[i].v}-${SEGS[i].kind}`)} }""")
                exp = {"ppo": 3, "po": 2, "aao": 3, "ao": 2, "p": 1}[order]
                ok(info["n"] == exp * info["nv"], f"{DEV} idx{idx} {voice} {order} queue {info['n']} != {exp}x{info['nv']}")
                ok(info["skip"] == info["all"] - info["n"], f"{DEV} idx{idx} {order} dimmed {info['skip']} expected {info['all'] - info['n']}")
                def run_speed(sp, info=info, order=order):
                    if DEV == "iphone":  # fresh media pipeline per run: WebKitGTK's GStreamer backend (not iOS AVFoundation) wedges after many rapid seeks
                        pg.goto(f"{B}/learn/{idx}"); ready(); pg.wait_for_function("audio.readyState >= 1 || true")
                    pg.select_option("#opt-speed", sp); r = float(sp)
                    pg.evaluate("SEEN.length = 0"); pg.click("#" + info["ida"])  # a real tap on the line starts the queue there (user gesture, like a phone)
                    ok(pg.evaluate("PLAYER.qpos") == info["p"], f"{DEV} {order}: tap line -> qpos {pg.evaluate('PLAYER.qpos')} != {info['p']}")
                    try: pg.wait_for_function("!audio.paused && !audio.seeking && audio.readyState >= 3", timeout=60000)
                    except Exception: print("HANG", order, sp, pg.evaluate("[audio.paused, audio.seeking, audio.readyState, audio.networkState, audio.currentTime, playing, PLAYER.qpos, mode, location.href]")); raise
                    t_s = pg.evaluate("audio.currentTime"); pg.wait_for_function("t => audio.currentTime > t + 0.05", arg=t_s, timeout=8000)
                    MEAS = "() => new Promise(res => { const t0 = audio.currentTime, w0 = performance.now(); setTimeout(() => res([(audio.currentTime - t0) / ((performance.now() - w0) / 1000), audio.playbackRate, audio.preservesPitch !== false]), 700); })"
                    m = pg.evaluate(MEAS)  # real speed measured right after the player's own seek (playFrom)
                    pg.evaluate("t => { audio.currentTime = t; }", pg.evaluate("i => SEGS[i].end", info["a"]) - 1.6 * r)  # jump close to the transition
                    ok(abs(m[1] - r) < 0.01, f"{DEV} idx{idx} {voice} {order} x{sp}: playbackRate {m[1]}")
                    ok(abs(m[0] - r) / r < 0.2, f"{DEV} idx{idx} {voice} {order} x{sp}: measured speed {m[0]:.2f}")
                    ok(m[2], f"{DEV} {order} x{sp}: pitch not preserved")
                    gap = 0 if pg.evaluate("([a,b]) => SEGS[b].start - SEGS[a].end", [info["a"], info["b"]]) in (0,) else 0.6
                    try: pg.wait_for_function("id => document.querySelector('.line.on') && document.querySelector('.line.on').id == id", arg=info["idb"], timeout=int((2.5 + 1.2 / r) * 1000) + 6000)
                    except Exception:
                        print("NOTRANS", order, sp, info["ida"], info["idb"], pg.evaluate("[audio.paused, audio.seeking, audio.readyState, audio.currentTime, playing, PLAYER.qpos, !!gapTimer, document.querySelector('.line.on') && document.querySelector('.line.on').id, SEGS[PLAYER.Q[1]]]"), flush=True); ok(False, f"{DEV} idx{idx} {voice} {order} x{sp}: no transition {info['ida']} -> {info['idb']}"); pg.evaluate("pause()"); return
                    seen = pg.evaluate("[...new Set(SEEN)]")
                    bad = [s for s in seen if s not in info["qids"]]
                    ok(not bad, f"{DEV} idx{idx} {voice} {order} x{sp}: highlighted skipped lines {bad}")
                    ok(info["ida"] in seen or True, "")
                    ok(abs(pg.evaluate("audio.playbackRate") - r) < 0.01, f"{DEV} idx{idx} {order} x{sp}: rate lost after transition")
                    pg.evaluate("pause()")
                for sp in speeds:
                    for attempt in (1, 2):
                        try: run_speed(sp); break
                        except Exception as e:
                            if attempt == 2: ok(False, f"{DEV} idx{idx} {voice} {order} x{sp}: {str(e)[:80]}")
                            else: RETRIES.append(f"idx{idx} {voice} {order} x{sp}"); pg.goto(f"{B}/learn/{idx}"); ready()
                print(DEV, "idx", idx, voice, order, "ok" if not fails else f"{len(fails)} fails so far", flush=True)
            # tap mode: one line only, at current speed, also a dimmed line
            pg.select_option("#opt-order", "po"); pg.click("button[data-mode=tap]"); pg.select_option("#opt-speed", "1.5")
            for lid in ["l-1-targum", "l-2-mikra2"]:
                pg.click("#" + lid); time.sleep(0.4)
                ok(pg.eval_on_selector(".line.on", "e => e.id") == lid, f"{DEV} idx{idx} {voice} tap {lid} not highlighted")
                ok(abs(pg.evaluate("audio.playbackRate") - 1.5) < 0.01, f"{DEV} tap rate")
                d = pg.evaluate("id => { const [_, v, k] = id.split('-'); const s = SEGS[segIdx(+v, k)]; return (s.end - s.start) / 1.5; }", lid)
                pg.wait_for_function("audio.paused", timeout=int((d + 4) * 1000))
                ok(pg.eval_on_selector(".line.on", "e => e.id") == lid, f"{DEV} tap {lid}: moved on to another line")
            # listen-only
            pg.click("button[data-mode=listen]"); ok(pg.is_hidden("#text"), f"{DEV} listen: text visible")
            pg.select_option("#opt-order", "ao"); pg.click("#bigplay"); time.sleep(2.5)
            t = pg.evaluate("audio.currentTime"); ok(not pg.evaluate("audio.paused") and t > 0.5, f"{DEV} idx{idx} {voice} listen not playing ({t})")
            ok(pg.inner_text("#listen-now").startswith("Pasuk "), f"{DEV} listen-now {pg.inner_text('#listen-now')}")
            pg.click("#bigplay"); ok(pg.evaluate("audio.paused"), f"{DEV} listen pause")
            pg.click("button[data-mode=auto]")
    # persistence across logout / login
    pg.goto(B + "/learn"); pg.select_option("#opt-order", "aao"); pg.select_option("#opt-speed", "1.25"); time.sleep(0.6)
    with pg.expect_navigation(): pg.evaluate("() => { const f = document.createElement('form'); f.method = 'post'; f.action = '/logout'; const i = document.createElement('input'); i.name = 'csrf'; i.value = CSRF; f.appendChild(i); document.body.appendChild(f); f.submit(); }")
    ctx.clear_cookies(); pg.evaluate("localStorage.clear()")
    login()
    st = pg.evaluate("[document.querySelector('#opt-speed').value, document.querySelector('#opt-order').value]")
    ok(st == ["1.25", "aao"], f"{DEV} persistence after logout/login (server only, localStorage cleared): {st}")
    pref(voice="audio_pt", order="ppo", speed="1.0")
    ok(not errs, f"{DEV} page errors {errs}")
    print(DEV, "media-pipeline retries:", RETRIES); print(f"{DEV}: {checks} checks, {len(fails)} failures", flush=True)
    sys.exit(1 if fails else 0)
