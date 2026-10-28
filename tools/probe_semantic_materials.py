"""Offline prototype of multilingual retrieval on frozen public documents.

Requires explicit, already-downloaded official ONNX assets; never downloads,
loads user materials, calls an API, or changes application behaviour/settings.
Example: python tools/probe_semantic_materials.py --model-dir logs/goal/models/multilingual-minilm
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools')]
from jsc_live.materials import MaterialsStore
from eval_materials import judge, load_fixture, timing_summary

MODEL_REPO = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
MODEL_REVISION = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
MODEL_FILES = {
    'onnx/model_quint8_avx2.onnx': '98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f',
    'tokenizer.json': '2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8',
}
# Fixed before observing prototype scores. Similarity is not answerability.
MIN_SIMILARITY = 0.35
MAX_SCORE_GAP = 0.15


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Encoder:
    def __init__(self, directory):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        for name, digest in MODEL_FILES.items():
            if sha(directory / name) != digest:
                raise ValueError('Model/tokenizer hash differs from the pinned official assets')
        self.tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id('<pad>'), pad_token='<pad>')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(directory / 'onnx/model_quint8_avx2.onnx'),
                                            sess_options=options, providers=['CPUExecutionProvider'])
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.runtime = ort.__version__

    def encode(self, texts):
        np = self.np
        vectors = []
        for start in range(0, len(texts), 4):
            rows = self.tokenizer.encode_batch(texts[start:start + 4])
            feed = {'input_ids': np.array([row.ids for row in rows], dtype=np.int64),
                    'attention_mask': np.array([row.attention_mask for row in rows], dtype=np.int64),
                    'token_type_ids': np.array([row.type_ids for row in rows], dtype=np.int64)}
            token_vectors = self.session.run(None, {name: value for name, value in feed.items() if name in self.inputs})[0]
            mask = feed['attention_mask'][..., None].astype(np.float32)
            mean = (token_vectors * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
            vectors.append(mean / np.maximum(np.linalg.norm(mean, axis=1, keepdims=True), 1e-9))
        return np.concatenate(vectors)


def evaluate(encoder, store, vectors, cases):
    rows, times = [], []
    ids = {chunk.source_id: index for index, chunk in enumerate(store.chunks)}
    for annotation in cases:
        start = time.perf_counter_ns()
        scores = vectors @ encoder.encode([annotation['query']])[0]
        elapsed = (time.perf_counter_ns() - start) / 1e6
        times.append(elapsed)
        ranked = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))
        best = float(scores[ranked[0]])
        semantic = [i for i in ranked if scores[i] >= max(MIN_SIMILARITY, best - MAX_SCORE_GAP)][:4]
        lexical = [ids[hit.source_id] for hit in store.search(annotation['query'])]
        fused = {}
        for weight, ranking in ((1.0, semantic), (0.5, lexical)):
            for rank, index in enumerate(ranking, 1):
                fused[index] = fused.get(index, 0) + weight / (60 + rank)
        hybrid = sorted(fused, key=lambda i: (-fused[i], i))[:4]
        row = {'annotation': annotation, 'embedding_ms': elapsed,
               'top_similarities': [{'path': store.chunks[i].relative_path, 'line': store.chunks[i].start_line,
                                    'cosine': round(float(scores[i]), 5)} for i in ranked[:5]]}
        for name, selection in (('lexical', lexical), ('semantic', semantic), ('hybrid', hybrid)):
            # Serialize through the existing snapshot guard and its unchanged size budget.
            payload = store.prompt_data(store.chunks[i] for i in selection)
            row[name] = judge(annotation, json.loads(payload)['sources'], payload)
        rows.append(row)
    summary = {}
    for condition in ('lexical', 'semantic', 'hybrid'):
        summary[condition] = {
            'relevant_covered': sum(row[condition]['relevant_ranges_covered'] for row in rows),
            'relevant_expected': sum(row[condition]['relevant_ranges_expected'] for row in rows),
            'no_data_empty': sum(row[condition]['empty_sources'] for row in rows if row['annotation'].get('forbid_all_sources')),
            'no_data_cases': sum(bool(row['annotation'].get('forbid_all_sources')) for row in rows),
            'mean_payload_chars': sum(row[condition]['payload_chars'] for row in rows) / len(rows),
        }
    return {'summary': summary, 'embedding_timing': timing_summary(times), 'cases': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    _, original, _ = load_fixture(ROOT / 'tests/fixtures/materials/general')
    followup_root = ROOT / 'tests/fixtures/materials/general-followup'
    manifest = json.loads((followup_root / 'manifest.json').read_text(encoding='utf-8'))
    actual = {'queries.json': sha(followup_root / 'queries.json'),
              'corpus_manifest': sha(ROOT / 'tests/fixtures/materials/general/manifest.json')}
    if manifest['sha256'] != actual:
        raise ValueError('Follow-up fixture changed after baseline freeze')
    followup = json.loads((followup_root / 'queries.json').read_text(encoding='utf-8'))
    store = MaterialsStore(ROOT / 'tests/fixtures/materials/general/data')
    start = time.perf_counter_ns()
    encoder = Encoder(args.model_dir)
    load_ms = (time.perf_counter_ns() - start) / 1e6
    start = time.perf_counter_ns()
    vectors = encoder.encode([chunk.heading + '\n' + chunk.text for chunk in store.chunks])
    index_ms = (time.perf_counter_ns() - start) / 1e6
    result = {'model': MODEL_REPO, 'revision': MODEL_REVISION, 'license': 'Apache-2.0',
              'official_assets': MODEL_FILES, 'runtime': encoder.runtime, 'cpu_threads': 2,
              'model_load_ms': load_ms, 'chunk_embedding_ms': index_ms, 'chunks': len(store.chunks),
              'policy': {'minimum_cosine': MIN_SIMILARITY, 'maximum_score_gap': MAX_SCORE_GAP,
                         'hybrid': 'RRF k=60, semantic weight=1, lexical weight=0.5, max4'},
              'api_calls': 0, 'private_data_read': False, 'app_default_changed': False,
              'fixture_hashes': actual, 'probe_sha256': sha(Path(__file__)),
              'limitation': 'Prototype on a tiny fixed corpus; similarity is not answerability. Not an end-to-end or visible latency run.',
              'original': evaluate(encoder, store, vectors, original['cases']),
              'followup': evaluate(encoder, store, vectors, followup['cases'])}
    out = ROOT / 'tests/live_suggest' / ('semantic-probe-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    with out.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(str(out))
    for name in ('original', 'followup'):
        print(json.dumps({name: result[name]['summary'], 'embedding_timing': result[name]['embedding_timing']}, ensure_ascii=False))
    print(json.dumps({'load_ms': load_ms, 'index_ms': index_ms, 'chunks': len(store.chunks)}))


if __name__ == '__main__':
    main()
