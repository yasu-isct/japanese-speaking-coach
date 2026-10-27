# tests/live_suggest — evaluation outputs

Raw results written by the tools in `tools/`. Each file is evidence for a number quoted in
`docs/decisions.md` / `docs/status.md`, so files are kept rather than overwritten; the newest file of
a family is the current one. All inputs are synthesized speech (Windows TTS), public or fictional
text — no real recordings, no personal data. `.gitattributes` marks this folder as generated, so
GitHub collapses it in pull-request diffs.

| File family | Written by | What it measures | Decision |
|---|---|---|---|
| `speakable-*.json` | `tools/eval_speakable.py` | Can the suggestions be said as they are; other answer offered; background facts blanked | D-038 |
| `e2e-dialogue-*`, `e2e-monologue-*`, `e2e-references-*` | `tools/e2e_suggest.py` | Full TTS → subtitle → suggestion run; ASR / API / render times | D-012〜D-038 |
| `star-eval-*.json` | `tools/eval_star.py` | ⭐ chosen by the model and agreement with `tests/sample_dialogues.md` | D-021 |
| `grounding-eval-*`, `grounding-review-*` | `tools/eval_grounding.py`, `eval_grounding_holdout.py` | Personal facts not invented (fixed 16 + 8 holdout cases; agent review, not human) | D-022, D-023 |
| `references-eval-*`, `document-answers-*`, `document-*` | `tools/eval_reference_answers.py`, `eval_document_answers.py` | Answers from external documents, with citations | D-024, D-037 |
| `semantic-*`, `materials-*`, `material-*`, `model-*` | `tools/eval_semantic_*.py`, `eval_materials*.py`, `probe_semantic_materials.py` | Retrieval recall, startup/scale/cancel behaviour, model settings | D-029〜D-035 |
| `pdf-*`, `long-pdf-*` | `tools/eval_pdf_materials.py`, `eval_long_pdf.py` | Word/PDF reading, page citations, long-PDF sections | D-032, D-033, D-036 |
| `review-*` | session review export checks | Local review page and Anki export | D-025, D-028 |
| `showcase-*` | `tools/capture_showcase.py` | Numbers behind the screenshots in `docs/images/` | D-026 and the site hand-off |
| `model-compare-*` | ad-hoc comparison | deepseek-flash vs deepseek-v4-pro | D-012 |
| `test_profile.md` | — | Fictional learner profile used by `tools/measure_profile.py` | D-018 |
