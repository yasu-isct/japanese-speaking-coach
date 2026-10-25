"""Does putting the learner's profile into the prompt cost latency, and is it used?

    python tools/measure_profile.py                       # uses tests/live_suggest/test_profile.md (fictional)
    python tools/measure_profile.py --profile profile.md  # your own profile (results are printed, not saved)

Text only (no audio): the same sentences are sent alternately without and with the
profile, so both conditions see the same network conditions. Reports API round
trip / first token, and for the questions about the learner whether the replies
used profile facts or 【】 placeholders.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import Suggester, load_env, load_profile  # noqa: E402

GENERAL = ["ポイントカードはお持ちですか？", "お箸おつけしますか？", "来週の飲み会、来られそうですか？",
           "今日マジ寒くない？", "それ、めっちゃいいじゃん！", "明日の会議の資料、もうできましたか？"]
PERSONAL = ["日本に来てどれくらいですか？", "ご出身はどちらですか？", "アルバイトは何をしているんですか？",
            "週末は何してたんですか？", "では、簡単に自己紹介をお願いできますか？"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", type=Path, default=ROOT / "tests" / "live_suggest" / "test_profile.md")
    p.add_argument("--reps", type=int, default=2)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    env = load_env()
    profile = load_profile(args.profile)
    plain = Suggester(env=env, profile="")
    with_profile = Suggester(env=env, profile=profile)
    plain.warm_up()
    with_profile.warm_up()
    print(f"system prompt: {len(plain.system_prompt)} chars without profile, "
          f"{len(with_profile.system_prompt)} with ({len(profile)} chars of profile)\n")

    times = {"without": [], "with": []}
    firsts = {"without": [], "with": []}
    placeholders = {"without": 0, "with": 0}
    replies_on_personal = {"without": 0, "with": 0}
    for rep in range(args.reps):
        for sentence in GENERAL + PERSONAL:
            for label, s in (("without", plain), ("with", with_profile)):
                r = s.suggest(sentence, [])
                times[label].append(r.api_ms)
                if r.first_token_ms:
                    firsts[label].append(r.first_token_ms)
                if sentence in PERSONAL:
                    replies_on_personal[label] += len(r.replies)
                    placeholders[label] += sum("【" in x.text for x in r.replies)
                    if rep == 0:
                        star = next((x.text for x in r.replies if x.star), "")
                        print(f"[{label:7}] {sentence}\n          ⭐ {star}\n          理由：{r.reason}")
    print("\n| | API 往返中位数 | p90 | 首字中位数 | 个人问题的回复里含【】 |\n|---|---|---|---|---|")
    for label in ("without", "with"):
        t = sorted(times[label])
        print(f"| {'不带资料' if label == 'without' else '带资料'} | {statistics.median(t):.0f} ms | "
              f"{t[int(0.9 * (len(t) - 1))]:.0f} ms | {statistics.median(firsts[label]):.0f} ms | "
              f"{placeholders[label]}/{replies_on_personal[label]} |")


if __name__ == "__main__":
    main()
