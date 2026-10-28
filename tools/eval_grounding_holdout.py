"""Paired public holdout: committed baseline versus current suggestion code.

    python tools/eval_grounding_holdout.py

Eight cases, baseline then current for each case, plus one warm-up per side.
At most 18 requests, no retries, no automatic semantic correctness verdict.
The baseline code is read from Git without changing the worktree or branch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import types
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import Suggester, load_env  # noqa: E402
from tools.eval_grounding import (  # noqa: E402
    EvaluationError, TOKYO, file_hash, fingerprint, measure, safe_error, summarize, validate_client,
)

FIXTURE = Path("tests/fixtures/grounding_holdout.json")
BASELINE_REVISION = "2fa3014"
SUGGEST_SOURCE = "app/jsc_live/suggest.py"
SHARED_SOURCES = ("skill/SKILL.md", "skill/references/politeness.md")
VARIANTS = ("baseline", "current")
MAX_REQUESTS = 18


def load_fixture(root: Path = ROOT) -> tuple[dict, list[dict], dict]:
    path = root / FIXTURE
    raw = path.read_text(encoding="utf-8-sig")
    fixture = json.loads(raw)
    cases = fixture.get("cases", [])
    if [case.get("id") for case in cases] != list(range(1, 9)):
        raise EvaluationError("Holdout requires exactly cases 1 through 8 in order")
    for case in cases:
        profile = case.get("profile")
        if (not isinstance(profile, dict) or set(profile) != {"text"}
                or not isinstance(profile["text"], str)):
            raise EvaluationError("Holdout profiles must be explicit inline fictional text; no profile files are loaded")
        if (not isinstance(case.get("sentences"), list) or not case["sentences"]
                or not all(isinstance(text, str) for text in case["sentences"])
                or not isinstance(case.get("context"), list)
                or not all(isinstance(text, str) for text in case["context"])
                or (case.get("partner") is not None and not isinstance(case["partner"], str))
                or not isinstance(case.get("rubric"), list) or not case["rubric"]
                or not isinstance(case.get("expected_facts"), list)
                or not isinstance(case.get("expected_unknown_slots"), list)):
            raise EvaluationError("A holdout case has an invalid input or review shape")
    return fixture, cases, {"fixture_sha256": file_hash(path), "fixed_input_sha256": fingerprint(raw)}


def module_from_source(source: str, root: Path = ROOT) -> types.ModuleType:
    """Register before execution so dataclasses can resolve the module namespace."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    module_name = "_jsc_grounding_baseline_" + digest[:16]
    module = types.ModuleType(module_name)
    module.__file__ = str(root / SUGGEST_SOURCE)
    sys.modules[module_name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_baseline(root: Path = ROOT) -> tuple[types.ModuleType, str]:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=" + root.resolve().as_posix(), "show",
             BASELINE_REVISION + ":" + SUGGEST_SOURCE],
            cwd=root, check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise EvaluationError("Cannot read the fixed baseline source from Git; worktree was not changed") from None
    source_hash = hashlib.sha256(result.stdout).hexdigest()
    return module_from_source(result.stdout.decode("utf-8-sig"), root), source_hash


