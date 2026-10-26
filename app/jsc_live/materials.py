"""Bounded, read-only retrieval from an external UTF-8 materials folder.

The index is a snapshot: build once off the UI thread, then call search() from
prefetch workers without mutating it. Reload by constructing a new store. Neither
the repository profile nor session logs are discovered implicitly. Retrieved
text is untrusted reference data, never an instruction or a verified fact.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from itertools import islice
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Mapping


def default_materials_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local and Path(local).is_absolute():
        return Path(local) / "JapaneseSpeakingCoach" / "materials"
    return Path.home() / ".local" / "share" / "JapaneseSpeakingCoach" / "materials"


@dataclass(frozen=True)
class MaterialsLimits:
    max_files: int = 64
    max_file_bytes: int = 65536
    max_document_bytes: int = 10 * 1024 * 1024
    max_total_chars: int = 100000
    max_chunks: int = 256
    max_chunk_chars: int = 700
    max_results: int = 4
    max_result_chars: int = 1800
    max_query_chars: int = 2000
    max_scan_entries: int = 2048
    max_depth: int = 4

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{field.name} must be a positive integer")
        if self.max_result_chars < self.max_chunk_chars:
            raise ValueError("max_result_chars must fit at least one complete chunk")


@dataclass(frozen=True)
class MaterialChunk:
    source_id: str
    relative_path: str
    start_line: int
    end_line: int
    text: str
    heading: str
    owner_hint: str
    location: str = field(default="", kw_only=True)


@dataclass(frozen=True)
class MaterialHit(MaterialChunk):
    score: float


def chunk_data(chunk: MaterialChunk) -> dict:
    data = asdict(chunk)
    if not data["location"]:
        del data["location"]  # Preserve existing text-source payloads and saved logs.
    return data


def source_location(source: Mapping, *, short: bool = False) -> str:
    """DOCX uses body positions, while text sources keep their original line IDs."""
    path = source.get("relative_path", "")
    location = source.get("location")
    if isinstance(location, str) and location:
        return f"{path}:{location}"
    start = source.get("start_line", "?")
    return f"{path}:{start}" if short else f"{path}:{start}–{source.get('end_line', '?')}"


# These concepts improve cross-language matching, not fact extraction or inference.
CONCEPTS = {
    "name": ("名前", "氏名", "姓名", "名字", "name"),
    "origin": ("出身", "故郷", "ふるさと", "家乡", "籍贯", "hometown", "origin"),
    "age": ("年齢", "何歳", "年龄", "age"),
    "work": ("仕事", "職業", "お勤め", "工作", "职业", "打工", "アルバイト", "job", "occupation"),
    "school": ("学校", "大学", "学院", "school", "university"),
    "major": ("専攻", "専門", "研究分野", "专业", "major"),
    "hobby": ("趣味", "好きなこと", "爱好", "興味", "hobby", "hobbies"),
    "residence": ("住ん", "住ま", "住在", "住所", "居住", "residence", "address"),
    "arrival": ("日本に来", "来日本", "来日", "arrival"),
    "weekend": ("週末", "周末", "weekend"),
    "schedule": ("予定", "都合", "空いて", "安排", "有空", "schedule", "availability"),
    "experience": ("経験", "ことがあ", "经历", "经验", "experience"),
}
COMMON_BIGRAMS = frozenset(("です", "すか", "ます", "した", "して", "てい", "いる", "ある", "こと",
                            "これ", "それ", "あれ", "から", "まで", "ので", "んで", "だよ", "んだ",
                            "くだ", "ださ", "さい", "この", "その", "あの", "どの", "さん", "さま",
                            "ため", "よう", "もの", "する", "なる", "れる", "られ", "ない", "あり",
                            "什么", "怎么", "怎样", "如何", "多少", "哪里", "哪儿", "是否", "可以",
                            "应该", "这个", "那个", "这些", "那些", "我们", "你们", "他们", "现在"))
CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]+")
FIELD_LINE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|[^:：]{1,40}[:：]\s*\S)")
HEADING_LINE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
# Lexical evidence thresholds are heuristic, not probabilities or fact checks.
MIN_QUERY_COVERAGE = 0.20
MIN_RELATIVE_SCORE = 0.50


def _content_term(term: str) -> bool:
    if term.startswith("concept:"):
        return True
    if term.startswith("word:"):
        return len(term[5:]) >= 2
    if not term.startswith("cjk:"):
        return True  # An injected tokenizer defines its own meaningful units.
    gram = term[4:]
    if gram in COMMON_BIGRAMS:
        return False
    if len(gram) == 2 and (gram[0] in "のにをがでともはへ" or gram[1] in "のにをがでともはへ"):
        return False  # Particle/word boundary overlap is weak retrieval evidence.
    return bool(re.search(r"[\u30a1-\u30fa\u3400-\u4dbf\u4e00-\u9fff]", gram))


def tokenize(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = ["word:" + word for word in re.findall(r"[a-z0-9]+", normalized)]
    for run in CJK_RUN.findall(normalized):
        grams = [run] if len(run) == 1 else (run[index:index + 2] for index in range(len(run) - 1))
        tokens.extend("cjk:" + gram for gram in grams if gram not in COMMON_BIGRAMS)
    for concept, aliases in CONCEPTS.items():
        if any((re.search(r"\b" + re.escape(alias) + r"\b", normalized) if alias.isascii()
                else alias in normalized) for alias in aliases):
            tokens.append("concept:" + concept)
    return tuple(token for token in tokens if _content_term(token))


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _blocks(text: str) -> Iterable[tuple[list[tuple[int, str]], str]]:
    """Keep fields separate and carry heading context so ownership cues survive."""
    block: list[tuple[int, str]] = []
    headings: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        heading = HEADING_LINE.match(line) if not fenced else None
        is_fence = line.lstrip().startswith(("```", "~~~"))
        is_field = bool(FIELD_LINE.match(line)) and not fenced
        if heading or not line.strip() or is_field or is_fence:
            if block:
                yield block, " / ".join(value for _, value in headings)[:160]
                block = []
        if heading:
            level = len(heading.group(1))
            headings = [(depth, value) for depth, value in headings if depth < level]
            headings.append((level, heading.group(2)))
        elif line.strip():
            block.append((number, line.rstrip()))
        if is_fence:
            fenced = not fenced
    if block:
        yield block, " / ".join(value for _, value in headings)[:160]


def _chunk_parts(block: list[tuple[int, str]], limit: int) -> Iterable[tuple[int, int, str]]:
    content: list[str] = []
    start = end = 0
    size = 0
    for number, line in block:
        for offset in range(0, len(line), limit):
            piece = line[offset:offset + limit]
            added = len(piece) + int(bool(content))
            if content and size + added > limit:
                yield start, end, "\n".join(content)
                content, size = [], 0
            if not content:
                start = number
            content.append(piece)
            size += len(piece) + int(len(content) > 1)
            end = number
    if content:
        yield start, end, "\n".join(content)


class MaterialsStore:
    """Immutable retrieval snapshot; absent/unreadable materials give no hits.

    Only root-level profile.md and basic-info.md receive the profile_candidate
    owner hint. This describes the source's intended purpose, not the truth or
    ownership of every statement; quotes and third-party facts remain unverified.
    """

    def __init__(self, root: str | Path | None = None, *, limits: MaterialsLimits | None = None,
                 tokenizer: Callable[[str], Iterable[str]] = tokenize) -> None:
        self.root = Path(root) if root is not None else default_materials_root()
        self.limits = limits or MaterialsLimits()
        self._tokenizer = tokenizer
        counts: Counter[str] = Counter()
        chunks: list[MaterialChunk] = []
        document_issues: list[dict] = []
        try:
            if not self.root.exists():
                counts["missing_root"] += 1
            elif _is_link(self.root) or not self.root.is_dir():
                counts["unsafe_root"] += 1
            else:
                resolved_root = self.root.resolve(strict=True)
                for path in self._paths(resolved_root, counts):
                    if counts["files_considered"] >= self.limits.max_files or len(chunks) >= self.limits.max_chunks:
                        counts["index_limit_reached"] += 1
                        break
                    counts["files_considered"] += 1
                    relative = path.relative_to(resolved_root).as_posix()
                    if path.suffix.casefold() == ".docx":
                        from .document_text import DocumentError, extract_docx

                        raw = self._read_bytes(path, resolved_root, counts, self.limits.max_document_bytes)
                        if raw is None:
                            document_issues.append({"path": relative, "message": "文件无法读取、已变化或超过10MB，请检查后重新读取。"})
                            continue
                        try:
                            sections, notices = extract_docx(raw, max_chars=self.limits.max_total_chars)
                        except DocumentError as exc:
                            counts["document_read_error"] += 1
                            document_issues.append({"path": relative, "message": str(exc)})
                            continue
                        document_issues.extend({"path": relative, "message": notice} for notice in notices)
                        if not sections:
                            counts["document_without_text"] += 1
                            continue
                        size = sum(len(section.text) for section in sections)
                        blocks = ((list(enumerate(section.text.splitlines(), 1)), section.heading, section.location)
                                  for section in sections)
                    else:
                        text = self._read(path, resolved_root, counts)
                        if text is None:
                            continue
                        size = len(text)
                        blocks = ((block, heading, "") for block, heading in _blocks(text))
                    if counts["indexed_chars"] + size > self.limits.max_total_chars:
                        counts["total_chars_limit"] += 1
                        document_issues.append({"path": relative, "message": "资料总文字超过当前容量，此文件未读取。请减少文件后重新读取。"})
                        continue
                    hint = "profile_candidate" if "/" not in relative and relative.casefold() in (
                        "profile.md", "basic-info.md") else "unverified"
                    counts["indexed_files"] += 1
                    counts["indexed_chars"] += size
                    for block, heading, location in blocks:
                        for part, (start, end, body) in enumerate(_chunk_parts(block, self.limits.max_chunk_chars)):
                            identity_parts = [relative, start, end, part, heading, body]
                            if location:
                                identity_parts.append(location)
                            identity = json.dumps(identity_parts, ensure_ascii=False)
                            source_id = "material-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
                            chunks.append(MaterialChunk(source_id, relative, start, end, body, heading, hint, location=location))
                            if len(chunks) >= self.limits.max_chunks:
                                break
                        if len(chunks) >= self.limits.max_chunks:
                            break
        except (OSError, RuntimeError):
            counts["root_read_error"] += 1
        self.chunks = tuple(chunks)
        self.document_issues = tuple(MappingProxyType(issue) for issue in document_issues)
        self.diagnostics: Mapping[str, int] = MappingProxyType(dict(counts, indexed_chunks=len(chunks)))
        self._terms = tuple(Counter(tokenizer(chunk.heading + "\n" + chunk.text)) for chunk in self.chunks)
        self._body_terms = tuple(frozenset(tokenizer(chunk.text)) for chunk in self.chunks)
        self._lengths = tuple(sum(terms.values()) for terms in self._terms)
        self._average_length = sum(self._lengths) / len(self._lengths) if self._lengths else 1.0
        self._document_frequency = Counter(term for terms in self._terms for term in terms)

    def rebuild(self, root):
        """Create a new snapshot with the same limits and retrieval configuration."""
        return MaterialsStore(root, limits=self.limits, tokenizer=self._tokenizer)

    def _paths(self, root: Path, counts: Counter) -> Iterable[Path]:
        pending = [(root, 0)]
        while pending and counts["scanned_entries"] < self.limits.max_scan_entries:
            folder, depth = pending.pop()
            try:
                if _is_link(folder) or not _inside(folder.resolve(strict=True), root):
                    counts["unsafe_path"] += 1
                    continue
                entries = []
                with os.scandir(folder) as scan:
                    for entry in scan:
                        if counts["scanned_entries"] >= self.limits.max_scan_entries:
                            counts["scan_limit_reached"] += 1
                            break
                        counts["scanned_entries"] += 1
                        entries.append(Path(entry.path))
                directories = []
                for path in sorted(entries, key=lambda value: (value.name.casefold(), value.name)):
                    if _is_link(path):
                        counts["unsafe_path"] += 1
                    elif path.is_dir():
                        if depth < self.limits.max_depth:
                            directories.append((path, depth + 1))
                        else:
                            counts["depth_limit_reached"] += 1
                    elif path.suffix.casefold() in (".md", ".txt", ".docx") and path.is_file():
                        yield path
                pending.extend(reversed(directories))
            except (OSError, RuntimeError):
                counts["directory_read_error"] += 1

    def _read(self, path: Path, root: Path, counts: Counter) -> str | None:
        raw = self._read_bytes(path, root, counts, self.limits.max_file_bytes)
        if raw is not None:
            try:
                return raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                counts["invalid_utf8"] += 1
        return None

    def _read_bytes(self, path: Path, root: Path, counts: Counter, byte_limit: int) -> bytes | None:
        try:
            resolved = path.resolve(strict=True)
            if not _inside(resolved, root) or _is_link(path):
                counts["unsafe_path"] += 1
                return None
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > byte_limit:
                counts["file_size_or_type_limit"] += 1
                return None
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as handle:
                if _signature(os.fstat(handle.fileno())) != _signature(before):
                    counts["file_changed"] += 1
                    return None
                raw = handle.read(byte_limit + 1)
                after = os.fstat(handle.fileno())
            if (len(raw) > byte_limit or _signature(after) != _signature(before)
                    or _is_link(path) or path.resolve(strict=True) != resolved
                    or _signature(path.lstat()) != _signature(before)):
                counts["file_changed"] += 1
                return None
            return raw
        except (OSError, RuntimeError):
            counts["file_read_error"] += 1
        return None

    def _ranked(self, query: str) -> tuple[set[str], list[dict]]:
        terms = set(self._tokenizer(query[:self.limits.max_query_chars]))
        if not terms or not self.chunks:
            return terms, []
        count = len(self.chunks)
        ranked = []
        for chunk, frequency, body_terms, length in zip(self.chunks, self._terms, self._body_terms, self._lengths):
            score = 0.0
            for term in terms:
                tf = frequency.get(term, 0)
                if not tf:
                    continue
                df = self._document_frequency[term]
                idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
                norm = 1.2 * (0.25 + 0.75 * length / max(self._average_length, 1.0))
                score += idf * tf * 2.2 / (tf + norm)
            if score > 0:
                matched = terms.intersection(frequency)
                body_matches = terms.intersection(body_terms)
                coverage = len(matched) / len(terms)
                reason = ("no_body_evidence" if not body_matches else
                          "low_query_coverage" if coverage < MIN_QUERY_COVERAGE else "eligible")
                ranked.append({"score": score, "chunk": chunk, "matched_terms": sorted(matched),
                               "body_matched_terms": sorted(body_matches), "query_coverage": coverage,
                               "reason": reason})
        ranked.sort(key=lambda item: (-item["score"], item["chunk"].relative_path.casefold(),
                                      item["chunk"].start_line, item["chunk"].source_id))
        strongest = max((item["score"] for item in ranked if item["reason"] == "eligible"), default=0)
        for item in ranked:
            if item["reason"] == "eligible" and item["score"] < strongest * MIN_RELATIVE_SCORE:
                item["reason"] = "below_relative_score"
        return terms, ranked

    def search(self, query: str, *, limit: int | None = None) -> list[MaterialHit]:
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
            raise ValueError("limit must be a non-negative integer")
        maximum = min(self.limits.max_results, limit) if limit is not None else self.limits.max_results
        if not maximum:
            return []
        _, ranked = self._ranked(query)
        hits = []
        chars = 0
        for item in ranked:
            if item["reason"] != "eligible":
                continue
            chunk = item["chunk"]
            if chars + len(chunk.text) > self.limits.max_result_chars:
                continue  # Do not cut a fact in half to fit the remaining budget.
            hits.append(MaterialHit(**asdict(chunk), score=item["score"]))
            chars += len(chunk.text)
            if len(hits) >= maximum:
                break
        return hits

    def explain(self, query: str) -> dict:
        """Return optional local diagnostics; no logging or ownership inference."""
        terms, ranked = self._ranked(query)
        selected = {hit.source_id for hit in self.search(query)}
        candidates = []
        for item in ranked:
            chunk = item["chunk"]
            reason = item["reason"]
            if chunk.source_id in selected:
                reason = "selected"
            elif reason == "eligible":
                reason = "result_budget"
            candidates.append({key: value for key, value in item.items() if key not in ("chunk", "reason")}
                              | {"source_id": chunk.source_id, "relative_path": chunk.relative_path,
                                 "start_line": chunk.start_line, "end_line": chunk.end_line, "reason": reason})
        return {"query_terms": sorted(terms), "minimum_query_coverage": MIN_QUERY_COVERAGE,
                "minimum_relative_score": MIN_RELATIVE_SCORE, "requires_body_evidence": True,
                "candidates": candidates,
                "limitation": "Lexical relevance only; no answerability, truth or ownership verification"}

    def prompt_data(self, hits: Iterable[MaterialHit]) -> str:
        """Serialize only this snapshot's allowed hits as JSON, never prompt instructions.

        The caller must retain its higher-priority rule that source text cannot
        issue instructions. JSON escaping alone is not a prompt-injection defense.
        """
        available = {chunk.source_id: chunk for chunk in self.chunks}
        selected = []
        seen = set()
        chars = 0
        for hit in islice(hits, self.limits.max_chunks):
            chunk = available.get(hit.source_id)
            if chunk is None or chunk.source_id in seen:
                continue
            if chars + len(chunk.text) > self.limits.max_result_chars:
                continue
            selected.append(chunk_data(chunk))
            seen.add(chunk.source_id)
            chars += len(chunk.text)
            if len(selected) >= self.limits.max_results:
                break
        return json.dumps({"kind": "untrusted_reference_data", "sources": selected}, ensure_ascii=False)
