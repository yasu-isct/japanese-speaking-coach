"""Offline review of an explicitly selected archive; no models or API calls."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re
import stat


class SessionReviewError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewExport:
    html_path: Path
    anki_path: Path
    sentence_count: int
    turn_count: int
    card_count: int
    diagnostics: dict[str, int]


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 20000
CLIP_NAME = re.compile(r"(\d{4,})\.wav\Z")


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inside(root: Path, relative: str) -> Path | None:
    """Return an existing regular file inside the archive, never through links."""
    current = root
    for part in Path(relative).parts:
        if part in ("..", ""):
            return None
        current /= part
        if not current.exists() or _linked(current):
            return None
    if not current.resolve().is_relative_to(root) or not current.is_file():
        return None
    return current


def _text(root: Path, name: str, diagnostics: Counter) -> str:
    path = _inside(root, name)
    if path is None:
        return ""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise SessionReviewError(f"{name} 超过32MB，请先选较小的存档。")
    with path.open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise SessionReviewError(f"{name} 超过32MB，请先选较小的存档。")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        diagnostics["无法读取的编码"] += 1
        return ""


def _jsonl(text: str, diagnostics: Counter) -> list[dict]:
    records = []
    for index, line in enumerate(text.splitlines()):
        if index >= MAX_RECORDS:
            diagnostics["超出记录上限"] += 1
            break
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            diagnostics["损坏的JSON行"] += 1
            continue
        if not isinstance(record, dict):
            diagnostics["格式不符的记录"] += 1
            continue
        records.append(record)
    return records


def _string(value) -> str:
    return value if isinstance(value, str) else ""


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _paragraph(value, css: str = "") -> str:
    return f'<p class="{css}">{escape(_string(value))}</p>'


def _audio(root: Path, relative: str) -> str:
    path = _inside(root, relative)
    if path is None:
        return ""
    return f'<audio controls preload="none" src="{escape(path.as_uri(), quote=True)}">浏览器不支持播放。</audio>'


def _sentences(root: Path, diagnostics: Counter) -> list[dict]:
    lines = _text(root, "clips/transcripts.tsv", diagnostics).splitlines()
    sentences = []
    seen = set()
    for line in lines[:MAX_RECORDS]:
        cells = line.split("\t")
        match = CLIP_NAME.fullmatch(cells[0]) if cells else None
        if len(cells) < 2 or match is None:
            diagnostics["损坏的字幕行"] += 1
            continue
        if len(cells) > 3 and cells[3]:
            continue
        uid = int(match[1])
        if uid in seen:
            diagnostics["重复字幕编号"] += 1
            continue
        seen.add(uid)
        if cells[1].strip():
            sentences.append({"uid": uid, "text": cells[1], "clip": cells[0],
                              "language": cells[2] if len(cells) > 2 else "ja"})
    if not sentences:
        # Do not invent a uid from line position: old text-only archives cannot
        # reliably be joined to suggestions or clips by their order.
        for line in _text(root, "transcript.txt", diagnostics).splitlines()[:MAX_RECORDS]:
            if line.strip():
                sentences.append({"uid": None, "text": line, "clip": "", "language": ""})
    return sentences


def _cards(vocabulary: list[dict], include_candidates: bool) -> list[dict]:
    cards, seen = [], set()
    for item in vocabulary:
        if item.get("kind") not in (("lookup", "candidate") if include_candidates else ("lookup",)):
            continue
        word, meaning = _string(item.get("word")).strip(), _string(item.get("meaning")).strip()
        if not word or not meaning or meaning.startswith(("（没查到", "（查询失败", "（没有 .env")):
            continue
        # Preserve different contexts; keep the first stored meaning per context.
        # Context is also part of the Anki front so imports do not merge senses.
        key = (word, _string(item.get("sentence")))
        if key in seen:
            continue
        seen.add(key)
        cards.append(item)
    return cards


def _field(text: str) -> str:
    return escape(re.sub(r"[\t\r\n]+", " ", text), quote=False)


def _anki(cards: list[dict], layout: str) -> str:
    lines = ["#separator:tab", "#html:true"]
    if layout == "basic":
        lines += ["#notetype:Basic", "#columns:Front\tBack\tTags", "#tags column:3"]
    else:
        lines += ["#columns:Front\tReading\tMeaning\tExample\tTags", "#tags column:5"]
    for item in cards:
        word, reading, meaning, sentence = [_string(item.get(key)) for key in ("word", "reading", "meaning", "sentence")]
        front = _field(word) + (f'<br><small>{_field(sentence)}</small>' if sentence else "")
        # Keep the selected surface and its reading together. Lemma is shown
        # separately in the review because its reading may differ.
        if layout == "basic":
            values = [front, "<br>".join(_field(x) for x in (reading, meaning, sentence) if x)]
        else:
            values = [front] + [_field(x) for x in (reading, meaning, sentence)]
        lines.append("\t".join(values + ["jsc session-review"]))
    return "\n".join(lines) + "\n"


CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#192d38;
font:17px/1.7 'Yu Gothic UI','Meiryo',sans-serif}main{max-width:960px;margin:40px auto;padding:0 24px}
h1{font-size:30px;line-height:1.3}h2{font-size:23px;margin-top:38px}h3{font-size:18px;margin:0}
p{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere}.muted{color:#526571;font-size:14px}
nav{display:flex;gap:18px;flex-wrap:wrap;margin:22px 0}a{color:#075e8d}article,.card{background:white;
border:1px solid #d4dfe5;border-radius:10px;padding:20px;margin:16px 0}.reply{border-left:3px solid #b2c4cf;
padding:5px 14px;margin:14px 0}.recommended{border-color:#15785e;background:#eef8f3}.warning{color:#9c3f10}
.japanese{font-size:20px}details{margin:14px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;
background:#f0f4f6;padding:14px;font:15px/1.6 inherit}audio{display:block;max-width:100%;margin:12px 0}
.counts{font-size:19px}.badge{font-size:14px;color:#32624e}.note{border-left:4px solid #507d95;padding-left:14px}
@media(max-width:600px){main{margin:20px auto;padding:0 14px}article,.card{padding:14px}h1{font-size:25px}}
@media print{audio,nav{display:none}body{background:white}main{max-width:none;margin:0}article{break-inside:avoid}}
"""


