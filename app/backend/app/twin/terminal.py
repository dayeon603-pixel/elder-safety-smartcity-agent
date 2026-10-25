"""In-process pub/sub for the 현장 단말 (bus-stop display) channel: last-message replay, per-stop filtering, offline TTS.

Nothing leaves the process: subscribers are SSE connections of this server; `publish` only enqueues. Messages are
small typed dicts (kind, title, body, audio_url, stop_id, id, published_at). Audio for a message without
`audio_url` is synthesised with the existing 마을방송 pipeline (macOS `say -v Yuna` + ffmpeg) into settings.audio_dir
and served from /terminal/audio/<id>.wav; when TTS is unavailable the message goes out without audio.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agent import cap_audio
from . import constants as C

logger = logging.getLogger("twin.terminal")
Message = dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def audio_path_for(message_id: int) -> str:
    return f"/terminal/audio/{message_id}.wav"


def audio_file(audio_dir: Path, message_id: int) -> Path:
    return audio_dir / f"{C.TERMINAL_AUDIO_PREFIX}{message_id}.wav"


def script_for(title: str, body: str) -> str:
    """마을방송 script: title, first sentence of the body, drill notice; trimmed like cap_audio.script."""
    first = body.split(".")[0].strip()
    parts = [title.rstrip("."), first.rstrip(".")] if first else [title.rstrip(".")]
    text = ". ".join(parts) + ". " + cap_audio.CLOSING
    if len(text) <= cap_audio.MAX_SCRIPT_CHARS:
        return text
    short = title.rstrip(".") + ". " + cap_audio.CLOSING
    if len(short) <= cap_audio.MAX_SCRIPT_CHARS:
        return short
    return short[: cap_audio.MAX_SCRIPT_CHARS - len(cap_audio.CLOSING) - 2].rstrip() + ". " + cap_audio.CLOSING


@dataclass(slots=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[Message]
    stop_id: str | None


@dataclass(slots=True)
class TerminalHub:
    """Thread-safe broadcaster. `publish` may be called from worker threads; `subscribe` from the event loop."""

    history: deque[Message] = field(default_factory=lambda: deque(maxlen=C.TERMINAL_HISTORY))
    _subs: dict[int, _Subscriber] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _seq: int = 0
    _next_sub: int = 0

    def next_id(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def publish(self, message: Message) -> Message:
        """Record and fan out a message (id/published_at filled in when missing); returns the stored message."""
        msg: Message = {"kind": message["kind"], "title": message["title"], "body": message["body"],
                        "audio_url": message.get("audio_url"), "stop_id": message.get("stop_id"),
                        "id": message.get("id") or self.next_id(), "published_at": message.get("published_at") or now_iso(),
                        "drill": C.TERMINAL_DRILL_LABEL, **{k: v for k, v in message.items() if k == "meta"}}
        with self._lock:
            self.history.append(msg)
            subs = list(self._subs.values())
        for s in subs:
            if s.stop_id is not None and msg["stop_id"] is not None and s.stop_id != msg["stop_id"]:
                continue
            s.loop.call_soon_threadsafe(s.queue.put_nowait, msg)
        logger.info("terminal publish #%s kind=%s stop=%s subscribers=%d", msg["id"], msg["kind"], msg["stop_id"], len(subs))
        return msg

    def last_for(self, stop_id: str | None) -> Message | None:
        """Most recent message addressed to every stop or to this stop."""
        with self._lock:
            for msg in reversed(self.history):
                if msg["stop_id"] is None or stop_id is None or msg["stop_id"] == stop_id:
                    return msg
        return None

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def attach(self, stop_id: str | None) -> tuple[asyncio.Queue[Message], Callable[[], None]]:
        """Register a live subscriber on the running loop; returns its queue and a detach callback.

        Deliberately not an async generator: `asyncio.wait_for` on a generator's `__anext__` cancels the pending
        `queue.get()` inside it, which finalises the generator and kills the SSE stream at the first keep-alive.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Message] = asyncio.Queue()
        with self._lock:
            self._next_sub += 1
            key = self._next_sub
            self._subs[key] = _Subscriber(loop, queue, stop_id)

        def detach() -> None:
            with self._lock:
                self._subs.pop(key, None)

        return queue, detach


