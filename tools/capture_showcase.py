"""Render frozen public TTS results through the current UI, outside the desktop.

Explicit: python tools/capture_showcase.py --render-saved --materials tests/fixtures/materials/general/data
No arguments do nothing. No API, desktop recording, profile writes or default user materials.
This is saved TTS replay, not a fresh audio/API latency run.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import queue
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / 'tests/fixtures/materials/general/data'
SAVED = ROOT / 'tests/live_suggest/e2e-references-20261026-171615.jsonl'
# Hash of text with universal newlines, independent of core.autocrlf.
SAVED_TEXT_SHA256 = '8e6f27f9eea43500076ce016dddb8a6a4592688a56afa28956475752aefbac0a'


def load_public_inputs(materials: Path) -> list[dict]:
    sys.path.insert(0, str(ROOT / 'tools'))
    from eval_materials import load_fixture, public_path
    if materials.resolve() != MATERIALS.resolve():
        raise ValueError('Showcase accepts only the explicit public general-document fixture')
    load_fixture(MATERIALS.parent)
    path = public_path(ROOT, SAVED.relative_to(ROOT).as_posix())
    text = path.read_text(encoding='utf-8')
    if hashlib.sha256(text.encode('utf-8')).hexdigest() != SAVED_TEXT_SHA256:
        raise ValueError('Saved public TTS log changed; refusing unverified content')
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if len(rows) != 4 or any(row.get('status') != 'done' or len(row.get('replies', [])) != 3 for row in rows):
        raise ValueError('Saved TTS demonstration is incomplete')
    return rows


def render(rows: list[dict], output: Path) -> dict:
    sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools')]
    from jsc_live.furigana import Furigana
    from jsc_live.overlay import SubtitleWindow, FG_FINAL
    from test_window import create_test_window, verify_offscreen
    from window_capture import capture_owned_window
    output.mkdir(parents=True, exist_ok=False)
    furigana = Furigana('graded')
    win = create_test_window(SubtitleWindow, queue.Queue(), alpha=1.0, offscreen=True,
                             materials_root='公开虚构文档（TTS展示）', history=1)
    metadata = {'kind': 'saved public TTS replay; current UI rendered offscreen',
                'generated_at': datetime.now().isoformat(), 'input': SAVED.relative_to(ROOT).as_posix(),
                'input_text_sha256': SAVED_TEXT_SHA256, 'live_latency_measured': False,
                'api_calls': 0, 'private_materials_read': False, 'screenshots': [],
                'overlay_sha256': hashlib.sha256((ROOT / 'app/jsc_live/overlay.py').read_bytes()).hexdigest(),
                'test_window': win.test_window, 'completed': False}
    try:
        for index, row in enumerate(rows, 1):
            win.lines = [(row['utterance_id'], furigana.annotate(row['text']), FG_FINAL)]
            replies = [{**r, 'segments': furigana.annotate(r['text'])} for r in row['replies']]
            payload = {'state': 'done', 'replies': replies, 'reason': row.get('reason', ''),
                       'sources': row.get('sources', []), 'cited_source_ids': row.get('cited_source_ids', [])}
            win.suggestion = {'uid': row['utterance_id'], 'payload': payload, 'timed_out': False, 'reported': True}
            win.redraw()
            win.root.update()
            bounds = verify_offscreen(win.root)
            target = output / f'references-{index}.png'
            captured = capture_owned_window(win.root, target)
            metadata['screenshots'].append({'path': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                                            **captured, **bounds})
        metadata['completed'] = len(metadata['screenshots']) == 4
    finally:
        win.close()
        (output / 'capture.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render-saved', action='store_true', help='explicit offscreen replay of public TTS results')
    parser.add_argument('--materials', type=Path, help='must be tests/fixtures/materials/general/data')
    parser.add_argument('--output', type=Path, help='new local folder; never overwritten')
    args = parser.parse_args(argv)
    if not args.render_saved:
        print('Nothing opened. Use --render-saved with the explicit public --materials fixture.')
        return 0
    if args.materials is None:
        parser.error('--render-saved requires explicit --materials')
    try:
        rows = load_public_inputs(args.materials)
        output = args.output or ROOT / 'logs/showcase' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        metadata = render(rows, output)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({'completed': metadata['completed'], 'output': str(output), 'screenshots': len(metadata['screenshots']),
                      'offscreen': True, 'new_latency_measurement': False}, ensure_ascii=False))
    return 0 if metadata['completed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
