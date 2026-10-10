# -*- coding: utf-8 -*-
"""
SAPI5 (classic Windows speech output) as a TTS engine.

Synthesizes via `SAPI.SpVoice` into an `SpMemoryStream` with a fixed format
(22 kHz, 16 bit, mono) and returns raw PCM, so it uses the same audio pipe as
Piper/Edge (pause, stop and speed behave the same).

Windows only (pywin32). Other platforms never import this module; the daemon
uses Piper there.
"""

import math

import pythoncom
import win32com.client as _w

import tts_langs

RATE = 22050          # SAFT22kHz16BitMono
CHANNELS = 1
_FMT = 22             # SpeechAudioFormatType.SAFT22kHz16BitMono

# Primary language ID (LCID & 0x3FF) per language code, for the automatic
# choice when no specific voice is configured.
_PRIMARY = tts_langs.LCID_PRIMARY


def _voice():
    pythoncom.CoInitialize()          # per thread; repeated calls are refcounted
    return _w.Dispatch("SAPI.SpVoice")


def list_voices():
    """Descriptions of all installed SAPI5 voices (for the tray's suggestions)."""
    try:
        return [t.GetDescription() for t in _voice().GetVoices()]
    except Exception:
        return []


def languages():
    """Language codes of the installed voices."""
    by_id = {v: k for k, v in _PRIMARY.items()}
    out = set()
    try:
        for t in _voice().GetVoices():
            code = by_id.get(_lang_primary(t.GetAttribute("Language")))
            if code:
                out.add(code)
    except Exception:
        pass
    return out


def _lang_primary(lcid_hex):
    try:
        return int(lcid_hex.split(";")[0], 16) & 0x3FF
    except Exception:
        return -1


def _select(v, name, key):
    """Pick a voice by name substring, else by language code, else None
    (system default voice)."""
    voices = list(v.GetVoices())
    if name:
        for t in voices:
            if name.lower() in t.GetDescription().lower():
                return t
    want = _PRIMARY.get(key)
    if want is not None:
        for t in voices:
            if _lang_primary(t.GetAttribute("Language")) == want:
                return t
    return None


def synth_pcm(text, voice_name, key, length_scale):
    """Synthesize text with SAPI5 -> (pcm_bytes, rate, channels)."""
    v = _voice()
    tok = _select(v, voice_name, key)
    if tok is not None:
        v.Voice = tok
    ls = length_scale or 1.0
    # Rate r speaks 3 ** (r / 10) times as fast (measured); ls < 1 = faster
    v.Rate = max(-10, min(10, round(10 * math.log(1.0 / ls) / math.log(3))))
    stream = _w.Dispatch("SAPI.SpMemoryStream")
    fmt = _w.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = _FMT
    stream.Format = fmt
    v.AudioOutputStream = stream
    v.Speak(text)                      # synchronous, into the memory stream
    return bytes(stream.GetData()), RATE, CHANNELS
