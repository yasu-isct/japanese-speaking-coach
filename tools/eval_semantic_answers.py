"""Explicit 12-request comparison on frozen, fictional general documents only."""
from pathlib import Path
from datetime import datetime
from dataclasses import asdict
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app")]
from jsc_live.materials import MaterialsStore
from jsc_live.semantic_materials import SemanticEncoder, SemanticMaterialsStore
from jsc_live.suggest import Suggester, load_env
from tools.eval_grounding import validate_client, safe_error
from tools.eval_materials import load_fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-api", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--candidate-only", action="store_true", help="six candidate requests; reuse the saved baseline")
    args = parser.parse_args(argv)
    if not args.run_api:
        print("No requests sent. Use --run-api and an explicit --model-dir after checking the daily budget.")
        return 0
    if args.model_dir is None:
        parser.error("--run-api requires --model-dir")
    folder = ROOT / "tests/fixtures/materials/general-independent"
    load_fixture(folder)
    case_file = folder / "answer-cases.json"
    manifest = json.loads((folder / "answer-manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(case_file.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Frozen stage questions changed")
    cases = json.loads(case_file.read_text(encoding="utf-8"))["cases"]
    if len(cases) != 6:
        raise ValueError("Stage must contain exactly six questions")
    encoder = SemanticEncoder(args.model_dir)
    env = load_env()
    clients = {"keywords": Suggester(env=env, materials=MaterialsStore(folder / "data")),
               "multilingual": Suggester(env=env, materials=SemanticMaterialsStore(folder / "data", encoder=encoder))}
    if args.candidate_only:
        clients.pop("keywords")
    for client in clients.values():
        validate_client(client)
    result = {"purpose": "Small general-document stage, not a general accuracy estimate", "fixture_manifest": manifest,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                ("app/jsc_live/suggest.py", "app/jsc_live/semantic_materials.py")},
              "private_data_read": False, "maximum_requests": len(cases) * len(clients), "results": [], "completed": False}
    out = ROOT / "tests/live_suggest" / ("semantic-answers-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    for case in cases:
        for variant, client in clients.items():
            client.prefetch(case["query"])
            answer = client.suggest(case["query"], [])
            result["results"].append({"case": case, "variant": variant, "raw": answer.raw,
                                      "replies": [asdict(reply) for reply in answer.replies], "reason": answer.reason,
                                      "complete": answer.complete, "error_kind": safe_error(answer.error),
                                      "api_ms": answer.api_ms, "retrieval_ms": answer.retrieval_ms,
                                      "retrieval_cached": answer.retrieval_cached,
                                      "sources": answer.sources, "cited_source_ids": answer.cited_source_ids})
            print(json.dumps({"case": case["id"], "variant": variant, "complete": answer.complete,
                              "hits": len(answer.sources), "cites": len(answer.cited_source_ids)}, ensure_ascii=False), flush=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if sum(bool(row["error_kind"]) for row in result["results"]) >= 2:
                print("Stopped after two failed responses; partial results retained.")
                return 2
    result["completed"] = len(result["results"]) == result["maximum_requests"]
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out))
    return 0 if result["completed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Stage check failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
