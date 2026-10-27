"""Can the learner say the suggestions as they are? (text only, no audio, no private data)

    python tools/eval_speakable.py --label before
    python tools/eval_speakable.py --label after

The 20 public sentences of tests/sample_dialogues.md, 2 passes each, with an EMPTY
materials folder (the app's normal path when the learner has added nothing).
Each sentence is labelled by what the other person asks for:

- choice : something the learner decides on the spot (do you have a card, can you come,
           will you cover the shift). Every reply must be sayable as it is, i.e. contain
           no 【】 blank; the other answer belongs in the reason as a ready phrase.
- fact   : background the app cannot know (how long in Japan, what happened to "that thing",
           a meeting time). A 【】 blank is allowed and expected instead of an invented value.
- other  : reactions, thanks, agreement. No blank should be needed.

Reported: sayable replies (no 【】) for choice+other, choice items whose reason offers the
other answer in Japanese, fact items that use a blank instead of an invented value, ⭐ agreement
with the table's expected style, and API time. Results go to tests/live_suggest/speakable-<label>-<time>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

from eval_star import PUBLIC_SOURCE, load_public_cases  # noqa: E402
from jsc_live.materials import MaterialsStore  # noqa: E402
from jsc_live.suggest import Suggester, load_env  # noqa: E402

KIND = {1: "choice", 2: "choice", 3: "choice", 4: "other", 5: "choice", 6: "choice", 7: "fact",
        8: "choice", 9: "choice", 10: "other", 11: "other", 12: "choice", 13: "other", 14: "fact",
        15: "other", 16: "other", 17: "other", 18: "other", 19: "fact", 20: "other"}
JAPANESE_QUOTE = re.compile(r"「[^」]*[぀-ヿ一-鿿][^」]*」")
PASSES = 2


def pct(values, q):
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cases = load_public_cases(ROOT / PUBLIC_SOURCE)
    env = load_env()
    with tempfile.TemporaryDirectory() as empty:
        store = MaterialsStore(root=empty)
        rows = []
        clients = {}
        for n in range(PASSES):
            for case in cases:
                partner = case["partner"]
                if partner not in clients:
                    clients[partner] = Suggester(env=env, partner=partner, materials=store)
                r = clients[partner].suggest(case["sentences"], case["context"])
                star = next((x.style for x in r.replies if x.star), None)
                rows.append({
                    "id": case["id"], "pass": n + 1, "kind": KIND[case["id"]], "sentence": case["sentences"][0],
                    "expected": case["expected"], "star": star, "star_inferred": r.star_inferred,
                    "complete": r.complete, "error": r.error, "api_ms": r.api_ms,
                    "replies": [{"style": x.style, "text": x.text, "meaning": x.meaning} for x in r.replies],
                    "reason": r.reason,
                })
                print(f"{case['id']:2d} {KIND[case['id']]:6} ⭐{star} | " + " / ".join(x.text for x in r.replies)
                      + f" | 理由：{r.reason[:60]}", flush=True)

    def blank(text):
        return "【" in text

    def offers_other_answer(row):
        # A quoted Japanese phrase in the reason that is a new sentence for the learner,
        # not a quote of what the other person said and not one of the three replies.
        said = row["sentence"]
        replies = {x["text"] for x in row["replies"]}
        for quote in JAPANESE_QUOTE.findall(row["reason"]):
            phrase = quote[1:-1]
            if phrase not in said and phrase not in replies and len(phrase) >= 4:
                return True
        return False

    sayable = [x for r in rows if r["kind"] != "fact" for x in r["replies"]]
    choice = [r for r in rows if r["kind"] == "choice"]
    fact = [r for r in rows if r["kind"] == "fact"]
    api = [r["api_ms"] for r in rows if r["api_ms"]]
    summary = {
        "label": args.label,
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "model": env.get("LLM_MODEL"),
        "sayable_replies": f"{sum(not blank(x['text']) for x in sayable)}/{len(sayable)}",
        "choice_reason_offers_other_answer": f"{sum(offers_other_answer(r) for r in choice)}/{len(choice)}",
        "fact_items_with_blank": f"{sum(any(blank(x['text']) for x in r['replies']) for r in fact)}/{len(fact)}",
        "star_matches_expected": f"{sum(r['star'] == r['expected'] for r in rows)}/{len(rows)}",
        "star_chosen_by_model": f"{sum(not r['star_inferred'] for r in rows)}/{len(rows)}",
        "complete": f"{sum(bool(r['complete']) for r in rows)}/{len(rows)}",
        "api_ms_p50": round(statistics.median(api)), "api_ms_p95": round(pct(api, 0.95)),
    }
    out = ROOT / "tests" / "live_suggest" / f"speakable-{args.label}-{time.strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("saved", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