def _turn_html(turn: dict, index: int, sentence_ids: set[int]) -> str:
    uid_list = [uid for uid in _list(turn.get("utterance_ids")) if isinstance(uid, int) and uid in sentence_ids]
    links = " · ".join(f'<a href="#sentence-{uid}">字幕 {uid}</a>' for uid in uid_list)
    status = {"done": "已显示", "late (done)": "延迟后显示", "error": "生成失败",
              "late (error)": "等待后生成失败", "superseded": "已被新一轮替换，可能未显示"}.get(
                  _string(turn.get("status")), "显示状态未记录")
    result = [f'<article id="turn-{index}"><h3>建议 {index} · {escape(status)}</h3>',
              '<p class="muted">可参考的说法，不代表使用者当时实际说过。</p>', links]
    for sentence in _list(turn.get("sentences")):
        result.append(_paragraph(sentence))
    for reply in _list(turn.get("replies")):
        if not isinstance(reply, dict):
            continue
        recommended = reply.get("star") is True
        label = ("⭐ 推荐 · " if recommended else "") + _string(reply.get("style"))
        result.append(f'<div class="reply {"recommended" if recommended else ""}"><strong>{escape(label)}</strong>')
        result.append(_paragraph(reply.get("text"), "japanese"))
        result.append(_paragraph(reply.get("meaning"), "muted"))
        if reply.get("warn"):
            result.append('<p class="warning">⚠ 这条的语气可能不适合当前对象。</p>')
        result.append("</div>")
    result.append(_paragraph(turn.get("reason")))
    sources = [s for s in _list(turn.get("sources")) if isinstance(s, dict)]
    cited = _list(turn.get("cited_source_ids"))
    if sources:
        result.append('<details><summary>查看当时发送的参考片段</summary><p class="muted">保留的是当时的片段快照。“回答引用”是模型声明，不代表已核实。</p>')
        if turn.get("materials_root"):
            result.append(_paragraph("生成时的资料文件夹：" + _string(turn["materials_root"]), "muted"))
        for source in sources:
            label = "回答引用" if source.get("source_id") in cited else "本轮发送"
            location = f"{_string(source.get('relative_path'))}:{source.get('start_line', '?')}–{source.get('end_line', '?')}"
            result.append(_paragraph(f"{label} · {location}", "muted"))
            result.append(f'<pre>{escape(_string(source.get("text")))}</pre>')
        result.append("</details>")
    result.append("</article>")
    return "\n".join(result)


