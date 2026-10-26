"""Fixed synthetic long-PDF evidence retrieval, no audio or generation API."""
from pathlib import Path
from datetime import datetime
import argparse,hashlib,json,re,statistics,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
from jsc_live.materials import MaterialsStore

def compact(text):return re.sub(r'\s+','',text)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True);parser.add_argument('--semantic-model',type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.label):parser.error('invalid label')
    corpus=ROOT/'tests/fixtures/materials/long_pdf'
    manifest=json.loads((corpus/'manifest.json').read_text(encoding='utf-8'))
    if {p.name for p in (corpus/'data').iterdir()}!={'handbook.pdf'}:raise ValueError('Fixture files changed')
    for name,sha in manifest['sha256'].items():
        path=corpus/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('Fixture hash mismatch: '+name)
    content=json.loads((corpus/'content.json').read_text(encoding='utf-8'))
    start=time.perf_counter()
    if args.semantic_model:
        from jsc_live.semantic_materials import SemanticEncoder,SemanticMaterialsStore
        store=SemanticMaterialsStore(corpus/'data',encoder=SemanticEncoder(args.semantic_model))
    else:store=MaterialsStore(corpus/'data')
    build_ms=(time.perf_counter()-start)*1000;rows=[];times=[]
    for case in json.loads((corpus/'queries.json').read_text(encoding='utf-8')):
        start=time.perf_counter();sources=json.loads(store.prompt_data(store.search(case['query'])))['sources'];times.append((time.perf_counter()-start)*1000)
        sources_on_page=[s for s in sources if s['relative_path']==case['path'] and s.get('location','').startswith(f"第{case['page']}页")]
        match=lambda text:all(compact(needle) in compact(text) for needle in case['needles'])
        targets=[heading+'\n'+text for heading,text in content[case['page']-1][2] if match(text)]
        if len(targets)!=1:raise ValueError('Ambiguous expected section: '+case['id'])
        rows.append(dict(case,atomic_covered=any(match(s['text']) for s in sources_on_page),
                         combined_covered=match('\n'.join(s['text'] for s in sources_on_page)),
                         whole_section_present=any(compact(targets[0]) in compact(s['text']) for s in sources_on_page),
                         selected_characters=sum(len(s['text']) for s in sources),sources=sources))
    result={'provenance':'Frozen synthetic long-page retrieval. Evidence coverage, not generated-answer accuracy.',
      'api_calls':0,'private_data_read':False,'mode':'semantic' if args.semantic_model else 'keywords',
      'sha256':manifest['sha256'],'diagnostics':dict(store.diagnostics),'issues':[dict(i) for i in store.document_issues],
      'build_ms':build_ms,'search_p50_ms':statistics.median(times),'search_p95_ms':sorted(times)[-1],
      'atomic_coverage':sum(r['atomic_covered'] for r in rows),'combined_coverage':sum(r['combined_covered'] for r in rows),
      'whole_section_coverage':sum(r['whole_section_present'] for r in rows),
      'mean_selected_characters':statistics.mean(r['selected_characters'] for r in rows),
      'total':len(rows),'rows':rows}
    path=ROOT/'tests/live_suggest'/('long-pdf-'+args.label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(path);print(json.dumps({k:v for k,v in result.items() if k not in ('sha256','rows')},ensure_ascii=True))

if __name__=='__main__':main()
