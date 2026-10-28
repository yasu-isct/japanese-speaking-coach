"""Fixed public, text-only checks for personal facts; semantic review is manual.

    python tools/eval_grounding.py --phase baseline
    python tools/eval_grounding.py --phase after

Sixteen fictional cases, twice each, plus one warm-up (33 requests maximum).
Only the two allowlisted public profile fixtures may be loaded. A placeholder
count is a diagnostic, never evidence that all personal claims are grounded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import Suggester, load_env, load_profile  # noqa: E402

FIXTURE = Path("tests/fixtures/grounding_cases.json")
PUBLIC_PROFILES = frozenset(("profile.example.md", "tests/live_suggest/test_profile.md"))
SOURCE_FILES = ("app/jsc_live/suggest.py", "skill/SKILL.md", "skill/references/politeness.md")
REPETITIONS = 2
MAX_REQUESTS = 33
TOKYO = timezone(timedelta(hours=9))


class EvaluationError(Exception):
    """A deliberately non-sensitive setup error."""


def fingerprint(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(root: Path = ROOT) -> tuple[dict, list[dict], dict]:
    """Keep raw inputs fixed even when production profile preprocessing changes."""
    path = root / FIXTURE
    raw_fixture = path.read_text(encoding="utf-8-sig")
    fixture = json.loads(raw_fixture)
    original_cases = fixture.get("cases", [])
    if [case.get("id") for case in original_cases] != list(range(1, 17)):
        raise EvaluationError("Fixture must contain exactly cases 1 through 16 in order")
    public_sources: dict[str, dict] = {}
    cases = []
    for original in original_cases:
        case = dict(original)
        if (not isinstance(case.get("sentences"), list) or not case["sentences"]
                or not all(isinstance(text, str) for text in case["sentences"])
                or not isinstance(case.get("context"), list)
                or not all(isinstance(text, str) for text in case["context"])
                or (case.get("partner") is not None and not isinstance(case["partner"], str))
                or not isinstance(case.get("rubric"), list) or not case["rubric"]
                or not isinstance(case.get("expected_facts"), list)
                or not isinstance(case.get("expected_unknown_slots"), list)):
            raise EvaluationError("A fixture case has an invalid input or review shape")
        profile = case.get("profile")
        if not isinstance(profile, dict) or set(profile) not in ({"text"}, {"path"}):
            raise EvaluationError("Each case requires one explicit inline or public-file profile")
        if "path" in profile:
            name = profile["path"]
            if not isinstance(name, str) or name not in PUBLIC_PROFILES:
                raise EvaluationError("Only allowlisted public profile fixtures may be loaded")
            source_path = root / name
            if source_path.resolve() != root.resolve().joinpath(name):
                raise EvaluationError("Public profile fixture must not resolve outside its expected path")
            if name not in public_sources:
                public_sources[name] = {"sha256": file_hash(source_path),
                                        "raw_text": source_path.read_text(encoding="utf-8-sig")}
            case["profile_text"] = load_profile(source_path)
        elif isinstance(profile["text"], str):
            case["profile_text"] = profile["text"]
        else:
            raise EvaluationError("Inline profile text must be a string")
        cases.append(case)
    provenance = {
        "fixture_sha256": file_hash(path),
        "fixed_input_sha256": fingerprint({"raw_fixture": raw_fixture, "profile_sources": public_sources}),
        "profile_sources": public_sources,
    }
    return fixture, cases, provenance


def safe_error(error: str | None) -> str | None:
    if not error:
        return None
    if error == "incomplete or malformed reply":
        return "malformed"
    # Neither provider response bodies nor exception messages are retained.
    if error.startswith("HTTP ") and error[5:8].isdigit():
        return "HTTP " + error[5:8]
    return "transport_or_protocol_error"


def measure(result) -> dict:
    return {
        "complete": result.complete,
        "error_kind": safe_error(result.error),
        "api_ms": result.api_ms,
        "first_token_ms": result.first_token_ms,
        "star_inferred": result.star_inferred,
        "reply_count": len(result.replies),
        "replies_with_placeholders": sum("【" in reply.text for reply in result.replies),
        "review_status": "pending",
        "grounding_verdict": None,
        "review": {"unsupported_personal_claims": None, "wrong_owner_claims": None,
                   "known_facts_correct": None, "unknown_slots_handled": None,
                   "directly_useful": None, "notes": ""},
    }


def summarize(records: list[dict]) -> dict:
    times = sorted(row["api_ms"] for row in records if row["api_ms"] is not None)
    return {
        "n": len(records),
        "complete": {"count": sum(row["complete"] for row in records), "total": len(records)},
        "errors": sum(row["error_kind"] is not None for row in records),
        "transport_or_protocol_errors": sum(row["error_kind"] not in (None, "malformed") for row in records),
        "api_ms": {"n": len(times), "p50": statistics.median(times) if times else None,
                   "p95": times[math.ceil(0.95 * len(times)) - 1] if times else None},
        "placeholder_diagnostic": {"replies_with_placeholders": sum(row["replies_with_placeholders"] for row in records),
                                   "reply_count": sum(row["reply_count"] for row in records),
                                   "meaning": "Presence of brackets does not establish grounding or usefulness"},
        "review_status": "pending",
        "grounding_verdict": None,
    }


def validate_client(client) -> None:
    effective_model = client.extra_body.get("model", client.model)
    if effective_model != "deepseek-flash" or client.extra_body.get("thinking") != {"type": "disabled"}:
        raise EvaluationError("Evaluation requires deepseek-flash with thinking disabled; settings were not changed")
    if any(key in client.extra_body for key in ("messages", "temperature", "max_tokens", "stream")):
        raise EvaluationError("LLM_EXTRA_BODY overrides a fixed evaluation setting; settings were not changed")


def run_evaluation(phase: str, root: Path = ROOT) -> int:
    if phase not in ("baseline", "after"):
        raise EvaluationError("Phase must be baseline or after")
    fixture, cases, provenance = load_fixture(root)
    env = load_env()
    clients = {}
    for profile in dict.fromkeys(["", *(case["profile_text"] for case in cases)]):
        client = Suggester(env=env, profile=profile)
        validate_client(client)
        clients[profile] = client
    started = datetime.now(TOKYO)
    payload = {
        "schema_version": 1, "phase": phase, "started_at": started.isoformat(),
        "model": "deepseek-flash", "thinking": "disabled", "temperature": 0.3, "max_tokens": 400,
        "order": "Two passes of cases 1 through 16 in fixture order; sequential; no retries",
        "planned_scored_requests": len(cases) * REPETITIONS, "warmup_requests": 1,
        "request_limit": MAX_REQUESTS, "fixture": fixture, **provenance,
        "evaluator_sha256": file_hash(Path(__file__)),
        "source_sha256": {name: file_hash(root / name) for name in SOURCE_FILES},
        "prompt_sha256_by_case": {str(case["id"]): fingerprint(clients[case["profile_text"]].system_prompt)
                                   for case in cases},
        "latency_definition": "Suggester.api_ms; all attempted scored requests including errors; median / nearest-rank p95",
        "review_instructions": [
            "Inspect every reply's personal claims outside placeholders against the authorized learner facts.",
            "Do not transfer the partner's, another person's, an example's or a quoted sentence's facts to the learner.",
            "Missing data is not evidence of a negative fact or evidence that the learner does not know their own life.",
            "Check relevance, useful fillable slots, correct known facts, attribution and ordinary conversation quality.",
            "A reply containing brackets can still invent another fact; no semantic verdict is calculated automatically.",
        ],
        "results": [],
    }
    output = root / "tests/live_suggest" / f"grounding-eval-{started:%Y%m%d-%H%M%S-%f}.json"
    print(json.dumps({"phase": phase, "planned_scored_requests": 32, "request_limit": MAX_REQUESTS},
                     ensure_ascii=False), flush=True)
    warmup = clients[""].suggest("はい。", [])
    requests = 1
    warmup_error = safe_error(warmup.error)
    payload["warmup_error_kind"] = warmup_error
    consecutive_errors = int(warmup_error not in (None, "malformed"))
    aborted = False
    for rep in range(1, REPETITIONS + 1):
        for case in cases:
            if requests >= MAX_REQUESTS:
                aborted = True
                break
            client = clients[case["profile_text"]]
            client.partner = case.get("partner")
            result = client.suggest(case["sentences"], case["context"])
            requests += 1
            row = {key: value for key, value in case.items() if key != "profile_text"}
            row.update(measure(result))
            row.update({"rep": rep, "effective_profile": client.profile, "raw": result.raw,
                        "reason": result.reason, "replies": [asdict(reply) for reply in result.replies]})
            payload["results"].append(row)
            request_error = row["error_kind"] not in (None, "malformed")
            consecutive_errors = consecutive_errors + 1 if request_error else 0
            print(json.dumps({"completed": len(payload["results"]), "case": case["id"], "rep": rep,
                              "consecutive_request_errors": consecutive_errors}, ensure_ascii=False), flush=True)
            if consecutive_errors >= 3:
                aborted = True
                break
        if aborted:
            break
    payload["completed"] = not aborted
    payload["requests_sent"] = requests
    payload["finished_at"] = datetime.now(TOKYO).isoformat()
    payload["summary"] = summarize(payload["results"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": output.relative_to(root).as_posix(), "completed": not aborted,
                      "requests_sent": requests, "summary": payload["summary"]}, ensure_ascii=False), flush=True)
    return 2 if aborted else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("baseline", "after"), required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run_evaluation(args.phase)
    except EvaluationError as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Evaluation stopped during setup or output ({type(exc).__name__}); no error body printed.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
