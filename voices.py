"""Multi-voice registry: which voice speaks for each use (reading, hotline, promo, announcements).
voices.json lists voices (engine + voice id, or an env var holding the clone's voice id) and per-use defaults.
The admin choice per use is stored in settings key "voiceuse_<use>"; if a use's preferred voice (e.g. a clone) is
ready it is used automatically, otherwise the free default."""
import json, os
import voice_engine
BASE = os.path.dirname(os.path.abspath(__file__))
REG = json.load(open(os.path.join(BASE, "voices.json"), encoding="utf-8"))
def voice_id(v): return v.get("voice") or os.environ.get(v.get("voice_env", ""), "")
def ready(vid):
    v = REG["voices"].get(vid)
    if not v: return False
    if v["engine"] == "polly": return True
    return bool(voice_id(v)) and voice_engine.available(v["engine"])
def resolve(use, chosen=None):
    """-> (voice_key, voice dict). chosen = admin setting ('' / 'auto' = preferred if ready else default)."""
    u = REG["uses"][use]
    for k in ([chosen] if chosen and chosen != "auto" else []) + [u["preferred"], u["default"]]:
        if k and ready(k): return k, REG["voices"][k]
    return u["default"], REG["voices"][u["default"]]
def synth(use, text, chosen=None, speed=1.0):
    k, v = resolve(use, chosen)
    if v["engine"] == "polly": raise RuntimeError("polly is spoken by Twilio itself")
    return voice_engine.synth(text, v["engine"], voice_id(v), speed)
def table(get_setting):
    return [{"use": u, "label": d["label"], "chosen": get_setting("voiceuse_" + u) or "auto", "active": resolve(u, get_setting("voiceuse_" + u))[0],
             "options": [(k, v["label"], ready(k)) for k, v in REG["voices"].items()]} for u, d in REG["uses"].items()]