def _html(root: Path, sentences: list[dict], turns: list[dict], vocabulary: list[dict],
          cards: list[dict], diagnostics: Counter) -> str:
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; media-src file:; img-src \'none\'">',
             f'<title>日语对话复盘 · {escape(root.name)}</title><style>{CSS}</style></head><body><main>',
             '<p class="muted">JAPANESE SPEAKING COACH · 本地复盘</p><h1>把这次对话，再听懂一点</h1>',
             _paragraph(root.name, "muted"),
             f'<p class="counts">{len(sentences)} 条字幕 · {len(turns)} 轮建议 · {len(cards)} 张生词卡</p>',
             '<nav><a href="#transcript">回听字幕</a><a href="#replies">参考说法</a><a href="#vocabulary">查过的词</a><a href="anki.tsv" download>下载 Anki 文件</a></nav>',
             '<p class="note">这页仅整理已有存档，没有调用 API。字幕可能有识别错误；模型建议和词义仍需结合语境判断。</p>',
             '<h2 id="transcript">回听字幕</h2><p class="muted">挑一句没听清的，先回听，再对照文字。系统没有替你判定哪些句子没听懂。</p>']
    if not sentences:
        parts.append(_paragraph("没有可读的字幕记录。"))
    sentence_ids = {s["uid"] for s in sentences if isinstance(s["uid"], int)}
    for index, sentence in enumerate(sentences, 1):
        uid = sentence["uid"]
        anchor = f"sentence-{uid}" if uid is not None else f"line-{index}"
        parts.append(f'<article id="{anchor}"><h3>字幕 {uid if uid is not None else index}</h3>')
        parts.append(_paragraph(sentence["text"], "japanese"))
        if sentence["clip"]:
            parts.append(_audio(root, f'clips/{sentence["clip"]}'))
        related = [i for i, turn in enumerate(turns, 1) if uid is not None and uid in _list(turn.get("utterance_ids"))]
        parts.append(" · ".join(f'<a href="#turn-{i}">查看建议 {i}</a>' for i in related))
        parts.append("</article>")
    parts += ['<h2 id="replies">当时可参考的说法</h2>',
              '<p class="muted">对照 ⭐ 和不同语气，选一句重新说一遍。这里保留原建议，没有新增纠错或评分。</p>']
    if not turns:
        parts.append(_paragraph("没有建议记录；这次可能未开启接话建议。"))
    parts.extend(_turn_html(turn, i, sentence_ids) for i, turn in enumerate(turns, 1))
    parts += ['<h2 id="vocabulary">查过的词</h2>',
              '<p class="muted">卡片采用已有查词释义；同词同语境保留首次记录。卡片正面包含原句，方便区分不同用法。旧存档不会自动补词。</p>']
    lookups = [item for item in vocabulary if item.get("kind") == "lookup"]
    if not lookups:
        parts.append(_paragraph("这份存档没有保存查词记录。录音版里选词查到意思后，会保存在本次存档。"))
    for item in _cards(lookups, False):
        parts.append('<div class="card">')
        parts.append(_paragraph(f'{_string(item.get("word"))} 【{_string(item.get("reading"))}】', "japanese"))
        if item.get("lemma"):
            parts.append(_paragraph(f'辞书形：{_string(item.get("lemma"))}', "muted"))
        parts.extend(_paragraph(item.get(key)) for key in ("meaning", "sentence"))
        uid = item.get("utterance_id")
        if isinstance(uid, int) and uid in sentence_ids:
            parts.append(f'<a href="#sentence-{uid}">回到字幕 {uid}</a>')
        parts.append("</div>")
    candidates = [v for v in vocabulary if v.get("kind") == "candidate"]
    if candidates:
        parts.append(f'<details><summary>另有 {len(candidates)} 条自动候选，不表示你查过或不认识</summary>')
        parts.extend(_paragraph(v.get("word")) for v in candidates)
        parts.append("</details>")
    if diagnostics:
        parts.append('<h2>部分记录需要检查</h2>')
        parts.extend(_paragraph(f"{key}：{value}") for key, value in sorted(diagnostics.items()))
    parts += ['<p class="muted">音频仍在原存档中；移动复盘页不会复制录音。此页含本次谈话内容，请按自己的意愿保管。</p>',
              '</main></body></html>']
    return "\n".join(parts)


def export_session_review(session_dir: str | Path, output_dir: str | Path | None = None, *,
                          anki_layout: str = "basic", include_candidates: bool = False) -> ReviewExport:
    if anki_layout not in ("basic", "fields"):
        raise SessionReviewError("Anki 格式只支持 basic 或 fields。")
    selected = Path(session_dir).expanduser()
    if not selected.is_dir() or _linked(selected):
        raise SessionReviewError("请选择实际存在的存档文件夹，不使用链接。")
    root = selected.resolve()
    diagnostics: Counter = Counter()
    sentences = _sentences(root, diagnostics)
    turns = _jsonl(_text(root, "suggestions.jsonl", diagnostics), diagnostics)
    vocabulary = _jsonl(_text(root, "vocabulary.jsonl", diagnostics), diagnostics)
    if not any(_inside(root, name) for name in ("transcript.txt", "clips/transcripts.tsv", "suggestions.jsonl", "vocabulary.jsonl")):
        raise SessionReviewError("这个文件夹没有字幕、建议或查词存档，请选择一次会话的文件夹。")
    cards = _cards(vocabulary, include_candidates)
    page = _html(root, sentences, turns, vocabulary, cards, diagnostics)
    destination = (Path(output_dir).expanduser() if output_dir is not None else
                   root / f'review-{datetime.now():%Y%m%d-%H%M%S-%f}')
    if destination.exists() or destination.is_symlink():
        raise SessionReviewError("输出文件夹已存在，未覆盖。请指定一个新文件夹。")
    destination.mkdir(parents=True, exist_ok=False)
    html_path, anki_path = destination / "review.html", destination / "anki.tsv"
    with html_path.open("x", encoding="utf-8") as output:
        output.write(page)
    with anki_path.open("x", encoding="utf-8") as output:
        output.write(_anki(cards, anki_layout))
    return ReviewExport(html_path.resolve(), anki_path.resolve(), len(sentences), len(turns), len(cards), dict(diagnostics))
