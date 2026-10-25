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
