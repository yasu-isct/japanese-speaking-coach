# Public synthetic long-page PDF

Four fictional handbook pages, twenty topical sections, sixteen Japanese questions. English fieldwork/studio instructions and Japanese/Chinese workshop notes; no real organisation, person or private input. `content.json` records the source text; `queries.json` and all input hashes were frozen before D-036 implementation. `data/handbook.pdf` was created with bundled ReportLab 4.4.9, deterministic canvas metadata.

All four pages were rendered with bundled Poppler at 85 DPI and visually inspected. CID font substitution warnings were retained; Japanese and Chinese text is legible. This fixture covers ordinary single-column pages with visible section gaps, not complex tables, scanned pages, arbitrary typography or all PDFs.

The original sixteen evidence targets already scored 16/16 with semantic retrieval. An additional context measure, defined before changing the application, checks whether the complete source section (heading and body) appears in one selected snippet. Baseline: 9/16; mean selected text 1370.875 characters. Original baseline JSON was retained when adding this measure. Neither metric is generated-answer accuracy.

Run `python tools/eval_long_pdf.py --label check` from the repository root. Add `--semantic-model logs/goal/models/multilingual-minilm` for the already prepared model. This only reads the fixed public fixture, validates hashes, and performs local retrieval; no audio, generation API or download.
