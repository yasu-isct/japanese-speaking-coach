"""Explicit synthetic startup benchmark; audio/API disabled, own HWND offscreen only."""
from pathlib import Path
from datetime import datetime
import argparse,contextlib,io,json,re,sys,time
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'app'),str(root/'tools')]
from eval_materials_scale import prepare
from jsc_live.main import main
from jsc_live.suggest import Suggester
from jsc_live.overlay import SubtitleWindow
from jsc_live.material_control import MaterialsController
from jsc_live.furigana import Segment
from test_window import create_test_window,_user32
from window_capture import capture_owned_window
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--label',required=True)
parser.add_argument('--model',required=True,type=Path,help='already prepared official local model; no download')
args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',args.label):parser.error('invalid label')
if not args.model.is_dir():parser.error('model directory does not exist')
if sys.platform!='win32':parser.error('owned offscreen HWND captures require Windows')
label=args.label;folder,_=prepare();out=root/'logs/ui-iterations'/('startup-'+label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
env={'LLM_API_KEY':'fake','LLM_BASE_URL':'https://example.invalid','LLM_MODEL':'unused'}
result={'kind':'Real main startup, fixed public 100-file library; audio/ASR and API worker replaced. Not whole application readiness or visible latency.',
 'api_calls':0,'private_data_read':False,'real_user_settings_changed':False}
before=_user32().GetForegroundWindow();start=time.perf_counter()
def fake_worker(args,events,stop,*rest):
    result['asr_worker_dispatched_ms']=(time.perf_counter()-start)*1000
    events.put(('status',0,[Segment('合成启动检查：音频与 API 未运行')]))
    stop.wait(60)
def factory(*args,**kwargs):
    result['window_constructor_entered_ms']=(time.perf_counter()-start)*1000
    window=create_test_window(SubtitleWindow,*args,offscreen=True,**kwargs)
    result['window_constructed_ms']=(time.perf_counter()-start)*1000
    controller=kwargs['materials_controller']
    def capture(name):
        window._poll();window.root.update_idletasks();window.root.update()
        meta=capture_owned_window(window.root,out/(name+'.png'))
        meta['foreground_unchanged']=before==_user32().GetForegroundWindow()
        (out/(name+'.json')).write_text(json.dumps(meta,indent=2),encoding='utf-8')
    def run():
        capture('initial')
        job=getattr(controller,'startup',None)
        ticks=0;maximum_gap=0;last=time.perf_counter();seen_keywords=False
        deadline=time.monotonic()+50
        while job is not None and job.snapshot()['busy'] and time.monotonic()<deadline:
            window.root.update();now=time.perf_counter();maximum_gap=max(maximum_gap,(now-last)*1000);last=now;ticks+=1
            info=job.snapshot()
            if info.get('ready') and not seen_keywords:
                seen_keywords=True;result['keywords_available_ms']=(now-start)*1000;capture('keywords')
            time.sleep(.01)
        if job is not None:
            assert not job.snapshot()['busy'];result['startup_result']=job.snapshot()
        result['all_materials_ready_ms']=(time.perf_counter()-start)*1000
        result['pump_ticks']=ticks;result['maximum_pump_gap_ms']=maximum_gap
        result['indexed_chunks']=len(controller.client.materials.chunks)
        capture('ready');window.close()
    window.run=run
    return window
def client(*args,**kwargs):return Suggester(*args,env=env,**kwargs)
def control(*args,**kwargs):return MaterialsController(*args,settings_path=out/'preferences.json',**kwargs)
with patch('jsc_live.main.worker',fake_worker),patch('jsc_live.suggest.Suggester',side_effect=client),patch('jsc_live.overlay.SubtitleWindow',side_effect=factory),patch('jsc_live.material_control.MaterialsController',side_effect=control),contextlib.redirect_stdout(io.StringIO()):
    main(['--suggest','--no-history','--no-record','--materials',str(folder),'--semantic-model',str(args.model.resolve())])
result['foreground_unchanged']=before==_user32().GetForegroundWindow()
path=root/'tests/live_suggest'/('materials-startup-'+label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
result['captures']=str(out.relative_to(root));path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(path);print(json.dumps(result,ensure_ascii=True))
