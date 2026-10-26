# Synthetic PDF materials

These three PDFs are public parser fixtures, not real class or business documents. Two PDFs contain four text pages in Japanese, Chinese and English. A third file contains one image-only page. No private input, recording, account information or generated model answer is included.

The ten retrieval questions were frozen before application PDF support was added. They cover course enrolment/submission/equipment rules and workshop/English troubleshooting instructions. Expected positions count physical PDF pages from 1; they do not use printed page labels.

Created with the bundled ReportLab 4.4.9 and Pillow 12.3.0, using built-in CID fonts without embedding proprietary fonts. All five pages were rendered with bundled Poppler at 85 DPI and inspected locally. Poppler reported font-substitution warnings; the rendered Japanese, Chinese and English text was legible. Rendering is evidence for these small fixtures, not a general layout-fidelity benchmark. Application/tests require only pypdf 6.21.2, not ReportLab or Poppler.

Before support: 0/10 target passages. After support: keyword 8/10, optional multilingual 9/10 with corresponding page positions. The Japanese question about provision of lunch still returns no source for the Chinese lunch rule. Keep this failure; do not rewrite the question to make the benchmark pass. The image-only page yields an explicit no-text notice and no fabricated text.

`manifest.json` freezes raw bytes with LF text metadata. Run:

```powershell
python tools/eval_pdf_materials.py --label keywords
python tools/eval_pdf_materials.py --label semantic --semantic-model "<prepared model directory>"
```

No API is called. Retrieval coverage is not generated answer accuracy. Larger, multi-column, scanned and unusual-font PDFs remain outside this small test's evidence.