def synthesize_audio(audio_dir: Path, message_id: int, title: str, body: str) -> str | None:
    """WAV for a message (cached per id); None when TTS is not available on this host."""
    out = audio_file(audio_dir, message_id)
    if out.is_file():
        return audio_path_for(message_id)
    try:
        cap_audio.synthesize(script_for(title, body), out)
    except cap_audio.TtsUnavailable as exc:
        logger.warning("terminal audio #%d not synthesised: %s", message_id, exc)
        return None
    return audio_path_for(message_id)


def publish_message(hub: TerminalHub, audio_dir: Path, msg: Message, synthesize: bool) -> Message:
    """Assign an id, synthesise audio when wanted and possible (never for `clear`), then broadcast."""
    message_id = hub.next_id()
    audio = msg.get("audio_url")
    if audio is None and synthesize and msg["kind"] != "clear":
        audio = synthesize_audio(audio_dir, message_id, msg["title"], msg["body"])
    return hub.publish({**msg, "id": message_id, "audio_url": audio})


def message_from_run(rec: dict[str, Any]) -> Message | None:
    """Terminal message for a finished agent run: fall -> `fall`; heat_daily with a CAP -> `heat`; else None."""
    trigger = rec.get("trigger")
    cap = next((s.get("result") for s in rec.get("steps", []) if s.get("tool") == "make_cap_alert" and s.get("ok")), None)
    audio = cap.get("audio_url") if isinstance(cap, dict) else None
    briefing = str(rec.get("briefing") or "").strip()
    first = briefing.split("\n")[0][: C.TERMINAL_BODY_MAX]
    ctx = rec.get("context") or {}
    if trigger == "fall":
        title = "낙상 의심 사건"
        body = first or "근처에서 넘어진 분이 감지되어 관제요원이 확인 중입니다 (훈련). 주변 분들은 상태를 살피고 필요하면 119에 신고해 주세요."
        return {"kind": "fall", "title": title[: C.TERMINAL_TITLE_MAX], "body": body, "audio_url": audio,
                "stop_id": None, "meta": {"run_id": rec.get("run_id"), "code": ctx.get("code"), "date": ctx.get("date")}}
    if trigger == "heat_daily" and cap is not None:
        title = str(cap.get("headline") or "폭염 주의")[: C.TERMINAL_TITLE_MAX]
        body = first or "오늘은 폭염 위험이 높습니다. 한낮 외출을 피하고 물을 자주 드세요."
        return {"kind": "heat", "title": title, "body": body, "audio_url": audio, "stop_id": None,
                "meta": {"run_id": rec.get("run_id"), "code": ctx.get("code"), "date": ctx.get("date")}}
    return None


def heat_message(region_name: str, heat: dict[str, Any], weather: dict[str, Any], hour: int, *, has_shelters: bool = False,
                 aed_hint: str | None = None) -> Message | None:
    """Terminal message for the replay when the agent decides an alert for the day (None when it does not).

    Only facilities of the 구 are mentioned: no shelter sentence when the 구 has no shelter data; `aed_hint` (e.g.
    "가까운 열린 AED: 관악구청 1층 (도보 2분)") replaces the generic AED sentence when the caller resolved one.
    """
    if heat.get("ours_alert") is not True:
        return None
    feels = weather.get("feels")
    felt = f"체감 {feels:.1f}도. " if isinstance(feels, (int, float)) else ""
    b1 = "기상특보는 없지만 " if heat.get("b1_alert") is False else ""
    topic = "은" if region_name.endswith("군") else "는"
    where = "가까운 무더위쉼터와 열린 AED 위치를 화면에서 확인하세요." if has_shelters else "가까운 열린 AED 위치를 화면에서 확인하세요."
    if aed_hint:
        where = (f"가까운 무더위쉼터는 화면에서 확인하세요. {aed_hint}." if has_shelters else f"{aed_hint}.")
    body = f"{b1}{region_name}{topic} 오늘 온열질환 위험이 높습니다 (에이전트 권고·훈련). {felt}{hour:02d}시 현재 {where}"[: C.TERMINAL_BODY_MAX]
    return {"kind": "heat", "title": f"{region_name} 폭염 주의 · 에이전트 권고(훈련)"[: C.TERMINAL_TITLE_MAX], "body": body, "audio_url": None,
            "stop_id": None, "meta": {"date": heat.get("date"), "hour": hour, "rank_text": heat.get("rank_text"), "has_shelters": has_shelters}}


__all__ = ["Message", "TerminalHub", "audio_file", "audio_path_for", "heat_message", "message_from_run", "now_iso",
           "publish_message", "script_for", "synthesize_audio"]
