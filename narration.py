"""Local narration engine (Windows Speech -> WAV, no network, no cost).

The OS reads each scene script into a WAV file that Saath muxes into
the lesson. Availability is proven by execution: no voices means no
narration, reported honestly, never faked.
"""

import os
import subprocess
import tempfile

_PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "narration.ps1")
MAX_NARRATION_CHARS = 2000


class NarrationError(Exception):
    """Local speech synthesis failed."""


def available_voices() -> list:
    """Installed voices as [{"name":..., "culture":...}]. Empty when the
    speech engine is missing — never raises."""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _PS1, "-ListVoices"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    voices = []
    for line in (completed.stdout or "").splitlines():
        name, _, culture = line.strip().partition("|")
        if name:
            voices.append({"name": name, "culture": culture})
    return voices


def default_voice() -> str:
    """First English voice, else first voice, else ""."""
    voices = available_voices()
    for voice in voices:
        if voice["culture"].lower().startswith("en"):
            return voice["name"]
    return voices[0]["name"] if voices else ""


def voice_for(language: str) -> str:
    """First voice matching the lesson language ("hi" needs a Hindi
    voice — an English voice reading Devanagari would be garbage, so
    no match means "" and the caller skips honestly)."""
    if (language or "en").strip().lower() != "hi":
        return default_voice()
    for voice in available_voices():
        culture = voice["culture"].lower()
        if culture.startswith("hi"):
            return voice["name"]
    return ""


def speak_to_wav(text: str, path: str, *, voice: str = "", rate: int = -1) -> dict:
    """Narrate text into a WAV file. Returns {"voice","chars"}."""
    clean = (text or "").strip()
    if not clean:
        raise NarrationError("nothing to narrate")
    if len(clean) > MAX_NARRATION_CHARS:
        raise NarrationError("narration text is too long")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(clean)
        text_file = handle.name
    try:
        args = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", _PS1,
            "-TextFile", text_file, "-OutFile", path, "-Rate", str(rate),
        ]
        if voice:
            args += ["-Voice", voice]
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            raise NarrationError("speech synthesis timed out")
        if completed.returncode != 0:
            raise NarrationError((completed.stderr or "speech synthesis failed").strip()[-500:])
    finally:
        try:
            os.remove(text_file)
        except OSError:
            pass
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise NarrationError("speech engine produced no audio")
    return {"voice": voice or default_voice(), "chars": len(clean)}
