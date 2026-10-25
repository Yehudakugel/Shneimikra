"""Pluggable voice engines for the read-out (pesukim + Onkelos).
Free default: Microsoft Edge neural voices (edge-tts, no key). Paid engines switch on when their key is set as an env var:
  azure      AZURE_SPEECH_KEY + AZURE_SPEECH_REGION   (SSML, custom lexicon, Custom Neural Voice)
  elevenlabs ELEVENLABS_API_KEY                        (voice cloning - e.g. a licensed baal koreh's voice)
  google     GOOGLE_TTS_API_KEY                        (Chirp 3 HD / WaveNet he-IL)
  openai     OPENAI_API_KEY                            (gpt-4o-mini-tts, steerable)
synth(text, engine, voice, speed) -> mp3 bytes. speed is a multiplier (1.0 = normal)."""
import asyncio, json, os, urllib.request
ENGINES = {
    "edge": {"label": "Microsoft Edge neural (free, current)", "env": [], "voices": ["he-IL-AvriNeural", "he-IL-HilaNeural"]},
    "azure": {"label": "Azure AI Speech neural (enterprise)", "env": ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"],
              "voices": ["he-IL-AvriNeural", "he-IL-HilaNeural", "en-US-AndrewMultilingualNeural"]},
    "elevenlabs": {"label": "ElevenLabs v3 (voice cloning)", "env": ["ELEVENLABS_API_KEY"], "voices": ["(paste voice ID)"]},
    "google": {"label": "Google Cloud TTS (Chirp 3 HD)", "env": ["GOOGLE_TTS_API_KEY"],
               "voices": ["he-IL-Chirp3-HD-Charon", "he-IL-Chirp3-HD-Orus", "he-IL-Wavenet-B", "he-IL-Wavenet-D"]},
    "openai": {"label": "OpenAI gpt-4o-mini-tts", "env": ["OPENAI_API_KEY"], "voices": ["onyx", "ash", "cedar", "echo"]},
}
def available(engine): return all(os.environ.get(k) for k in ENGINES[engine]["env"])
def status(): return {k: {**v, "ready": available(k)} for k, v in ENGINES.items()}
def _pct(speed): return f"{round((float(speed) - 1) * 100):+d}%"
def _post(url, body, headers):
    req = urllib.request.Request(url, data=body if isinstance(body, bytes) else json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r: return r.read()
def synth(text, engine="edge", voice=None, speed=1.0):
    if engine not in ENGINES: raise ValueError("unknown engine")
    if not available(engine): raise RuntimeError(f"{engine} needs env var(s): {', '.join(ENGINES[engine]['env'])}")
    voice = voice or ENGINES[engine]["voices"][0]; speed = float(speed or 1.0)
    if engine == "edge":
        import edge_tts
        async def run():
            buf = b""
            async for c in edge_tts.Communicate(text, voice, rate=_pct(speed)).stream():
                if c["type"] == "audio": buf += c["data"]
            return buf
        return asyncio.run(run())
    if engine == "azure":
        from xml.sax.saxutils import escape
        lang = voice[:5]
        ssml = f'<speak version="1.0" xml:lang="{lang}"><voice name="{voice}"><prosody rate="{_pct(speed)}">{escape(text)}</prosody></voice></speak>'
        return _post(f"https://{os.environ['AZURE_SPEECH_REGION']}.tts.speech.microsoft.com/cognitiveservices/v1", ssml.encode(),
                     {"Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_KEY"], "Content-Type": "application/ssml+xml",
                      "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3", "User-Agent": "schneimaker-kp"})
    if engine == "elevenlabs":
        return _post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
                     {"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_v3"), "language_code": "he",
                      "voice_settings": {"speed": max(0.7, min(1.2, speed))}},
                     {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    if engine == "google":
        import base64
        r = _post(f"https://texttospeech.googleapis.com/v1/text:synthesize?key={os.environ['GOOGLE_TTS_API_KEY']}",
                  {"input": {"text": text}, "voice": {"languageCode": voice[:5], "name": voice},
                   "audioConfig": {"audioEncoding": "MP3", "speakingRate": speed}}, {"Content-Type": "application/json"})
        return base64.b64decode(json.loads(r)["audioContent"])
    if engine == "openai":
        return _post("https://api.openai.com/v1/audio/speech",
                     {"model": "gpt-4o-mini-tts", "voice": voice, "input": text, "speed": speed, "response_format": "mp3",
                      "instructions": "Read slowly and clearly, like a traditional Ashkenazi Torah reader. Pronounce exactly as written."},
                     {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
