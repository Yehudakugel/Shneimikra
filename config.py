# Single place to change the program's name (working name from a phone call – may be a mishearing of "Shnayim Mikra").
BRAND_NAME = "Schneimaker KP"
BRAND_TAGLINE = "Shnayim Mikra v'Echad Targum – the whole Torah, one aliyah a day"
BRAND_HE = "שנים מקרא ואחד תרגום"

# Hebrew pasuk audio: "pockettorah" = real chanted reading with trop from PocketTorah (CC BY-SA, static/audio_pt/),
# "tts" = our computer voice in Hungarian havara (static/audio/). Can also be set with the HEBREW_AUDIO env var.
import os as _os
HEBREW_AUDIO = _os.environ.get("HEBREW_AUDIO", "pockettorah")
AUDIO_CREDIT = {"text": "Torah audio: PocketTorah, CC BY-SA", "name": "PocketTorah", "license": "CC BY-SA",
                "url": "https://github.com/rneiss/PocketTorah", "license_url": "https://creativecommons.org/licenses/by-sa/3.0/"}
