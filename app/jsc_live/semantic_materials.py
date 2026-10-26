"""Optional local multilingual retrieval from explicitly installed, pinned assets.

No downloads or user-directory discovery. The original material chunks remain
the source of prompt text and citations; token windows are only retrieval views.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import threading
from types import MappingProxyType

from .materials import MaterialHit, MaterialsStore

MODEL_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
ASSETS = {
    "onnx/model_quint8_avx2.onnx": (118453870, "98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f"),
    "tokenizer.json": (9081518, "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"),
}
MIN_SIMILARITY = 0.35


class SemanticUnavailable(RuntimeError):
    """Local model setup/inference failed; callers can keep lexical retrieval."""


def verify_assets(directory: Path):
    for name, (size, expected) in ASSETS.items():
        path = directory / name
        try:
            if path.stat().st_size != size:
                raise SemanticUnavailable("本地检索模型不完整，请按说明准备固定版本。")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise SemanticUnavailable("本地检索模型校验未通过，请重新取得固定官方文件。")
        except OSError:
            raise SemanticUnavailable("找不到本地检索模型，请检查模型文件夹。") from None


class SemanticEncoder:
    """Shared CPU encoder; lock one small batch at a time so reloads yield."""
    def __init__(self, directory: Path):
        directory = Path(directory)
        verify_assets(directory)
        self.directory = directory.resolve()
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self.np = np
            self.lock = threading.Lock()
            self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
            self.tokenizer.no_truncation()
            self.tokenizer.no_padding()
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self.session = ort.InferenceSession(str(directory / "onnx/model_quint8_avx2.onnx"),
                                                sess_options=options, providers=["CPUExecutionProvider"])
            self.inputs = {item.name for item in self.session.get_inputs()}
            self.pad_id = self.tokenizer.token_to_id("<pad>")
            self.runtime = ort.__version__
        except Exception:
            raise SemanticUnavailable("无法载入本地检索模型，暂时使用关键词查找。") from None

    def windows(self, heading: str, body: str) -> list[str]:
        """Cover the entire body, preserving original characters and overlap."""
        with self.lock:
            heading_tokens = self.tokenizer.encode(heading, add_special_tokens=False)
            if len(heading_tokens.ids) > 24:
                heading = heading[:heading_tokens.offsets[23][1]]
            tokens = self.tokenizer.encode(body, add_special_tokens=False)
            offsets = tokens.offsets
            if not offsets:
                return [heading + "\n" + body]
            pieces = []
            start = 0
            while start < len(offsets):
                end = min(start + 96, len(offsets))
                left = 0 if start == 0 else offsets[start][0]
                while True:
                    right = len(body) if end == len(offsets) else offsets[end - 1][1]
                    piece = (heading + "\n" if heading else "") + body[left:right]
                    if len(self.tokenizer.encode(piece).ids) <= 128:
                        break
                    end -= 1
                    if end <= start:
                        raise SemanticUnavailable("资料分段失败，暂时使用关键词查找。")
                pieces.append(piece)
                if end == len(offsets):
                    break
                start = max(start + 1, end - 16)
            return pieces

    def encode(self, texts: list[str]):
        np = self.np
        outputs = []
        try:
            for offset in range(0, len(texts), 4):
                with self.lock:
                    rows = self.tokenizer.encode_batch(texts[offset:offset + 4])
                    width = max(len(row.ids) for row in rows)
                    if width > 128:
                        raise SemanticUnavailable("检索文本超过窗口长度，暂时使用关键词查找。")
                    ids = np.full((len(rows), width), self.pad_id, dtype=np.int64)
                    mask = np.zeros_like(ids)
                    types = np.zeros_like(ids)
                    for i, row in enumerate(rows):
                        length = len(row.ids)
                        ids[i, :length], mask[i, :length], types[i, :length] = row.ids, row.attention_mask, row.type_ids
                    feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
                    encoded = self.session.run(None, {key: value for key, value in feed.items() if key in self.inputs})[0]
                weights = mask[..., None].astype(np.float32)
                mean = (encoded * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
                normalized = mean / np.maximum(np.linalg.norm(mean, axis=1, keepdims=True), 1e-9)
                if normalized.shape[1] != 384 or not np.isfinite(normalized).all():
                    raise SemanticUnavailable("本地检索结果异常，暂时使用关键词查找。")
                outputs.append(normalized)
            return np.concatenate(outputs) if outputs else np.empty((0, 384), dtype=np.float32)
        except SemanticUnavailable:
            raise
        except Exception:
            raise SemanticUnavailable("本地检索暂不可用，已回到关键词查找。") from None

    def query(self, text: str):
        # The final question matters most in a long current speaking turn.
        return self.encode([self.windows("", text)[-1]])[0]


class SemanticMaterialsStore(MaterialsStore):
    def __init__(self, root=None, *, encoder, **kwargs):
        super().__init__(root, **kwargs)
        self.encoder = encoder
        self.semantic_failed = threading.Event()
        views, owners = [], []
        for index, chunk in enumerate(self.chunks):
            pieces = encoder.windows(chunk.heading, chunk.text)
            views.extend(pieces)
            owners.extend([index] * len(pieces))
        self._vectors = encoder.encode(views)
        self._owners = tuple(owners)
        self._chunk_ids = {chunk.source_id: i for i, chunk in enumerate(self.chunks)}
        self.diagnostics = MappingProxyType(dict(self.diagnostics, semantic_enabled=1, semantic_windows=len(views)))

    def rebuild(self, root):
        return SemanticMaterialsStore(root, encoder=self.encoder, limits=self.limits, tokenizer=self._tokenizer)

    def explain(self, query: str) -> dict:
        hits = self.search(query)
        return {"mode": "keywords_fallback" if self.semantic_failed.is_set() else "local_multilingual_hybrid",
                "minimum_cosine": MIN_SIMILARITY, "semantic_windows": len(self._owners),
                "selected_source_ids": [hit.source_id for hit in hits],
                "limitation": "Semantic similarity and lexical overlap are not answerability or truth verification"}

    def search(self, query: str, *, limit: int | None = None) -> list[MaterialHit]:
        lexical = super().search(query, limit=limit)
        maximum = min(self.limits.max_results, limit) if limit is not None else self.limits.max_results
        if not maximum or not query.strip() or not self.chunks or self.semantic_failed.is_set():
            return lexical
        try:
            similarities = self._vectors @ self.encoder.query(query[:self.limits.max_query_chars])
            scores = {}
            for owner, similarity in zip(self._owners, similarities):
                scores[owner] = max(scores.get(owner, -1), float(similarity))
            # Keep up to three semantically related chunks. A relative gap alone
            # wrongly excluded complementary rules next to a very similar fact.
            semantic = sorted((i for i, value in scores.items() if value >= MIN_SIMILARITY),
                              key=lambda i: (-scores[i], i))[:3]
            fused = {}
            for weight, indices in ((1.0, semantic), (0.5, [self._chunk_ids[hit.source_id] for hit in lexical])):
                for rank, index in enumerate(indices, 1):
                    fused[index] = fused.get(index, 0) + weight / (60 + rank)
            chosen = sorted(fused, key=lambda i: (-fused[i], i))
            hits, chars = [], 0
            for index in chosen:
                chunk = self.chunks[index]
                if chars + len(chunk.text) > self.limits.max_result_chars:
                    continue
                hits.append(MaterialHit(**asdict(chunk), score=fused[index]))
                chars += len(chunk.text)
                if len(hits) >= maximum:
                    break
            return hits
        except SemanticUnavailable:
            self.semantic_failed.set()
            return lexical


def open_materials(root, model_directory: Path | None = None):
    """Explicit opt-in; unavailable models keep the app usable without networking."""
    if model_directory is None:
        return MaterialsStore(root), None
    try:
        encoder = SemanticEncoder(model_directory)
        return SemanticMaterialsStore(root, encoder=encoder), None
    except SemanticUnavailable as exc:
        return MaterialsStore(root), str(exc)
