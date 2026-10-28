"""Explicit bounded general-document stage; no audio, profile or private input."""
from pathlib import Path
from datetime import datetime
from dataclasses import asdict
import argparse,hashlib,json,re,sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'app')]
from jsc_live.semantic_materials import SemanticEncoder,SemanticMaterialsStore
from jsc_live.suggest import Suggester,load_env
from tools.eval_grounding import validate_client,safe_error


def load_cases(case_set='fixed'):
    fixture=ROOT/'tests/fixtures/materials/document-stage'
    if case_set not in {'fixed', 'transfer'}:
        raise ValueError('Unknown case set')
    manifest_file = 'manifest.json' if case_set == 'fixed' else 'transfer-manifest.json'
    cases_file = 'answer-cases.json' if case_set == 'fixed' else 'transfer-cases.json'
    count = 8 if case_set == 'fixed' else 4
    manifest=json.loads((fixture/manifest_file).read_text(encoding='utf-8'))
    expected={'long_pdf','documents','general-independent'} if case_set == 'fixed' else {'general-independent'}
    for corpus in expected:
        folder=ROOT/'tests/fixtures/materials'/corpus/'data'
        actual={p.relative_to(ROOT).as_posix() for p in folder.rglob('*') if p.is_file()}
        wanted={name for name in manifest['sha256'] if name.startswith(f'tests/fixtures/materials/{corpus}/data/')}
        if actual!=wanted:raise ValueError('Fixed public corpus file list changed')
    for name,sha in manifest['sha256'].items():
        path=ROOT/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise ValueError('Frozen input changed')
    cases=json.loads((fixture/cases_file).read_text(encoding='utf-8'))['cases']
    if len(cases)!=count or any(c['corpus'] not in expected for c in cases):raise ValueError('Fixed public cases required')
    return cases,manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-api',action='store_true')
    parser.add_argument('--model-dir',type=Path)
    parser.add_argument('--label',default='run')
    parser.add_argument('--case-set', choices=('fixed', 'transfer'), default='fixed')
    args=parser.parse_args(argv)
    if not args.run_api:
        print('No requests sent. Check the daily budget, then explicitly use --run-api and --model-dir.');return 0
    if args.model_dir is None or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.label):parser.error('Explicit model directory and valid label required')
    cases,manifest=load_cases(args.case_set);env=load_env()
    if env.get('LLM_BASE_URL','').rstrip('/')!='https://api.deepseek.com':raise ValueError('Unexpected evaluation provider')
    encoder=SemanticEncoder(args.model_dir)
    clients={name:Suggester(env=env,materials=SemanticMaterialsStore(ROOT/'tests/fixtures/materials'/name/'data',encoder=encoder))
             for name in sorted({c['corpus'] for c in cases})}
    for client in clients.values():validate_client(client)
    result={'purpose':'Bounded document-answering stage; agent review required, not general accuracy or visible latency.',
      'label':args.label,'fixture_manifest':manifest,'api_model':'deepseek-flash','thinking':'disabled','temperature':.3,'max_tokens':400,
      'maximum_requests':len(cases),'warmup_requests':0,'retries':0,'private_data_read':False,'real_user_settings_changed':False,
      'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ('app/jsc_live/suggest.py','app/jsc_live/pdf_worker.py','app/jsc_live/semantic_materials.py')},'results':[],'completed':False}
    out=ROOT/'tests/live_suggest'/('document-answers-'+args.label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    for case in cases:
        client=clients[case['corpus']];client.partner=case.get('partner')
        client.prefetch(case['query']);answer=client.suggest(case['query'],[])
        result['results'].append({'case':case,'raw':answer.raw,'replies':[asdict(r) for r in answer.replies],
          'reason':answer.reason,'complete':answer.complete,'error_kind':safe_error(answer.error),
          'star_inferred':answer.star_inferred,'recommended_style':answer.recommended_style,
          'api_ms':answer.api_ms,'first_token_ms':answer.first_token_ms,
          'retrieval_ms':answer.retrieval_ms,'retrieval_cached':answer.retrieval_cached,
          'sources':answer.sources,'cited_source_ids':answer.cited_source_ids})
        out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'case':case['id'],'complete':answer.complete,'citations':len(answer.cited_source_ids),'error':safe_error(answer.error)}),flush=True)
        if sum(bool(row['error_kind']) for row in result['results'])>=2:
            print('Stopped after two failed responses; partial results retained.');return 2
    result['completed']=len(result['results'])==len(cases)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(out);return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('Stage failed: '+type(exc).__name__,file=sys.stderr);raise SystemExit(2)
