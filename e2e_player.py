"""Player controls e2e: speed, voice selector, reading orders, highlight sync, tap, listen-only, persistence."""
import sys, time
from playwright.sync_api import sync_playwright
B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8095"
with sync_playwright() as p:
    br = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    ctx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/login"); pg.fill("#username", "demo"); pg.fill("#password", "demo123"); pg.click("button:has-text('Log in')"); pg.wait_for_url("**/learn*")
    pg.click("button[data-mode=auto]")
    opts = pg.eval_on_selector_all("#opt-voice option", "o => o.map(x => x.value)"); print("voices:", opts); assert len(opts) >= 2
    pg.screenshot(path="screenshots/20-player-controls.png"); print("shot")
    # speed: pitch preserved + persisted
    pg.select_option("#opt-speed", "1.25"); pg.click("#play"); time.sleep(1.5)
    r = pg.evaluate("[audio.playbackRate, audio.preservesPitch]"); print("rate/pitch", r); assert r == [1.25, True]
    pg.evaluate("pause()")
    def run_order(o, expect):
        pg.select_option("#opt-order", o); time.sleep(0.3)
        q = pg.evaluate("PLAYER.Q.map(i => SEGS[i].v + ':' + SEGS[i].kind)")
        assert q[:len(expect)] == expect, (o, q[:6]); return q
    nv = pg.evaluate("NV")
    run_order("ppo", ["0:mikra1", "0:mikra2", "0:targum", "1:mikra1"])
    run_order("po", ["0:mikra1", "0:targum", "1:mikra1", "1:targum"])
    q = run_order("aao", ["0:mikra1", "1:mikra1"]); assert q[nv] == "0:mikra2" and q[2 * nv] == "0:targum"
    q = run_order("ao", ["0:mikra1", "1:mikra1"]); assert q[nv] == "0:targum" and len(q) == 2 * nv
    q = run_order("p", ["0:mikra1", "1:mikra1"]); assert len(q) == nv
    # highlight sync across a queue jump in "po": play near end of 0:mikra1 -> must skip mikra2 and land on 0:targum
    pg.select_option("#opt-order", "po"); pg.select_option("#opt-speed", "1.5")
    pg.evaluate("playFrom(0); audio.currentTime = SEGS[PLAYER.Q[0]].end - 0.4"); time.sleep(2.2)
    on = pg.eval_on_selector(".line.on", "e => e.id"); print("po jump ->", on); assert on == "l-0-targum", on
    pg.evaluate("pause()")
    # whole-aliyah order: after the last pasuk's mikra1 comes pasuk 1 targum ("ao")
    pg.select_option("#opt-order", "ao")
    pg.evaluate(f"playFrom({nv - 1}); audio.currentTime = SEGS[segIdx({nv - 1}, 'mikra1')].end - 0.3"); time.sleep(2.2)
    on = pg.eval_on_selector(".line.on", "e => e.id"); print("ao jump ->", on); assert on == "l-0-targum", on
    pg.evaluate("pause()"); assert pg.is_visible("#l-0-mikra2") and "skip" in pg.get_attribute("#l-0-mikra2", "class")
    # tap mode: plays only the line
    pg.click("button[data-mode=tap]"); pg.click("#l-1-targum"); time.sleep(0.5)
    assert pg.eval_on_selector(".line.on", "e => e.id") == "l-1-targum"
    d = pg.evaluate("(SEGS[segIdx(1,'targum')].end - SEGS[segIdx(1,'targum')].start) / 1.5"); time.sleep(d + 1.2)
    print("tap stopped:", pg.evaluate("audio.paused")); assert pg.evaluate("audio.paused")
    # listen-only
    pg.click("button[data-mode=listen]"); assert pg.is_hidden("#text") and pg.is_visible("#opt-order")
    pg.click("#bigplay"); time.sleep(2); print("listen:", pg.inner_text("#listen-now"), pg.inner_text("#listen-time")); pg.click("#bigplay")
    # persistence (server + localStorage)
    pg.reload(); time.sleep(0.5)
    st = pg.evaluate("[document.querySelector('#opt-speed').value, document.querySelector('#opt-order').value, localStorage.getItem('kp_order')]"); print("persisted", st)
    assert st == ["1.5", "ao", "ao"]
    # voice switch
    other = [o for o in opts if o != pg.evaluate("AUDIO.dir")][0]
    pg.select_option("#opt-voice", other); pg.wait_for_load_state("load"); time.sleep(1.5)
    print("voice now", pg.evaluate("AUDIO.dir")); assert pg.evaluate("AUDIO.dir") == other
    pg.select_option("#opt-voice", opts[0]); time.sleep(1.5)
    pg.select_option("#opt-order", "ppo"); pg.select_option("#opt-speed", "1.0"); pg.click("button[data-mode=auto]"); time.sleep(0.4)
    assert not errs, errs; print("PLAYER E2E OK")
