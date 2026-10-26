"""Fixed, text-only recommendation evaluation (no audio or private profile).

    python tools/eval_star.py --phase baseline
    python tools/eval_star.py --phase after

A: all 20 public samples, three passes, with their explicit partner labels.
B: the last 100 saved classroom turns, once each, including their context.
Only A's individual results are saved. B's aggregate metrics go to stdout;
redirect stdout to a file under ignored logs/ when retaining a local report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import STYLES, Suggester, load_env  # noqa: E402

PUBLIC_SOURCE = Path("tests/sample_dialogues.md")
PRIVATE_SOURCE = Path("logs/sessions/20261025-114606/suggestions.jsonl")
TOKYO = timezone(timedelta(hours=9))
MAX_REQUESTS = 161  # 60 public + 100 private + one warm-up; no retries.


class EvaluationError(Exception):
    """An intentionally non-sensitive setup error."""


def fingerprint(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_public_cases(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    section = text.split("## 测试句", 1)[1].split("## 附", 1)[0]
    cases = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not cells[0].isdigit() or cells[2] not in STYLES:
            continue
        sentence = cells[1]
        partner_match = re.match(r"^（对方是([^）]+)）", sentence)
        partner = partner_match.group(1) if partner_match else None
        if partner_match:
            sentence = sentence[partner_match.end():]
        cases.append({"id": int(cells[0]), "sentences": [sentence], "context": [],
                      "partner": partner, "expected": cells[2]})
    if [case["id"] for case in cases] != list(range(1, 21)):
        raise EvaluationError("A must contain exactly samples 1 through 20 in table order")
    if {case["id"] for case in cases if case["partner"]} != {6, 10, 16}:
        raise EvaluationError("A must pass the explicit partners for samples 6, 10 and 16")
    return cases


def load_private_cases(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(rows) < 100:
        raise EvaluationError("B requires at least 100 saved turns")
    cases = []
    for index, row in enumerate(rows[-100:], start=1):
        sentences, context, partner = row.get("sentences"), row.get("context"), row.get("partner")
        if (not isinstance(sentences, list) or not sentences
                or not all(isinstance(item, str) for item in sentences)
                or not isinstance(context, list) or not all(isinstance(item, str) for item in context)
                or (partner is not None and not isinstance(partner, str))):
            raise EvaluationError("B contains a turn with an invalid input shape; no filtering is allowed")
        cases.append({"id": index, "sentences": sentences, "context": context, "partner": partner})
    return cases


def safe_error(error: str | None) -> str | None:
    """Do not retain server error bodies, which may echo request text."""
    if not error:
        return None
    if error == "incomplete or malformed reply":
        return "malformed"
    match = re.match(r"HTTP (\d{3})\b", error)
    return "HTTP " + match.group(1) if match else "transport_or_protocol_error"


def measure(result, expected: str | None = None) -> dict:
    stars = [reply.style for reply in result.replies if reply.star]
    actual = stars[0] if len(stars) == 1 else None
    model_selected = result.error is None and not result.star_inferred and actual is not None
    return {
        "star_inferred": result.star_inferred,
        "exactly_one_model_star": model_selected,
        "actual": actual,
        "complete": result.complete,
        "error_kind": safe_error(result.error),
        "api_ms": result.api_ms,
        "first_token_ms": result.first_token_ms,
        "expected": expected,
        "matches_expected": actual == expected if expected is not None else None,
        "model_matches_expected": model_selected and actual == expected if expected is not None else None,
    }


def summarize(records: list[dict]) -> dict:
    n = len(records)
    times = sorted(row["api_ms"] for row in records if row["api_ms"] is not None)

    def ratio(key: str, value: object = True) -> dict:
        count = sum(row[key] == value for row in records)
        return {"count": count, "total": n, "rate": count / n if n else None}

    summary = {
        "n": n,
        "star_not_inferred": ratio("star_inferred", False),
        "exactly_one_model_star": ratio("exactly_one_model_star"),
        "complete": ratio("complete"),
        "errors": sum(row["error_kind"] is not None for row in records),
        "transport_or_protocol_errors": sum(row["error_kind"] not in (None, "malformed") for row in records),
        "malformed": sum(row["error_kind"] == "malformed" for row in records),
        "api_ms": {"n": len(times), "p50": statistics.median(times) if times else None,
                   "p95": times[math.ceil(0.95 * len(times)) - 1] if times else None},
    }
    if records and all(row["expected"] is not None for row in records):
        summary["matches_expected"] = ratio("matches_expected")
        summary["model_matches_expected"] = ratio("model_matches_expected")
    return summary


def run_evaluation(phase: str, group: str, root: Path = ROOT) -> int:
    public = load_public_cases(root / PUBLIC_SOURCE) if group in ("A", "all") else []
    private = load_private_cases(root / PRIVATE_SOURCE) if group in ("B", "all") else []
    suggester = Suggester(env=load_env(), profile="")
    effective_model = suggester.extra_body.get("model", suggester.model)
    if effective_model != "deepseek-flash" or suggester.extra_body.get("thinking") != {"type": "disabled"}:
        raise EvaluationError("Evaluation requires deepseek-flash with thinking disabled; settings were not changed")
    if any(key in suggester.extra_body for key in ("messages", "temperature", "max_tokens", "stream")):
        raise EvaluationError("LLM_EXTRA_BODY overrides a fixed evaluation setting; settings were not changed")
    started = datetime.now(TOKYO)
    payload = {
        "schema_version": 1, "phase": phase, "started_at": started.isoformat(),
        "model": effective_model, "thinking": "disabled", "temperature": 0.3, "max_tokens": 400,
        "profile": "explicitly empty; private profile.md is not loaded",
        "order": "A: passes 1-3, samples 1-20; then B: last 100 saved rows in file order; sequential",
        "warmup_requests": 1, "request_limit": MAX_REQUESTS,
        "latency_definition": "Suggester.api_ms; all attempted scored requests; p50 median, p95 nearest rank",
        "rate_definition": "all attempted scored requests, including errors; star_not_inferred is literal not star_inferred; exactly_one_model_star additionally requires exactly one parsed star and no request or format error",
        "evaluator_sha256": file_hash(Path(__file__)),
        "suggest_module_sha256": file_hash(root / "app/jsc_live/suggest.py"),
        "system_prompt_sha256": hashlib.sha256(suggester.system_prompt.encode("utf-8")).hexdigest(),
        "A_input_sha256": fingerprint(public) if public else None,
        "B_input_sha256": fingerprint(private) if private else None,
        "B_input_count": len(private),
        "A_results": [],
    }
    output = root / "tests/live_suggest" / f"star-eval-{started:%Y%m%d-%H%M%S-%f}.json"
    records = {"A": [], "B": []}
    requests = 0
    consecutive_errors = 0
    aborted = False

    print(json.dumps({"phase": phase, "group": group, "model": effective_model,
                      "A_planned": len(public) * 3, "B_planned": len(private),
                      "request_limit": MAX_REQUESTS}, ensure_ascii=False), flush=True)
    warmup = suggester.suggest("はい。", [])
    requests += 1
    warmup_error = safe_error(warmup.error)
    consecutive_errors = int(warmup_error not in (None, "malformed"))
    payload["warmup_error_kind"] = warmup_error
    schedule = [("A", rep, case) for rep in range(1, 4) for case in public]
    schedule += [("B", 1, case) for case in private]
    for name, rep, case in schedule:
        if requests >= MAX_REQUESTS:
            aborted = True
            break
        suggester.partner = case["partner"]
        result = suggester.suggest(case["sentences"], case["context"])
        requests += 1
        row = measure(result, case.get("expected"))
        records[name].append(row)
        if name == "A":
            payload["A_results"].append({**case, **row, "rep": rep,
                                         "raw": result.raw, "reason": result.reason,
                                         "replies": [asdict(reply) for reply in result.replies]})
        is_request_error = row["error_kind"] not in (None, "malformed")
        consecutive_errors = consecutive_errors + 1 if is_request_error else 0
        if len(records[name]) % 10 == 0 or consecutive_errors:
            print(json.dumps({"progress": name, "completed": len(records[name]),
                              "consecutive_request_errors": consecutive_errors}, ensure_ascii=False), flush=True)
        if consecutive_errors >= 3:
            aborted = True
            break
    payload["completed"] = not aborted
    payload["finished_at"] = datetime.now(TOKYO).isoformat()
    payload["requests_sent"] = requests
    payload["A_summary"] = summarize(records["A"])
    # B's only public metadata is its input hash/count; never serialize its records or metrics here.
    if public:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"A_output": output.relative_to(root).as_posix()}, ensure_ascii=False), flush=True)
    for name in ("A", "B"):
        if records[name]:
            print(json.dumps({"summary": name, **summarize(records[name])}, ensure_ascii=False), flush=True)
    print(json.dumps({"completed": not aborted, "requests_sent": requests}, ensure_ascii=False), flush=True)
    return 2 if aborted else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("baseline", "after"), required=True)
    parser.add_argument("--group", choices=("A", "B", "all"), default="all")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run_evaluation(args.phase, args.group)
    except EvaluationError as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
    except Exception as exc:
        # Private input and service responses must not appear in traceback messages.
        print(f"Evaluation stopped during setup or output ({type(exc).__name__}); no error body printed.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
