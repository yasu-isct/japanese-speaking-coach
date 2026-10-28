"""Score document-answer runs for D-040: conclusion first, still complete, shorter.

    python tools/score_conclusion_first.py tests/live_suggest/document-answers-conclusion-*.json

Input: runs of tools/eval_document_answers.py on the 8 frozen public questions.
For the starred reply it reports
- first-sentence length (the part the glance view shows first),
- whether each required fact of the case is mentioned anywhere in the starred reply
  (Japanese keywords below, written from the case's required_facts; a proxy, not a judge),
- characters of all three replies (what the model has to generate) and the API time.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

# case id -> list of facts; each fact is a list of alternative keywords
FACTS = {
    "press": [["15分", "十五分"], ["布"], ["スポンジ"]],
    "booking": [["前日"], ["500円", "五百円"], ["閉"]],
    "meal": [["用意", "出ません", "ありません", "出ない"], ["窓"]],
    "storage": [["3日", "三日"], ["14時", "2時", "二時"], ["17時", "5時", "五時"]],
    "absence": [["2時間", "二時間"], ["連絡欄", "サイト"], ["電話"]],
    "cancel": [["前日"], ["サイト"], ["材料費"]],
    "renewal": [["予約"], ["期限", "前"], ["1回", "一回", "一度"]],
    "equipment_missing": [["書いて", "書かれて", "記載", "載って", "ありません", "わかりません"]],
}
SENTENCE = re.compile(r"[^。！？!?]+[。！？!?]?")


def first_sentence(text: str) -> str:
    m = SENTENCE.match(text.strip())
    return m.group(0) if m else text


def score(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows, covered, total = [], 0, 0
    for r in data["results"]:
        cid = r["case"]["id"]
        star = next((x for x in r["replies"] if x.get("star")), r["replies"][0] if r["replies"] else {"text": ""})
        text = star["text"]
        facts = FACTS[cid]
        hit = [any(k in text for k in fact) for fact in facts]
        covered += sum(hit)
        total += len(facts)
        if cid == "equipment_missing":
            hit = [hit[0] and "【" not in text]
        rows.append({"case": cid, "star_style": star.get("style"), "first_sentence": first_sentence(text),
                     "first_sentence_chars": len(first_sentence(text)), "star_chars": len(text),
                     "sentences": len(SENTENCE.findall(text)), "facts_in_star": f"{sum(hit)}/{len(facts)}",
                     "all_reply_chars": sum(len(x["text"]) for x in r["replies"]), "api_ms": round(r["api_ms"] or 0)})
    api = [x["api_ms"] for x in rows]
    return {"file": path.name, "facts_in_star": f"{covered}/{total}",
            "first_sentence_chars_median": statistics.median(x["first_sentence_chars"] for x in rows),
            "star_chars_median": statistics.median(x["star_chars"] for x in rows),
            "all_reply_chars_median": statistics.median(x["all_reply_chars"] for x in rows),
            "api_ms_median": statistics.median(api), "api_ms_max": max(api), "rows": rows}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for name in sys.argv[1:]:
        s = score(Path(name))
        print(f"== {s['file']}: facts in ⭐ {s['facts_in_star']}, first sentence {s['first_sentence_chars_median']} chars, "
              f"⭐ {s['star_chars_median']} chars, all replies {s['all_reply_chars_median']} chars, "
              f"API median {s['api_ms_median']:.0f} ms (max {s['api_ms_max']})")
        for r in s["rows"]:
            print(f"   {r['case']:17} {r['facts_in_star']} {r['sentences']}句 {r['api_ms']:5d}ms | {r['first_sentence']}")


if __name__ == "__main__":
    main()
