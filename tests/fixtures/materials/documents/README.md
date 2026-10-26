# Synthetic Word materials

Two small DOCX files contain fictional course instructions and a craft workshop notice. No real person, class, organization or private recording is represented. `readme.txt` is only a public-data disclaimer. The ten questions were written and measured against the old reader before DOCX support was added. They check whether expected content is retrieved, **not whether a generated answer is correct**.

`course.docx` covers MP3 submissions, recording duration, absence notification and a simple equipment table. It also contains a deleted old instruction and explicitly hidden sentinel text. `workshop.docx` covers start time, clothing, collection and a Chinese cancellation rule. The final question asks about that Chinese rule in Japanese.

The packages were generated with the small standard-library helper `tests/docx_helpers.py`, using minimal WordprocessingML and fixed ZIP timestamps. They are parser fixtures, not a visual-layout or Office interoperability benchmark. `manifest.json` records the original bytes. Normal Word page layout was not rendered or human-reviewed.

Coverage before support: 0/10. After support: keyword 9/10, optional local multilingual mode 10/10. Corpus and questions are development examples; common real-world Word layouts still need testing. No generation API was called.

Run from the repository root:

```powershell
python tools/eval_docx_materials.py --label keywords
python tools/eval_docx_materials.py --label semantic --semantic-model "<prepared model directory>"
```
