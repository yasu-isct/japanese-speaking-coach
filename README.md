# Japanese Speaking Coach / 日本語会話コーチ

A Claude Skill that helps Chinese-speaking learners (JLPT N4–N2) living in Japan **understand**, **reply to** and **remember** real-life Japanese conversation.

中国語を母語とする日本語学習者（N4〜N2）が、日本での実際の会話を「聞き取る・返す・覚える」ためのClaude Skillです。

[English](#english) ・ [日本語](#日本語) ・ [中文简介](#中文简介)

> 📷 Screenshots / demo GIF: *coming soon* — see `docs/` for sample outputs in the meantime.
> スクリーンショット・デモGIFは準備中です。

---

## English

### What it does

| Mode | You type | You get |
|---|---|---|
| **A. Reply suggestions** (default) | A Japanese sentence someone said to you | Furigana + one-line meaning + key point, then **3 replies** (polite / casual / simplest) with exactly one ⭐ recommended |
| **B. Say it in Japanese** | 「我想说……」 (what you want to say, in Chinese) | The same 3-reply format |
| **C. Role-play** | 「陪练」 + a scene | The AI plays the other person in one of 8 scenes (convenience store, part-time job interview, doctor, real estate agent, job interview…) and gives 💡 tips only for misunderstandings or rude replies |
| **D. Review** | 「复盘」 | Corrections, what you did well, and a vocabulary CSV you can import into **Anki** |

Example (mode A):

```
## 听懂
原文：週末(しゅうまつ)何(なに)してたんですか？
意思：你周末做什么了？
要点：「してた」是「していた」的口语缩约。

## 回复建议
1. 丁寧⭐｜【友達(ともだち)と鎌倉(かまくら)】に行(い)ってきました。〇〇さんは？
   → 和朋友去了镰仓。你呢？
2. カジュアル｜【友達(ともだち)と鎌倉(かまくら)】行(い)ってきた！〇〇は？
   → 和朋友去镰仓了！你呢？
3. かんたん｜【家(いえ)でゆっくり】しました。
   → 在家好好休息了。
推荐理由：对方用了です/ます，按同等礼貌程度回答最稳妥。

## 生词
- ゆっくりする：悠闲地度过、好好休息
```

Design highlights:
- **Most urgent first** — you are usually mid-conversation, so speakable replies come before explanations.
- **One ⭐ recommendation** chosen by an explicit 5-step politeness rule (`skill/references/politeness.md`).
- **【】 marks your personal facts** — the model never invents your experiences.
- **⚠ warning** on the casual reply when the other person is your boss, teacher, client, interviewer or a clerk.
- Every kanji word carries its reading as `漢字(かな)`, which also converts losslessly to Anki furigana.

### Install

**Claude.ai / Claude desktop app** (Pro, Max, Team or Enterprise plan with code execution enabled):

```bash
python tools/package_skill.py
```

Then upload `dist/japanese-speaking-coach.zip` in **Settings → Features → Skills**.

**Claude Code**: copy the `skill/` folder to your personal skills directory and name it after the skill:

```bash
cp -r skill ~/.claude/skills/japanese-speaking-coach
```

(On Windows PowerShell: `Copy-Item -Recurse skill $HOME\.claude\skills\japanese-speaking-coach`.)

### Export vocabulary to Anki

Save the review (复盘) output to a file, then:

```bash
python skill/scripts/export_anki.py review.md -o cards.txt --deck "日本語::会話"
```

Import `cards.txt` in Anki (File → Import). The default layout targets Anki's built-in **Basic** note type; use `--layout fields` for a 4-field custom note type. Readings in example sentences are converted to Anki's `漢字[かな]` furigana syntax. Only the Python standard library is required (Python 3.9+).

### Project layout

```
skill/                    # the Skill itself (this is what gets installed)
  SKILL.md                #   roles, modes, output format, rules
  references/             #   politeness rules, 8 role-play scenes
  scripts/export_anki.py  #   review CSV -> Anki import file
tests/                    # test sentences, format checker, review prompt, test logs
docs/design.md            # design overview
docs/decisions.md         # decision log (why each design choice was made)
tools/package_skill.py    # builds the upload zip
app/jsc_live/             # local app: real-time Japanese subtitles with furigana (see app/README.md)
```

### How it was tested

- **20 test sentences** (shop clerks, workplace, friends, edge cases) with an expected ⭐ for each; scored by `tests/check_format.py` plus a blind review of the Japanese by a stronger model (`tests/review_prompt.md`).
- **Role-play**: each scene was run with two independent agents — the coach (this skill) and a simulated N3 student with planted mistakes — to check that tips fire on real mistakes only and that scenes finish.
- CPU unit tests: install `requirements-dev.txt`, then run `python -m unittest discover -s tests`. See [development and packaging instructions](docs/development.md).
- Every rule change and its reason is recorded in `docs/decisions.md`.

Known limits: the "users" in tests are simulated; native-speaker review and real learner trials are still in progress.

### Roadmap

1. **This repo (phase 1)** — the Skill.
2. **Prototype done** — real-time Japanese subtitles with furigana from the microphone or system audio, running locally on the GPU (faster-whisper + kotoba-whisper, MeCab readings). See [`app/README.md`](app/README.md).
3. Next — suggestions for what to say next, based on the live transcript.

---

## 日本語

### できること

| モード | 入力 | 出力 |
|---|---|---|
| **A. 返答の提案**（デフォルト） | 相手に言われた日本語 | ふりがな付きの原文・中国語の意味・ポイント、そして **3種類の返答**（丁寧／カジュアル／かんたん）。おすすめは必ず1つ（⭐） |
| **B. 言いたいことを日本語に** | 中国語で「我想说……」 | 同じ3種類の返答 |
| **C. ロールプレイ** | 「陪练」＋場面 | コンビニ、バイト面接、病院、不動産屋、就活面接など8場面でAIが相手役を演じる。誤解や失礼につながる返答のときだけ 💡 でヒント |
| **D. 振り返り** | 「复盘」 | 添削・よかった点・**Anki** に取り込める単語カード（CSV） |

説明文は学習者の母語である中国語、口に出す部分だけ日本語、という設計です。

### インストール

**Claude.ai / Claudeデスクトップアプリ**（Pro・Max・Team・Enterpriseプラン、コード実行が有効なこと）：

```bash
python tools/package_skill.py
```

生成された `dist/japanese-speaking-coach.zip` を **設定 → Features → Skills** からアップロードします。

**Claude Code**：`skill/` フォルダを個人用スキルディレクトリにコピーします。

```bash
cp -r skill ~/.claude/skills/japanese-speaking-coach
```

### Ankiへの書き出し

```bash
python skill/scripts/export_anki.py review.md -o cards.txt --deck "日本語::会話"
```

Ankiの「ファイル → 読み込む」で `cards.txt` を取り込みます。標準の「基本」ノートタイプに対応し、例文のふりがなは `漢字[かな]` 形式に変換されます。

### テストについて

- 20のテスト文（店員、職場、友人、境界ケース）で、形式の自動チェック（`tests/check_format.py`）と、より強いモデルによる日本語のブラインドレビューを実施。
- ロールプレイは「コーチ（本スキル）」と「N3レベルの学習者役（わざと間違いを含む）」を別々のエージェントで動かして検証。
- 設計判断とその理由はすべて `docs/decisions.md` に記録しています。

---

## 中文简介

一个 Claude Skill：粘贴别人对你说的日语，立刻得到带假名的原文、中文意思、要点，以及 **丁寧 / カジュアル / かんたん** 三种回复（⭐ 标出最推荐的一条）。
还能用中文说「我想说……」转成日语、在 8 个生活情景里陪练（便利店、打工面试、医院、找房子、求职面试等），练完输入「复盘」获得纠错和可导入 Anki 的生词卡。

安装方法见上方 [Install](#install)；设计思路见 `docs/design.md`，每个设计决策的理由见 `docs/decisions.md`。

---

## License

[MIT](LICENSE) © 2026 CHEN YI AN
