// Quiet celebration effects: muted-gold confetti and soft synthesized chimes (no audio files).
const FX = (() => {
  let ctx;
  const ac = () => (ctx = ctx || new (window.AudioContext || window.webkitAudioContext)());
  function tone(freq, start, dur, vol = 0.07) {
    const a = ac(), o = a.createOscillator(), g = a.createGain();
    o.type = "sine"; o.frequency.value = freq; o.connect(g); g.connect(a.destination);
    const t = a.currentTime + start; g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0008, t + dur); o.start(t); o.stop(t + dur + 0.05);
  }
  const sounds = {
    fanfare() { [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => tone(f, i * 0.12, 1.4, 0.06)); },
    win() { [880, 1318.5].forEach((f, i) => tone(f, i * 0.11, 1.0, 0.07)); },
    lose() { tone(587.33, 0, 0.8, 0.05); },
    scratch() { tone(2400 + Math.random() * 600, 0, 0.02, 0.008); },
    tick() { tone(1400, 0, 0.06, 0.03); },
    check() { [659.25, 987.77].forEach((f, i) => tone(f, i * 0.09, 0.7, 0.06)); },
  };
  function confetti(ms = 2600, n = 70) {
    let c = document.getElementById("confetti");
    if (!c) { c = document.createElement("canvas"); c.id = "confetti"; document.body.appendChild(c); }
    const x = c.getContext("2d"), dpr = window.devicePixelRatio || 1;
    c.width = innerWidth * dpr; c.height = innerHeight * dpr; x.scale(dpr, dpr);
    const cols = ["#b8975a", "#cdb27c", "#e4d3ae", "#8a6d3b"];
    const ps = Array.from({ length: Math.min(n, 90) }, () => ({ x: Math.random() * innerWidth, y: -10 - Math.random() * innerHeight * 0.4,
      w: 3 + Math.random() * 4, h: 6 + Math.random() * 6, vx: -0.6 + Math.random() * 1.2, vy: 1.2 + Math.random() * 1.8,
      r: Math.random() * 6, vr: -0.06 + Math.random() * 0.12, c: cols[(Math.random() * cols.length) | 0] }));
    const t0 = performance.now(), end = t0 + ms;
    (function f(now) {
      x.clearRect(0, 0, innerWidth, innerHeight);
      const fade = Math.max(0, Math.min(1, (end - now) / 600));
      ps.forEach(p => { p.x += p.vx + Math.sin((now - t0) / 500 + p.r) * 0.3; p.y += p.vy; p.r += p.vr;
        x.save(); x.globalAlpha = 0.9 * fade; x.translate(p.x, p.y); x.rotate(p.r); x.fillStyle = p.c; x.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); x.restore(); });
      if (now < end) requestAnimationFrame(f); else x.clearRect(0, 0, innerWidth, innerHeight);
    })(t0);
  }
  return { sounds, confetti };
})();
