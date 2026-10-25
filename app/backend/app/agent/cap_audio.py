"""마을방송/스피커용 음성: a short plain-Korean script from a CAP alert, synthesised offline.

macOS `say -v Yuna` (Korean) writes AIFF; ffmpeg converts to 16 kHz mono PCM WAV. Both run as
subprocesses with timeouts; a missing binary or a failure raises TtsUnavailable (the route answers
503). Files are cached per alert identifier under settings.audio_dir.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SAY_BIN, FFMPEG_BIN = "say", "ffmpeg"
VOICE = "Yuna"
SAMPLE_RATE_HZ = 16000
CHANNELS = 1
MAX_SCRIPT_CHARS = 70  # Yuna reads ~4-5 Korean chars/s: 70 chars stays under the 20 s ceiling
MAX_SECONDS = 20.0
SUBPROCESS_TIMEOUT_S = 30.0
CLOSING = "훈련 방송입니다."
AUDIO_MEDIA_TYPE = "audio/wav"


class TtsUnavailable(RuntimeError):
    """`say`/ffmpeg missing, failed or timed out."""


def _first_sentence(text: str) -> str:
    return text.split(".")[0].strip()


def script(cap: dict[str, Any]) -> str:
    """Headline + first instruction sentence + a drill notice; trimmed to MAX_SCRIPT_CHARS at a sentence end."""
    info = cap["alert"].get("info") or {}
    if isinstance(info, list):
        info = info[0] if info else {}
    headline = str(info.get("headline") or info.get("event") or "생활안전 안내")
    instruction = _first_sentence(str(info.get("instruction") or ""))
    parts = [headline.rstrip("."), instruction.rstrip(".")] if instruction else [headline.rstrip(".")]
    text = ". ".join(parts) + ". " + CLOSING
    if len(text) <= MAX_SCRIPT_CHARS:
        return text
    short = headline.rstrip(".") + ". " + CLOSING
    return short if len(short) <= MAX_SCRIPT_CHARS else short[: MAX_SCRIPT_CHARS - len(CLOSING) - 2].rstrip() + ". " + CLOSING


def available() -> bool:
    return shutil.which(SAY_BIN) is not None and shutil.which(FFMPEG_BIN) is not None


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=SUBPROCESS_TIMEOUT_S)  # noqa: S603 - fixed argv
    except FileNotFoundError as exc:
        raise TtsUnavailable(f"{cmd[0]} not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise TtsUnavailable(f"{cmd[0]} timed out after {SUBPROCESS_TIMEOUT_S:.0f}s") from exc
    except subprocess.CalledProcessError as exc:
        raise TtsUnavailable(f"{cmd[0]} failed ({exc.returncode})") from exc


def wav_seconds(path: Path) -> float:
    """Duration of a PCM WAV from its header."""
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes() / float(fh.getframerate())


def synthesize(text: str, out_wav: Path) -> float:
    """Write `text` as 16 kHz mono WAV at `out_wav`; returns the duration in seconds.

    Raises:
        TtsUnavailable: when `say` or ffmpeg is missing, fails, or exceeds the timeout.
    """
    if not available():
        raise TtsUnavailable("say/ffmpeg not available on this host")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    aiff = out_wav.with_suffix(".aiff")
    tmp = out_wav.with_suffix(".tmp.wav")
    try:
        _run([SAY_BIN, "-v", VOICE, "-o", str(aiff), text])
        _run([FFMPEG_BIN, "-y", "-loglevel", "error", "-i", str(aiff), "-ar", str(SAMPLE_RATE_HZ), "-ac", str(CHANNELS),
              "-acodec", "pcm_s16le", str(tmp)])
        tmp.replace(out_wav)
    finally:
        aiff.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    seconds = wav_seconds(out_wav)
    if seconds > MAX_SECONDS:
        logger.warning("audio for %s is %.1fs (> %.0fs)", out_wav.name, seconds, MAX_SECONDS)
    return seconds


__all__ = ["AUDIO_MEDIA_TYPE", "MAX_SCRIPT_CHARS", "MAX_SECONDS", "TtsUnavailable", "available", "script",
           "synthesize", "wav_seconds"]
