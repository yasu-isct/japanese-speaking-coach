"""Fixed public PDF retrieval and physical-page provenance; no generation API."""
from pathlib import Path
from datetime import datetime
import argparse,hashlib,json,re,statistics,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from jsc_live.materials import MaterialsStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='run')
    parser.add_argument('--semantic-model',type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.label):
        parser.error('Use letters, digits, underscores or hyphens for --label')
    corpus=ROOT/'tests/fixtures/materials/pdf'
    manifest=json.loads((corpus/'manifest.json').read_text(encoding='utf-8'))
    expected={'queries.json','data/course.pdf','data/handbook.pdf','data/scan-only.pdf'}
    actual={'queries.json'}|{p.relative_to(corpus).as_posix() for p in (corpus/'data').rglob('*') if p.is_file()}
    if expected!=actual or set(manifest['sha256'])!=expected:
        raise SystemExit('Fixed public fixture file list changed.')
    for name,sha in manifest['sha256'].items():
        path=corpus/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise SystemExit('Fixed public fixture hash mismatch: '+name)
    start=time.perf_counter()
    if args.semantic_model:
        from jsc_live.semantic_materials import SemanticEncoder,SemanticMaterialsStore
        store=SemanticMaterialsStore(corpus/'data',encoder=SemanticEncoder(args.semantic_model))
    else:
        store=MaterialsStore(corpus/'data')
    build_ms=(time.perf_counter()-start)*1000
    rows=[];times=[]
    for case in json.loads((corpus/'queries.json').read_text(encoding='utf-8')):
        start=time.perf_counter()
        sources=json.loads(store.prompt_data(store.search(case['query'])))['sources']
        times.append((time.perf_counter()-start)*1000)
        covered=any(s['relative_path']==case['path'] and all(n in s['text'] for n in case['needles']) for s in sources)
        located=any(s['relative_path']==case['path'] and s.get('location')==case['location'] and all(n in s['text'] for n in case['needles']) for s in sources)
        rows.append(dict(case,covered=covered,location_matched=located,sources=sources))
    result=dict(provenance='Fixed synthetic PDF retrieval, not generated answer accuracy or visible latency.',
        api_calls=0,network_calls=0,private_data_read=False,mode='semantic' if args.semantic_model else 'keywords',
        sha256=manifest['sha256'],diagnostics=dict(store.diagnostics),issues=[dict(i) for i in store.document_issues],
        build_ms=build_ms,search_p50_ms=statistics.median(times),search_p95_ms=sorted(times)[-1],
        coverage=sum(r['covered'] for r in rows),page_locations=sum(r['location_matched'] for r in rows),total=len(rows),rows=rows)
    out=ROOT/'tests/live_suggest'/('pdf-materials-'+args.label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','sha256')},ensure_ascii=False));print(out)


if __name__=='__main__': main()
