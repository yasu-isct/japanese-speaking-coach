"""Glue between finished subtitles and reply suggestions.

For every final sentence the worker calls ``SuggestionManager.submit``; a
background thread asks the API and posts ("suggest", utterance_id, payload)
events to the same queue the subtitle window reads, so a slow API never holds
up the next subtitle. Only the newest sentence's suggestion is shown.

Latency is logged in three separate parts per sentence (never summed into one):
  asr_ms     speech recognition decode time
  api_ms     API round trip (request sent -> last token); first_token_ms too
  render_ms  suggestion event queued -> drawn in the window
plus shown_after_ms = subtitle queued -> suggestion drawn, for the 3 s target.
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


class SuggestionManager:
    def __init__(self, events: queue.Queue, suggester: Suggester, furigana: Furigana,
                 context_size: int = 3, log: TimingLog | None = None) -> None:
        self.events = events
        self.suggester = suggester
        self.furigana = furigana
        self.context: deque[str] = deque(maxlen=context_size)
        self.log = log or TimingLog()
        self.latest = 0

    def submit(self, uid: int, text: str, asr_ms: float) -> None:
        text = text.strip()
        if not text:
            return
        context = list(self.context)
        self.context.append(text)
        self.latest = uid
        self.log.update(uid, text=text, context=context, partner=self.suggester.partner,
                        asr_ms=round(asr_ms, 1), subtitle_queued_at=time.perf_counter())
        self.events.put(("suggest", uid, {"state": "pending"}))
        threading.Thread(target=self._run, args=(uid, text, context), daemon=True).start()

    def _payload(self, state: str, s: Suggestion) -> dict:
        replies = [{
            "style": r.style, "star": r.star, "warn": r.warn, "meaning": r.meaning,
            "segments": self.furigana.annotate(r.text),
        } for r in s.replies]
        return {"state": state, "replies": replies, "reason": s.reason, "error": s.error,
                "queued_at": time.perf_counter()}

    def _run(self, uid: int, text: str, context: list[str]) -> None:
        def on_update(partial: Suggestion) -> None:
            if uid == self.latest:
                self.events.put(("suggest", uid, self._payload("streaming", partial)))

        s = self.suggester.suggest(text, context, on_update=on_update)
        state = "error" if s.error else "done"
        self.log.update(uid, api_ms=round(s.api_ms, 1),
                        first_token_ms=round(s.first_token_ms, 1) if s.first_token_ms else None,
                        replies=[asdict(r) for r in s.replies], reason=s.reason, raw=s.raw, error=s.error)
        payload = self._payload(state, s)
        if uid == self.latest:
            self.events.put(("suggest", uid, payload))
        else:  # a newer sentence arrived; this one is never drawn
            self.log.finish(uid, status="superseded")

    def rendered(self, uid: int, payload: dict, timed_out: bool) -> None:
        """Called by the window right after a final suggestion has been drawn."""
        now = time.perf_counter()
        record = self.log.records.get(uid, {})
        shown_after = (now - record["subtitle_queued_at"]) * 1000 if "subtitle_queued_at" in record else None
        status = payload["state"] if not timed_out else f"late ({payload['state']})"
        self.log.finish(uid, render_ms=round((now - payload["queued_at"]) * 1000, 1),
                        shown_after_ms=round(shown_after, 1) if shown_after else None, status=status)
