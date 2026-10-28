# Development and Checks

## Unit Tests (CPU)

At the repository root, create an isolated environment with Python 3.12. In Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

On macOS / Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests
```

`requirements-dev.txt` installs only NumPy (for sentence-splitting and resampling tests) and fugashi + unidic-lite (for furigana tests). Anki export, format checks, and the packaging tool use only the Python standard library. The tests do not require a GPU, microphone, audio device, or API key, and do not download speech recognition models. Do not install the full application dependencies from `app/requirements.txt` to run the tests.

Currently, `tests/test_live.py` tests only CPU logic; it includes no GPU or speech recognition model tests. The existing `skipUnless` skips only the five furigana tests in `FuriganaTest` when fugashi is missing; all other tests still run. CI installs the full development dependencies and then runs all unit tests.

## Evaluate the Live Recommendation Choice (Paid API)

Run the fixed text-only evaluation at the repository root, before and after a suggestion change:

```powershell
python tools/eval_star.py --phase baseline
python tools/eval_star.py --phase after
```

The script uses the existing `.env` configuration and requires `deepseek-flash` with thinking disabled. It explicitly uses an empty profile, so it never loads personal `profile.md`. Each run sends 161 requests at most: one unscored warm-up, all 20 public sentences from `tests/sample_dialogues.md` three times, then the last 100 classroom turns from `logs/sessions/20261025-114606/suggestions.jsonl` once. The explicit partner labels in samples 6, 10 and 16 are passed through the same `partner` setting as the application's `--partner`; sample 16 retains the full label from the table. Requests run sequentially in fixed order, without retries; three consecutive transport/protocol errors stop the run with a nonzero exit code.

The default run sends those saved classroom sentences and their context to the configured API. For a public-only check, use `--group A`; that cannot establish the classroom acceptance target. Public sample inputs and outputs are saved to `tests/live_suggest/star-eval-<Tokyo timestamp>.json`. Classroom inputs and outputs are never saved by this script; their summary is printed only to the console. Keep any captured console output under ignored `logs/`, and put classroom summary numbers only in the local delivery report.

The report separates literal `star_inferred == false` from a valid model choice (exactly one parsed star and no error), and records final-choice agreement, model-only agreement, `complete`, errors and API latency. Latency includes every attempted scored request: p50 is the median, p95 is the nearest rank. `complete` retains the application's definition (three styles and one star), not a full Japanese-language quality review. Compare input and evaluator hashes between runs; incomplete runs are marked `completed: false` and cannot establish acceptance. This is a text replay, not a new live audio or end-to-end latency test.

## Build the Skill Upload Package

Run the following at the repository root (you can also use Python from the environment above):

```bash
python tools/package_skill.py
```

The tool takes no command-line arguments. Its output path is fixed at `dist/japanese-speaking-coach.zip`, and each run regenerates that file. It reads `skill/` recursively, excluding only `__pycache__/` and `.pyc`, and does not read `app/`, `tests/`, or `docs/`. The package should currently contain only these six release files:

```text
japanese-speaking-coach/LICENSE.txt
japanese-speaking-coach/SKILL.md
japanese-speaking-coach/references/basic_kanji.md
japanese-speaking-coach/references/politeness.md
japanese-speaking-coach/references/scenarios.md
japanese-speaking-coach/scripts/export_anki.py
```

Before uploading, you can use the standard library to list the files in the package:

```bash
python -m zipfile -l dist/japanese-speaking-coach.zip
python -m zipfile -t dist/japanese-speaking-coach.zip
```

`skill/` is the release directory, so include only files that need to be distributed with the Skill. Do not put logs, recordings, models, or credentials in this directory; the packaging tool does not automatically filter them out.
