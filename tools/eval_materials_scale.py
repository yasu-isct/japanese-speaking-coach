"""Offline capacity benchmark with generated fictional files, not natural-QA accuracy."""
from pathlib import Path
from datetime import datetime
import argparse,ctypes,hashlib,json,re,statistics,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from jsc_live.materials import MaterialsStore


def library():
    """100 fixed text files, 16 paragraphs each; unique IDs test complete indexing."""
    files={}
    for number in range(1,101):
        paragraphs=[f'# 架空資料 {number:03d}：公開の容量検証用\n']
        for section in range(1,17):
            token=f'fixture{number:03d}section{section:02d}'
            paragraphs.append(f'{token}。この記録はソフトウェア検証のために作成した架空の講座案内です。'
                f'第{section}回の作業は指定された順番で行い、終わったら用具を片付けます。'
                f'受付番号は{number:03d}-{section:02d}です。疑問があれば開始前に担当者へ確認してください。')
        files[f'lesson-{number:03d}.md']='\n\n'.join(paragraphs)+'\n'
    return files


def prepare():
    folder=ROOT/'logs/goal/public-scale-fixture-v1'
    folder.mkdir(parents=True,exist_ok=True)
    files=library()
    if any(p.name not in files for p in folder.iterdir()):
        raise ValueError('Unexpected file in fixed public fixture directory.')
    for name,text in files.items():
        path=folder/name;raw=text.encode('utf-8')
        if path.exists():
            if path.is_symlink() or path.read_bytes()!=raw:
                raise ValueError('Public fixture changed: '+name)
        else:
            with path.open('xb') as out:out.write(raw)
    return folder,files


def memory_bytes():
    if sys.platform!='win32':return None
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(name,ctypes.c_size_t) for name in
            ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
    process=ctypes.windll.kernel32.GetCurrentProcess
    process.restype=ctypes.c_void_p
    read=ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    counters=Counters();counters.cb=ctypes.sizeof(counters)
    if not read(process(),ctypes.byref(counters),counters.cb):return None
    return {'working_set':counters.WorkingSetSize,'private':counters.PrivateUsage,'peak_working_set':counters.PeakWorkingSetSize}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--semantic-model',type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.label):parser.error('invalid label')
    folder,files=prepare();before=memory_bytes();start=time.perf_counter()
    if args.semantic_model:
        from jsc_live.semantic_materials import SemanticEncoder,SemanticMaterialsStore
        store=SemanticMaterialsStore(folder,encoder=SemanticEncoder(args.semantic_model))
    else:store=MaterialsStore(folder)
    elapsed=(time.perf_counter()-start)*1000;after=memory_bytes();rows=[];times=[]
    for number in (1,10,20,30,40,50,60,70,80,100):
        query=f'fixture{number:03d}section16'
        start=time.perf_counter();sources=json.loads(store.prompt_data(store.search(query)))['sources'];times.append((time.perf_counter()-start)*1000)
        rows.append({'query':query,'expected_file':f'lesson-{number:03d}.md','covered':any(query in s['text'] for s in sources),
                     'selected_source_ids':[s['source_id'] for s in sources]})
    final_marker_files=sum(any(f'fixture{number:03d}section16' in chunk.text for chunk in store.chunks) for number in range(1,101))
    result={'provenance':'100 synthetic files, 16 paragraphs per file. Identifier queries test capacity, not natural language answer quality.',
      'api_calls':0,'private_data_read':False,'network_calls':0,'mode':'semantic' if args.semantic_model else 'keywords',
      'generated_input':{'files':100,'paragraphs':1600,'characters':sum(len(t) for t in files.values()),
        'sha256':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in files.items()}},
      'diagnostics':dict(store.diagnostics),'final_marker_files_indexed':final_marker_files,
      'index_ms':elapsed,'memory_before':before,'memory_after':after,
      'search_p50_ms':statistics.median(times),'search_p95_ms':sorted(times)[-1],
      'identifier_query_coverage':sum(row['covered'] for row in rows),'identifier_queries':len(rows),'rows':rows}
    out=ROOT/'tests/live_suggest'/('materials-scale-'+args.label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','generated_input')},ensure_ascii=False));print(out)


if __name__=='__main__':main()
