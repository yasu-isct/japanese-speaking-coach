"""Evaluate the application store on frozen public corpora only."""
from pathlib import Path
from datetime import datetime
import argparse,json,sys,time,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tools')]
from jsc_live.semantic_materials import SemanticEncoder,SemanticMaterialsStore
from eval_materials import load_fixture,judge,timing_summary
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model-dir',required=True,type=Path,help='prepared pinned assets; no automatic download')
args=parser.parse_args()
encoder=SemanticEncoder(args.model_dir)
results={}
for name in ('general','general-followup','general-independent'):
    corpus=ROOT/'tests/fixtures/materials'/('general' if name=='general-followup' else name)
    _,fixture,_=load_fixture(corpus)
    if name=='general-followup':
        fixture=json.loads((ROOT/'tests/fixtures/materials/general-followup/queries.json').read_text(encoding='utf-8'))
        manifest=json.loads((ROOT/'tests/fixtures/materials/general-followup/manifest.json').read_text(encoding='utf-8'))
        assert hashlib.sha256((ROOT/'tests/fixtures/materials/general-followup/queries.json').read_bytes()).hexdigest()==manifest['sha256']['queries.json']
    start=time.perf_counter_ns()
    store=SemanticMaterialsStore(corpus/'data',encoder=encoder)
    build_ms=(time.perf_counter_ns()-start)/1e6
    rows=[];times=[]
    for case in fixture['cases']:
        start=time.perf_counter_ns();hits=store.search(case['query']);times.append((time.perf_counter_ns()-start)/1e6)
        payload=store.prompt_data(hits)
        rows.append({'annotation':case,'result':judge(case,json.loads(payload)['sources'],payload)})
    results[name]={'index_ms':build_ms,'windows':store.diagnostics['semantic_windows'],'chunks':len(store.chunks),
      'timing':timing_summary(times),'rows':rows,'summary':{
        'covered':sum(r['result']['relevant_ranges_covered'] for r in rows),'expected':sum(r['result']['relevant_ranges_expected'] for r in rows),
        'empty':sum(r['result']['empty_sources'] for r in rows if r['annotation'].get('forbid_all_sources')),
        'no_data':sum(bool(r['annotation'].get('forbid_all_sources')) for r in rows),
        'mean_payload_chars':sum(r['result']['payload_chars'] for r in rows)/len(rows)}}
    print(json.dumps({name:results[name]['summary'],'timing':results[name]['timing'],'windows':results[name]['windows']},ensure_ascii=False))
out=ROOT/'tests/live_suggest'/('semantic-runtime-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
out.write_text(json.dumps({'api_calls':0,'private_data_read':False,'runtime_sha256':hashlib.sha256((ROOT/'app/jsc_live/semantic_materials.py').read_bytes()).hexdigest(),'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'suites':results},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(str(out))
