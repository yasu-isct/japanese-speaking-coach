"""Small public document-answering integration check, not a model accuracy benchmark.

Six frozen questions spanning course, meeting, manual, article and missing data.
Pairs the old no-document app with current retrieval, at most 14 API requests.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.materials import MaterialsStore
from jsc_live.suggest import Suggester, load_env
from tools.eval_grounding import TOKYO, file_hash, safe_error, validate_client
from tools.eval_grounding_holdout import load_baseline
from tools.eval_materials import load_fixture


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    fixture_root = ROOT / "tests/fixtures/materials/general"
    manifest, fixture, _ = load_fixture(fixture_root, minimum_cases=10)
    selected = [case for case in fixture["cases"] if case["id"] in (1, 3, 4, 5, 7, 9)]
    baseline, baseline_hash = load_baseline()
    env = load_env()
    clients = {
        "no_documents_baseline": baseline.Suggester(env=env, profile=""),
        "retrieval": Suggester(env=env, materials=MaterialsStore(fixture_root / "data")),
    }
    for client in clients.values():
        validate_client(client)
    now = datetime.now(TOKYO)
    payload = {
        "purpose": "Six-case public integration demonstration; no semantic accuracy score",
        "started_at": now.isoformat(), "fixture_manifest": manifest,
        "selected_case_ids": [case["id"] for case in selected],
        "baseline_revision": "2fa3014", "baseline_source_sha256": baseline_hash,
        "source_sha256": {name: file_hash(ROOT / name) for name in (
            "app/jsc_live/suggest.py", "app/jsc_live/materials.py", "tools/eval_reference_answers.py")},
        "max_requests": 14, "results": [], "completed": False,
    }
    errors = 0
    sent = 0
    for variant, client in clients.items():
        result = client.suggest("はい。", [])
        sent += 1
        errors = errors + 1 if safe_error(result.error) not in (None, "malformed") else 0
    for case in selected:
        for variant, client in clients.items():
            result = client.suggest(case["query"], [])
            sent += 1
            error = safe_error(result.error)
            errors = errors + 1 if error not in (None, "malformed") else 0
            sources = getattr(result, "sources", [])
            cited = getattr(result, "cited_source_ids", [])
            payload["results"].append({
                "case": case, "variant": variant, "raw": result.raw,
                "replies": [asdict(reply) for reply in result.replies], "reason": result.reason,
                "complete": result.complete, "error_kind": error, "api_ms": result.api_ms,
                "sources": sources, "model_declared_citations": cited,
                "retrieval_ms": getattr(result, "retrieval_ms", None),
            })
            print(json.dumps({"case": case["id"], "variant": variant, "complete": result.complete,
                              "hits": len(sources), "citations": len(cited), "error_kind": error}), flush=True)
            if errors >= 3:
                break
        if errors >= 3:
            break
    payload["requests_sent"] = sent
    payload["completed"] = sent == 14
    output = ROOT / "tests/live_suggest" / f"references-eval-{now:%Y%m%d-%H%M%S-%f}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT).as_posix(), flush=True)
    return 0 if payload["completed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Reference check failed ({type(exc).__name__}); no error body printed.", file=sys.stderr)
        raise SystemExit(2)
