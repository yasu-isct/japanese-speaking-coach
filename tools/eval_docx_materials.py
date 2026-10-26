"""Offline, fixed public DOCX retrieval coverage. No API or private directories."""
from pathlib import Path
import argparse, hashlib, json, re, statistics, sys, time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from jsc_live.materials import MaterialsStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--semantic-model', type=Path)
    parser.add_argument('--label', default='run')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', args.label):
        parser.error('--label must contain 1–40 letters, digits, underscores or hyphens')
    corpus = ROOT / 'tests/fixtures/materials/documents'
    manifest = json.loads((corpus/'manifest.json').read_text(encoding='utf-8'))
    expected_names = {'queries.json', 'data/course.docx', 'data/workshop.docx', 'data/readme.txt'}
    actual_names = {'queries.json'} | {p.relative_to(corpus).as_posix() for p in (corpus/'data').rglob('*') if p.is_file()}
    if actual_names != expected_names or set(manifest['sha256']) != expected_names:
        raise SystemExit('Fixed public fixture files changed; refusing to read another corpus.')
    for name, expected in manifest['sha256'].items():
        path = corpus/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit('Fixed public fixture hash mismatch: ' + name)
    start = time.perf_counter()
    if args.semantic_model:
        from jsc_live.semantic_materials import SemanticEncoder, SemanticMaterialsStore
        store = SemanticMaterialsStore(corpus / 'data', encoder=SemanticEncoder(args.semantic_model))
    else:
        store = MaterialsStore(corpus / 'data')
    build_ms = (time.perf_counter() - start) * 1000
    rows, durations = [], []
    for case in json.loads((corpus / 'queries.json').read_text(encoding='utf-8')):
        start = time.perf_counter()
        payload = json.loads(store.prompt_data(store.search(case['query'])))
        durations.append((time.perf_counter() - start) * 1000)
        sources = payload['sources']
        covered = any(s['relative_path'] == case['path'] and all(n in s['text'] for n in case['needles']) for s in sources)
        location_matched = any(s['relative_path'] == case['path'] and s.get('location') == case['location']
                               and all(n in s['text'] for n in case['needles']) for s in sources)
        rows.append(dict(case, covered=covered, location_matched=location_matched, sources=sources))
    result = dict(label=args.label, generated_at=datetime.now().isoformat(), corpus='tests/fixtures/materials/documents/data',
                  mode='semantic' if args.semantic_model else 'keywords', provenance='Synthetic public test documents; no API, no people, not answer accuracy.',
                  sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((corpus/'data').iterdir())},
                  diagnostics=dict(store.diagnostics), build_ms=build_ms, search_p50_ms=statistics.median(durations),
                  search_p95_ms=sorted(durations)[-1], coverage=sum(r['covered'] for r in rows), total=len(rows), rows=rows)
    name = ROOT / 'tests/live_suggest' / ('docx-materials-' + args.label + '-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    name.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','sha256')},ensure_ascii=False))
    print(name)


if __name__ == '__main__':
    main()
