"""Glue between finished subtitles and reply suggestions.

Suggestions are made per *turn*, not per sentence. The segmenter cuts speech
at every 0.6 s pause, so one person talking for a while produces several
"sentences" (some only half a sentence, e.g. 「そうですね私」). Answering each
piece on its own gave replies that ignored what was being said. Instead,
finished sentences are collected in a ``TurnBuffer`` and sent together once the
speaker has been quiet for ``turn_gap`` seconds after the last one; while they
keep talking, the previous suggestion stays on screen.

A background thread asks the API and posts ("suggest", turn_id, payload) events
to the same queue the subtitle window reads, so a slow API never holds up the
next subtitle. Only the newest turn's suggestion is shown.

Latency is logged in three separate parts per turn (never summed into one):
  asr_ms     speech recognition decode time of the turn's last sentence
  api_ms     API round trip (request sent -> last token); first_token_ms too
  render_ms  suggestion event queued -> drawn in the window
plus turn_wait_ms (last subtitle -> request sent, the deliberate wait) and
shown_after_ms = last subtitle queued -> suggestion drawn, for the 3 s target.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .furigana import Furigana
from .suggest import Suggester, Suggestion

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


class TimingLog:
    """One JSON line per sentence in logs/live-YYYYmmdd-HHMMSS.jsonl (git-ignored)."""

    def __init__(self, path: Path | None = None) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        self.path = path or LOG_DIR / time.strftime("live-%Y%m%d-%H%M%S.jsonl")
        self.records: dict[int, dict] = {}
        self.lock = threading.Lock()

    def update(self, uid: int, **fields) -> None:
        with self.lock:
            self.records.setdefault(uid, {"utterance_id": uid}).update(fields)

    def finish(self, uid: int, **fields) -> dict:
        with self.lock:
            record = self.records.pop(uid, {"utterance_id": uid})
            record.update(fields)
            record.pop("subtitle_queued_at", None)  # perf_counter value, meaningless in a log
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(format_record(record), flush=True)
        return record


def format_record(r: dict) -> str:
    def ms(key):
        return f"{r[key]:.0f}" if isinstance(r.get(key), (int, float)) else "-"

    star = next((x["text"] for x in r.get("replies", []) if x.get("star")), "")
    return (f"[suggest #{r['utterance_id']}] asr {ms('asr_ms')} ms | api {ms('api_ms')} ms "
            f"(first token {ms('first_token_ms')}) | render {ms('render_ms')} ms | "
            f"shown after {ms('shown_after_ms')} ms | {r.get('status')}  ⭐ {star}")


class TurnBuffer:
    """Collects finished sentences until the speaker pauses long enough (pure logic, clock passed in)."""

    def __init__(self, turn_gap: float = 0.8, max_sentences: int = 6) -> None:
        self.turn_gap = turn_gap          # extra quiet time after the last sentence (it already ended with 0.6 s)
        self.max_sentences = max_sentences
        self.pending: list[dict] = []
        self.last_activity = 0.0  # last time speech stopped or a sentence arrived
        self.speaking = False

    def add(self, uid: int, text: str, asr_ms: float, now: float) -> None:
        self.pending.append({"uid": uid, "text": text, "asr_ms": asr_ms, "queued_at": now})
        self.last_activity = now

    def set_speaking(self, speaking: bool, now: float) -> None:
        # The pause is counted from when speech stopped, not from when the subtitle arrived:
        # a sentence is still being transcribed for ~0.25 s after the segmenter closes it,
        # and in that gap "not speaking" must not be mistaken for the end of the turn.
        if self.speaking and not speaking:
            self.last_activity = now
        self.speaking = speaking

    def ready(self, now: float) -> bool:
        return bool(self.pending) and not self.speaking and now - self.last_activity >= self.turn_gap

    def take(self) -> tuple[list[dict], list[dict]]:
        """(sentences of this turn, older overflow sentences that should become context)."""
        turn, self.pending = self.pending, []
        return turn[-self.max_sentences:], turn[:-self.max_sentences]


class SuggestionManager:
    def __init__(self, events: queue.Queue, suggester: Suggester, furigana: Furigana,
                 context_size: int = 4, turn_gap: float = 0.8, log: TimingLog | None = None) -> None:
        self.events = events
        self.suggester = suggester
        self.furigana = furigana
        self.context: deque[str] = deque(maxlen=context_size)  # sentences from earlier turns
        self.turns = TurnBuffer(turn_gap)
        self.lock = threading.Lock()
        self.log = log or TimingLog()
        self.latest = 0
        threading.Thread(target=self._tick, daemon=True).start()

    @property
    def idle(self) -> bool:
        return not self.turns.pending and not self.log.records

    def submit(self, uid: int, text: str, asr_ms: float) -> None:
        """A sentence was finished and shown as a subtitle; it joins the current turn."""
        text = text.strip()
        if text:
            with self.lock:
                self.turns.add(uid, text, asr_ms, time.perf_counter())

    def set_speaking(self, speaking: bool) -> None:
        """Called by the worker for every audio block: is someone talking right now?"""
        self.turns.set_speaking(speaking, time.perf_counter())

    def _tick(self) -> None:
        while True:
            time.sleep(0.05)
            with self.lock:
                if not self.turns.ready(time.perf_counter()):
                    continue
                turn, overflow = self.turns.take()
                self.context.extend(s["text"] for s in overflow)
                context = list(self.context)
                self.context.extend(s["text"] for s in turn)
            self._fire(turn, context)

    def _fire(self, turn: list[dict], context: list[str]) -> None:
        uid = turn[-1]["uid"]
        texts = [s["text"] for s in turn]
        now = time.perf_counter()
        self.latest = uid
        self.log.update(uid, text="".join(texts), sentences=texts, utterance_ids=[s["uid"] for s in turn],
                        context=context, partner=self.suggester.partner, asr_ms=round(turn[-1]["asr_ms"], 1),
                        turn_wait_ms=round((now - turn[-1]["queued_at"]) * 1000, 1),
                        subtitle_queued_at=turn[-1]["queued_at"])
        self.events.put(("suggest", uid, {"state": "pending"}))
        threading.Thread(target=self._run, args=(uid, texts, context), daemon=True).start()

    def _payload(self, state: str, s: Suggestion) -> dict:
        replies = [{
            "style": r.style, "star": r.star, "warn": r.warn, "meaning": r.meaning,
            "segments": self.furigana.annotate(r.text),
        } for r in s.replies]
        return {"state": state, "replies": replies, "reason": s.reason, "error": s.error,
                "queued_at": time.perf_counter()}

    def _run(self, uid: int, text: list[str], context: list[str]) -> None:
        def on_update(partial: Suggestion) -> None:
            if uid == self.latest:
                self.events.put(("suggest", uid, self._payload("streaming", partial)))

        s = self.suggester.suggest(text, context, on_update=on_update)
        state = "error" if s.error else "done"
        self.log.update(uid, api_ms=round(s.api_ms, 1),
                        first_token_ms=round(s.first_token_ms, 1) if s.first_token_ms else None,
                        replies=[asdict(r) for r in s.replies], reason=s.reason, raw=s.raw, error=s.error,
                        star_inferred=s.star_inferred)
        payload = self._payload(state, s)
        if uid == self.latest:
            self.events.put(("suggest", uid, payload))
        else:  # a newer turn was sent meanwhile; this one is never drawn
            self.log.finish(uid, status="superseded")

    def rendered(self, uid: int, payload: dict, timed_out: bool) -> None:
        """Called by the window right after a final suggestion has been drawn."""
        now = time.perf_counter()
        record = self.log.records.get(uid, {})
        shown_after = (now - record["subtitle_queued_at"]) * 1000 if "subtitle_queued_at" in record else None
        status = payload["state"] if not timed_out else f"late ({payload['state']})"
        self.log.finish(uid, render_ms=round((now - payload["queued_at"]) * 1000, 1),
                        shown_after_ms=round(shown_after, 1) if shown_after else None, status=status)
