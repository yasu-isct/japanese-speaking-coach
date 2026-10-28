"""Evaluate a frozen public materials fixture locally; never call a model.

Run from any directory with ``python tools/eval_materials.py`` for the general
document fixture. Use ``--fixture profile-regression-v2`` for the separate
legacy-profile regression fixture. This compares the old material-sending
strategy with the current default BM25 snapshot. It evaluates
retrieval, payload size, and local timing, not generated answers or correctness.
The fixture manifest must already exist; this script never rewrites it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import stat
import statistics
import sys
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.materials import MaterialsStore  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "materials"
OUTPUT = ROOT / "tests" / "live_suggest"
MODULE = ROOT / "app" / "jsc_live" / "materials.py"
TOKYO = timezone(timedelta(hours=9))
TIMING_REPETITIONS = 20


class EvaluationError(Exception):
    """A setup failure whose message does not contain any file contents."""


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_path(base: Path, name: str) -> Path:
    """Reject traversal and links before reading the allowlisted fixture."""
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or ".." in relative.parts
            or "\\" in name or ":" in name):
        raise EvaluationError("Invalid relative fixture path")
    current = base
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise EvaluationError("Fixture paths must not contain links or junctions")
    if not current.resolve().is_relative_to(base.resolve()):
        raise EvaluationError("Fixture path escaped the fixture directory")
    return current


def load_fixture(fixture_root: Path = FIXTURE / "general", minimum_cases: int = 10) -> tuple[dict, dict, dict[str, str]]:
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    expected_hashes = manifest.get("sha256", {})
    actual_names = {"queries.json"} | {
        path.relative_to(fixture_root).as_posix() for path in (fixture_root / "data").rglob("*") if path.is_file()
    }
    if set(expected_hashes) != actual_names:
        raise EvaluationError("Frozen fixture file set changed")
    texts = {}
    for name, expected in expected_hashes.items():
        path = public_path(fixture_root, name)
        if file_hash(path) != expected:
            raise EvaluationError("Frozen fixture hash mismatch; create a new fixture version")
        if name.startswith("data/"):
            texts[name.removeprefix("data/")] = path.read_text(encoding="utf-8")
    fixture = json.loads(public_path(fixture_root, "queries.json").read_text(encoding="utf-8"))
    if fixture.get("baseline_profile") not in texts:
        raise EvaluationError("The baseline profile is not a frozen public source")
    cases = fixture.get("cases", [])
    if len(cases) < minimum_cases or [case.get("id") for case in cases] != list(range(1, len(cases) + 1)):
        raise EvaluationError("Fixture does not have the required consecutively numbered cases")
    for case in cases:
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise EvaluationError("A fixture query is missing")
        if not isinstance(case.get("relevant"), list) or not isinstance(case.get("forbidden"), list):
            raise EvaluationError("A fixture case needs relevant and forbidden range lists")
        if case.get("forbid_all_sources") and case["relevant"]:
            raise EvaluationError("An empty-result case cannot also require relevant sources")
        for annotation in case["relevant"] + case["forbidden"]:
            name, start, end = annotation.get("path"), annotation.get("start"), annotation.get("end")
            if (name not in texts or not isinstance(start, int) or not isinstance(end, int)
                    or not 1 <= start <= end <= len(texts[name].splitlines())):
                raise EvaluationError("A fixture source range is invalid")
    return manifest, fixture, texts


def range_covered(expected: dict, sources: list[dict]) -> bool:
    covered = set()
    for source in sources:
        if source["relative_path"] == expected["path"]:
            covered.update(range(source["start_line"], source["end_line"] + 1))
    return set(range(expected["start"], expected["end"] + 1)).issubset(covered)


def judge(case: dict, sources: list[dict], payload: str) -> dict:
    coverage = [{**expected, "covered": range_covered(expected, sources)} for expected in case["relevant"]]
    violations = []
    for forbidden in case["forbidden"]:
        overlaps = [source["source_id"] for source in sources
                    if source["relative_path"] == forbidden["path"]
                    and source["start_line"] <= forbidden["end"]
                    and source["end_line"] >= forbidden["start"]]
        if overlaps:
            violations.append({"rule": forbidden, "source_ids": overlaps})
    if case.get("forbid_all_sources") and sources:
        violations.append({"rule": "no source is relevant to this question",
                           "source_ids": [source["source_id"] for source in sources]})
    failures = [f"Missing annotated range {item['path']}:{item['start']}-{item['end']}"
                for item in coverage if not item["covered"]]
    failures.extend("Sent forbidden annotated range " + (
        f"{item['rule']['path']}:{item['rule']['start']}-{item['rule']['end']}"
        if isinstance(item["rule"], dict) else item["rule"]) for item in violations)
    return {
        "source_ids": [source["source_id"] for source in sources],
        "sources": sources,
        "payload": payload,
        "relevant_ranges": coverage,
        "relevant_ranges_covered": sum(item["covered"] for item in coverage),
        "relevant_ranges_expected": len(coverage),
        "all_relevant_ranges_covered": all(item["covered"] for item in coverage) if coverage else None,
        "forbidden_rule_applicable": bool(case["forbidden"] or case.get("forbid_all_sources")),
        "forbidden_violations": violations,
        "failure_reasons": failures,
        "forbidden_free": not violations,
        "empty_sources": not sources,
        "body_chars": sum(len(source["text"]) for source in sources),
        "payload_chars": len(payload),
        "payload_utf8_bytes": len(payload.encode("utf-8")),
    }


def timing_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"samples": len(values), "p50_ms": statistics.median(ordered),
            "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1]}


def summarize(cases: list[dict], condition: str) -> dict:
    outputs = [case[condition] for case in cases]
    covered = sum(result["relevant_ranges_covered"] for result in outputs)
    expected = sum(result["relevant_ranges_expected"] for result in outputs)
    relevant_cases = [result for result in outputs if result["relevant_ranges_expected"]]
    restricted_cases = [result for result in outputs if result["forbidden_rule_applicable"]]
    no_data = [case[condition] for case in cases if case["annotation"].get("forbid_all_sources")]
    retrieval = [value for result in outputs for value in result["retrieval_ms_samples"]]
    preparation = [value for result in outputs for value in result["preparation_ms_samples"]]
    total_body = sum(result["body_chars"] for result in outputs)
    total_payload = sum(result["payload_chars"] for result in outputs)
    return {
        "cases": len(outputs),
        "relevant_range_recall": {"covered": covered, "expected": expected, "rate": covered / expected},
        "complete_recall_cases": {"passed": sum(result["all_relevant_ranges_covered"] for result in relevant_cases),
                                  "applicable": len(relevant_cases)},
        "forbidden_free_cases": {"passed": sum(result["forbidden_free"] for result in restricted_cases),
                                 "applicable": len(restricted_cases)},
        "empty_for_no_data_cases": {"passed": sum(result["empty_sources"] for result in no_data),
                                    "applicable": len(no_data)},
        "body_chars": {"total": total_body, "mean_per_question": total_body / len(outputs)},
        "payload_chars": {"total": total_payload, "mean_per_question": total_payload / len(outputs)},
        "payload_utf8_bytes_total": sum(result["payload_utf8_bytes"] for result in outputs),
        "local_retrieval": timing_summary(retrieval),
        "local_payload_preparation": timing_summary(preparation),
    }


def safety_diagnostics() -> dict:
    """Only synthetic temporary files; no symlink privileges or external data."""
    with tempfile.TemporaryDirectory(prefix="materials-eval-") as directory:
        temp = Path(directory)
        empty_root = temp / "empty-materials"
        empty_root.mkdir()
        store = MaterialsStore(empty_root)
        empty_ok = not store.chunks and not store.search("仕事") and json.loads(store.prompt_data([]))["sources"] == []
        outside = temp / "outside-fictional.txt"
        outside.write_text("PUBLIC_SYNTHETIC_OUTSIDE_SENTINEL", encoding="utf-8")
        counts = Counter()
        rejected = store._read(outside, empty_root.resolve(), counts) is None
        path_ok = rejected and counts["unsafe_path"] == 1
        return {
            "empty_library": {"passed": empty_ok, "diagnostics": dict(store.diagnostics)},
            "path_escape": {"passed": path_ok, "diagnostics": dict(counts),
                            "method": "Direct read guard given a synthetic file outside its allowed root; no links created",
                            "limitation": "This is not an OS-level junction race or sandbox security proof"},
        }


def run(suite: str = "general", phase: str = "current") -> Path:
    started = datetime.now(TOKYO)
    if suite not in ("general", "profile-regression-v2"):
        raise EvaluationError("Only the complete public general and profile-regression-v2 fixtures are supported")
    fixture_root = FIXTURE / suite
    is_profile = suite == "profile-regression-v2"
    minimum_cases = 12 if is_profile else 10
    manifest, fixture, texts = load_fixture(fixture_root, minimum_cases)
    hashes = {"manifest_sha256": file_hash(fixture_root / "manifest.json"),
              "evaluator_sha256": file_hash(Path(__file__)), "materials_sha256": file_hash(MODULE)}
    before = perf_counter_ns()
    store = MaterialsStore(fixture_root / "data")
    index_build_ms = (perf_counter_ns() - before) / 1_000_000
    if store.diagnostics.get("indexed_files") != len(texts):
        raise EvaluationError("The public fixture was not completely indexed")
    profile_name = fixture["baseline_profile"]
    profile = texts[profile_name] if is_profile else ""
    baseline_sources = [{"source_id": "whole-profile-" + hashlib.sha256(profile.encode("utf-8")).hexdigest()[:24],
                         "relative_path": profile_name, "start_line": 1, "end_line": len(profile.splitlines()),
                         "text": profile, "heading": "", "owner_hint": "profile_candidate"}]
    if not profile:
        baseline_sources = []
    cases = []
    for annotation in fixture["cases"]:
        query = annotation["query"]
        # Warm once per query. All following samples use this same loaded snapshot.
        store.prompt_data(store.search(query))
        retrieval_times, preparation_times, baseline_times = [], [], []
        payload = ""
        for _ in range(TIMING_REPETITIONS):
            before = perf_counter_ns()
            baseline_payload = profile
            baseline_times.append((perf_counter_ns() - before) / 1_000_000)
            before = perf_counter_ns()
            hits = store.search(query)
            searched = perf_counter_ns()
            payload = store.prompt_data(hits)
            prepared = perf_counter_ns()
            retrieval_times.append((searched - before) / 1_000_000)
            preparation_times.append((prepared - before) / 1_000_000)
        baseline = judge(annotation, baseline_sources, baseline_payload)
        baseline.update(retrieval_ms_samples=[0.0] * TIMING_REPETITIONS,
                        preparation_ms_samples=baseline_times)
        current = judge(annotation, json.loads(payload)["sources"], payload)
        current.update(retrieval_ms_samples=retrieval_times, preparation_ms_samples=preparation_times)
        if hasattr(store, "explain"):
            current["retrieval_diagnostics"] = store.explain(query)
            current["retrieval_failure_details"] = []
            for expected in current["relevant_ranges"]:
                if expected["covered"]:
                    continue
                candidates = [candidate for candidate in current["retrieval_diagnostics"]["candidates"]
                              if candidate["relative_path"] == expected["path"]
                              and candidate["start_line"] <= expected["end"]
                              and candidate["end_line"] >= expected["start"]]
                current["retrieval_failure_details"].append({
                    "expected": expected,
                    "candidate_reasons": sorted({candidate["reason"] for candidate in candidates})
                    if candidates else ["no_lexical_overlap"],
                })
        cases.append({"id": annotation["id"], "query": query, "topic": annotation["topic"],
                      "annotation": annotation, "whole_profile": baseline, "bm25": current})
    diagnostics = safety_diagnostics()
    # Refuse to combine conditions with source or fixture edits during the run.
    load_fixture(fixture_root, minimum_cases)
    if (file_hash(MODULE) != hashes["materials_sha256"]
            or file_hash(Path(__file__)) != hashes["evaluator_sha256"]
            or file_hash(fixture_root / "manifest.json") != hashes["manifest_sha256"]):
        raise EvaluationError("Evaluation inputs changed while the run was in progress")
    summaries = {condition: summarize(cases, condition) for condition in ("whole_profile", "bm25")}
    result = {
        "created_at": started.isoformat(), "kind": "public_fictional_local_retrieval_evaluation",
        "suite": suite, "phase": phase,
        "api_requests": 0, "source_hashes": hashes, "fixture_manifest": manifest,
        "conditions": {
            "whole_profile": ("Old material-sending strategy: entire fixed profile body on every question, no notes; no historical model answer replay"
                              if is_profile else "Old material-sending strategy with no configured basic profile: empty materials; general documents were not retrieved"),
            "bm25": "Current default MaterialsStore.search(query), then prompt_data(hits); one immutable snapshot, no reranking or answer generation",
            "limits": asdict(store.limits), "index_build_ms": index_build_ms,
            "index_diagnostics": dict(store.diagnostics), "timing_repetitions_per_case": TIMING_REPETITIONS,
            "timing": "One untimed warm-up per query; fixed serial case order; p50=median, p95=nearest-rank; baseline retrieval=0 by definition; payload preparation excludes disk reads, index construction, scoring, output writing and network",
        },
        "metric_definitions": {
            "relevant_range_recall": "Number of fully covered annotated path/line ranges divided by all relevant ranges; coverage may be the union of multiple sent chunks",
            "complete_recall_cases": "Questions with every relevant range covered; excludes questions with no relevant data",
            "forbidden_free_cases": "No overlap with specified forbidden ranges and no sources for forbid_all_sources questions; counts only questions with a prohibition rule",
            "empty_for_no_data_cases": "No sources sent where the fixture says no source establishes the requested fact",
            "body_chars": "Python Unicode character count of sent source bodies, including preserved whitespace",
            "payload_chars": "Exact material payload Unicode characters; BM25 includes JSON provenance and empty-envelope overhead, whole_profile is raw profile body",
            "local_retrieval": "Current search() duration only; excludes indexing, serialization, model and voice latency",
        },
        "summary": summaries, "safety_diagnostics": diagnostics, "cases": cases,
        "limitations": [
            "Retrieval coverage is not fact correctness, source ownership verification, or generated answer quality",
            "Forbidden-range checks are strict sending-purity checks; a candidate being sent does not prove it will be misused",
            "The annotations are finite and do not measure exhaustive semantic precision",
            "All data is fictional; sixteen questions do not establish performance on a user's real corpus",
            "Timing is for this small warm local snapshot and cannot establish end-to-end latency",
            "Whole-profile and JSON payload sizes include their real differing metadata overhead; token counts are not estimated",
        ],
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / ("materials-eval-" + started.strftime("%Y%m%d-%H%M%S-%f") + ".json")
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"result": path.relative_to(ROOT).as_posix(), "summary": summaries,
                      "safety_diagnostics": diagnostics}, ensure_ascii=False, indent=2))
    if not all(item["passed"] for item in diagnostics.values()):
        raise EvaluationError("A safety diagnostic failed; the saved report contains its result")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=("general", "profile-regression-v2"), default="general")
    parser.add_argument("--phase", choices=("baseline", "after", "current"), default="current")
    args = parser.parse_args()
    try:
        run(args.fixture, args.phase)
    except EvaluationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Local evaluation failed ({type(error).__name__}); no source contents printed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
