# docs/images — 画面素材

すべて **TTS 音声による再生・個人情報なし**。新旧の画面と測定条件は以下のとおり。

## 2026-10-26：外部文書を使う返答

`live-reference-answer.png`（900×419）と `live-reference-manual.png`（900×444）は、D-024の4問TTS実行 `tests/live_suggest/e2e-references-20261026-171615.jsonl` を現在のUI（アプリ `ed12b8f`）へ再表示した画面。架空の授業案内・録音機マニュアルから回答し、理由にファイル名と行番号を示す。

`tools/capture_showcase.py --render-saved --materials tests/fixtures/materials/general/data` で再現可能。ウィンドウは全モニターの外側に配置し、入力透過・非アクティブを維持して自身のHWNDだけをPrintWindowで描画。デスクトップのピクセルは取得せず、APIも呼ばない。画像は無加工。これは保存済みTTS結果のUI再描画であり、新たなリアルタイム速度測定ではない。来歴とハッシュ：`tests/live_suggest/showcase-replay-20261026-200255.json`。

`live-materials.png`（820×600）はD-027の資料管理窓。上と同じ公開TTS回答の参考片段を表示し、明示した公開fixtureの索引を読み込んだ画面。自身のHWNDを画面外で描画し、個人資料や新しいAPI応答は使っていない。ソースと画像のSHA-256：`tests/live_suggest/material-ui-20261026-201649.json`。改前後のチェックは `docs/ui-materials-20261026.md`。

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
