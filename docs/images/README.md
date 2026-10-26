# docs/images — 画面素材

すべて **公開の架空データまたはTTS音声による再生・個人情報なし**。新旧の画面と測定条件は以下のとおり。

## 2026-10-26：外部文書を使う返答

`live-reference-answer.png`（900×419）と `live-reference-manual.png`（900×444）は、D-024の4問TTS実行 `tests/live_suggest/e2e-references-20261026-171615.jsonl` を現在のUI（アプリ `ed12b8f`）へ再表示した画面。架空の授業案内・録音機マニュアルから回答し、理由にファイル名と行番号を示す。

`tools/capture_showcase.py --render-saved --materials tests/fixtures/materials/general/data` で再現可能。ウィンドウは全モニターの外側に配置し、入力透過・非アクティブを維持して自身のHWNDだけをPrintWindowで描画。デスクトップのピクセルは取得せず、APIも呼ばない。画像は無加工。これは保存済みTTS結果のUI再描画であり、新たなリアルタイム速度測定ではない。来歴とハッシュ：`tests/live_suggest/showcase-replay-20261026-200255.json`。

`live-materials.png`（820×600）はD-027の資料管理窓。上と同じ公開TTS回答の参考片段を表示し、明示した公開fixtureの索引を読み込んだ画面。自身のHWNDを画面外で描画し、個人資料や新しいAPI応答は使っていない。ソースと画像のSHA-256：`tests/live_suggest/material-ui-20261026-201649.json`。改前後のチェックは `docs/ui-materials-20261026.md`。

## 2026-10-26：会話アーカイブの選択と復習

`live-review-picker.png`（780×570）はD-028の復習窓。公開fixture `tests/fixtures/session_review/` の2字幕・2回答・2カードを実際にバックグラウンドで書き出した。音声を含まない架空データであり、TTSやAPIを実行していない。画面外の自身のHWNDだけを捕獲、無加工。来歴とSHA：`tests/live_suggest/review-ui-20261026-203339.json`。

## 2026-10-26：任意のローカル多言語検索

`live-materials-semantic.png`（820×600）はD-030の有効状態。公開資料の索引を本機モデルで作り、本文は以前のTTS出典スナップショットを再表示している。画面外の自身のHWNDのみ捕獲し、前台を変えていない。新たな生成回答の画面ではない。SHAと来歴：`tests/live_suggest/semantic-stage-summary-20261026.json`。

## 2026-10-26：本機モデルの準備と切替

`live-model-settings.png`（640×520）と `live-materials-controls.png`（820×600）はD-031。公開資料・実際の本機モデル・一時的な設定ファイルで、切替と保存を確認した。設定窓には生成回答を含まない。資料窓の本文は保存済みTTS出典の再表示。全画面外、自身HWNDのみ、無加工。SHAと来歴：`tests/live_suggest/model-controls-20261026.json`。

## 2026-10-25：以前の画面と測定

以下の旧素材は `tests/sample_dialogues.md` の10文をWindows Harukaで合成し、アプリのファイル入力で再生したもの。個人プロフィールは公開例のプレースホルダー。旧撮影スクリプトは現在の安全な再描画方式へ置き換え済み。

撮影：2026-10-25、アプリのコードはコミット `3ba4bea`（D-020）の状態、撮影スクリプトは `tools/capture_showcase.py` / `tools/capture_archive.py`。1× 等倍、ウィンドウの枠は切っていない。数値は同じ実行のログから算出（`tests/live_suggest/showcase-20261025-1825.json`）。

| ファイル | 説明 | サイズ |
|---|---|---|
| `live-subtitle-suggest.png` | 字幕（難しい漢字にだけふりがな）と返答候補 3 つ。⭐ は推奨 1 つ、カジュアルには店員相手なので ⚠「对此对象不要用」。 | 900×565 |
| `live-history-lookup.png` | 字幕履歴ウィンドウで「可能」を選択した直後：読み【かのう】と、この文脈での中国語の意味（DeepSeek）。 | 424×1007 |
| `live-archive.png` | 録音版（`jsc-live-录音.bat`）を閉じたあとに自動で開く保存フォルダ。**左のナビゲーション欄は個人情報（アカウント名・個人フォルダ）が写るため塗りつぶした**。ファイル名はアプリが付ける汎用名のみ。 | 1262×711 |
| `live-demo.webm` | 26 秒・10 fps・無音。「聞き取り中」から字幕 3 文と返答候補が出るまで。録画中は字幕ウィンドウの背後に無地の黒い板を置き、デスクトップの他の内容が写らないようにした（アプリの表示自体は無加工）。 | 900×760, 264 KB |

字幕の誤認識も含め、実行結果はそのまま。

`live-materials-docx.png`（820×600）はD-032。公開架空Wordの検索片段と引用状態を合成表示した画面で、生成APIは使用していない。改前/後/窄窓は画面外の自身HWNDのみ、無加工、前台不変。出典は本文段落/表行で、Wordページ番号ではない。SHAと来歴：`tests/live_suggest/docx-stage-20261026.json`。

`live-materials-pdf.png`（820×600）はD-033。公開架空PDFから本機で抽出した文字と物理ページ番号を、合成引用状態として表示。生成APIなし、画面外自身HWNDのみ、無加工。SHAと来歴：`tests/live_suggest/pdf-stage-20261026.json`。


`live-materials-progress.png`（820×600）はD-034。固定100份架空講義の読み込み進捗を16/100で一時停止したレイアウト検証用画面。生成APIなし、画面外自身HWNDのみ、無加工、前台不変。速度の証拠ではない。SHAと来歴：`tests/live_suggest/materials-scale-stage-20261026.json`。


`live-materials-startup.png`（900×149）はD-035。100份架空資料で、キーワードが使える状態から多言語索引を準備する実際のUI状態。音声/ASR/APIは隔離し、字幕部分は合成起動テスト文字。画面外自身HWNDのみ、前台不変、無加工。全アプリの起動時間や可視速度の証拠ではない。SHAと来歴：`tests/live_suggest/materials-startup-stage-20261026.json`。


`live-materials-long-pdf.png`（820×600）はD-036。固定架空PDFの音声課題に関する実際のローカル検索と、合成引用状態。物理ページと今回の抽出ブロック番号を表示する。画面外自身HWNDのみ、前台不変、無加工。生成API・真人体験・可視速度の証拠ではない。SHAと来歴：`tests/live_suggest/long-pdf-stage-20261026.json`。