def run_evaluation(root: Path = ROOT) -> int:
    fixture, cases, provenance = load_fixture(root)
    baseline, baseline_hash = load_baseline(root)
    env = load_env()
    classes = {"baseline": baseline.Suggester, "current": Suggester}
    profiles = list(dict.fromkeys(["", *(case["profile"]["text"] for case in cases)]))
    clients = {}
    for variant in VARIANTS:
        for profile in profiles:
            # Both implementations receive the same raw input, then apply their own preprocessing.
            client = classes[variant](env=dict(env), profile=profile)
            validate_client(client)
            clients[variant, profile] = client
    started = datetime.now(TOKYO)
    payload = {
        "schema_version": 1, "started_at": started.isoformat(), "fixture": fixture, **provenance,
        "baseline_revision": BASELINE_REVISION,
        "source_sha256": {"baseline": baseline_hash, "current": file_hash(root / SUGGEST_SOURCE)},
        "shared_source_sha256": {name: file_hash(root / name) for name in SHARED_SOURCES},
        "evaluator_sha256": file_hash(Path(__file__)),
        "metric_helper_sha256": file_hash(ROOT / "tools/eval_grounding.py"),
        "model": "deepseek-flash", "thinking": "disabled", "temperature": 0.3, "max_tokens": 400,
        "planned_scored_requests": 16, "warmup_requests": 2, "request_limit": MAX_REQUESTS,
        "order": "Warm-up baseline/current, then cases 1-8 with baseline/current paired per case; no retries",
        "error_stop": "Three consecutive request errors globally or within either implementation",
        "latency_definition": "All attempted scored requests, including errors; median / nearest-rank p95",
        "review_instructions": [
            "Review every reply against its case rubric and authorized learner facts; brackets alone do not prove grounding.",
            "Distinguish current speech acts from unsupported existing personal facts and explicit attendance commitments.",
            "Check a yes/no prefix outside a blank: it may already affirm an unknown status.",
            "Keep grounding, correct known facts, handling of unknown fields and usefulness as separate judgments.",
        ],
        "prompt_sha256_by_variant": {
            variant: {str(case["id"]): fingerprint(clients[variant, case["profile"]["text"]].system_prompt)
                      for case in cases} for variant in VARIANTS
        },
        "warmup_error_kind": {}, "results": [],
    }
    output = root / "tests/live_suggest" / f"grounding-holdout-{started:%Y%m%d-%H%M%S-%f}.json"
    requests = 0
    consecutive_errors = 0
    errors_by_variant = {variant: 0 for variant in VARIANTS}
    aborted = False

    def reached_error_limit(variant: str, error: str | None) -> bool:
        nonlocal consecutive_errors
        failed = error not in (None, "malformed")
        consecutive_errors = consecutive_errors + 1 if failed else 0
        errors_by_variant[variant] = errors_by_variant[variant] + 1 if failed else 0
        return consecutive_errors >= 3 or errors_by_variant[variant] >= 3

    print(json.dumps({"planned_scored_requests": 16, "request_limit": MAX_REQUESTS,
                      "baseline_revision": BASELINE_REVISION}, ensure_ascii=False), flush=True)
    for variant in VARIANTS:
        result = clients[variant, ""].suggest("はい。", [])
        requests += 1
        error = safe_error(result.error)
        payload["warmup_error_kind"][variant] = error
        if reached_error_limit(variant, error):
            aborted = True
            break
    if not aborted:
        for case in cases:
            for variant in VARIANTS:
                if requests >= MAX_REQUESTS:
                    aborted = True
                    break
                client = clients[variant, case["profile"]["text"]]
                client.partner = case.get("partner")
                result = client.suggest(case["sentences"], case["context"])
                requests += 1
                row = {**case, **measure(result), "variant": variant, "rep": 1,
                       "effective_profile": client.profile, "raw": result.raw,
                       "reason": result.reason, "replies": [asdict(reply) for reply in result.replies]}
                payload["results"].append(row)
                print(json.dumps({"case": case["id"], "variant": variant,
                                  "requests_sent": requests}, ensure_ascii=False), flush=True)
                if reached_error_limit(variant, row["error_kind"]):
                    aborted = True
                    break
            if aborted:
                break
    payload["completed"] = not aborted
    payload["requests_sent"] = requests
    payload["finished_at"] = datetime.now(TOKYO).isoformat()
    payload["summary_by_variant"] = {
        variant: summarize([row for row in payload["results"] if row["variant"] == variant])
        for variant in VARIANTS
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": output.relative_to(root).as_posix(), "completed": not aborted,
                      "requests_sent": requests, "summary_by_variant": payload["summary_by_variant"]},
                     ensure_ascii=False), flush=True)
    return 2 if aborted else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run_evaluation()
    except EvaluationError as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Evaluation stopped during setup or output ({type(exc).__name__}); no error body printed.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
