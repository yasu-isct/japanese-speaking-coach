# jsc_live — 实时日语字幕（带假名）

麦克风或电脑正在播放的声音 → 实时日语识别 → 带假名注音的置顶字幕窗。全部在本机运行，不联网、不需要 API Key。

```
音频采集 (soundcard: 麦克风 / WASAPI 回采)
  → 分句 (能量 VAD，自适应噪声底)
  → 识别 (faster-whisper + kotoba-whisper-v2.0，GPU float16)
  → 注音 (MeCab/fugashi + UniDic 读音，按 N5–N4 基础汉字表分级)
  → 字幕窗 (tkinter Canvas 手动排 ruby)
```

## 安装

```bash
conda activate jsc
pip install -r app/requirements.txt
```

第一次运行会自动下载识别模型（约 1.5 GB，存到 Hugging Face 缓存）。

## 使用

在 `app` 目录下运行：

```bash
python -m jsc_live --source loopback
```

| 参数 | 说明 |
|---|---|
| `--source loopback` | 默认。字幕显示电脑正在播放的声音（视频、网课、通话） |
| `--source mic` | 字幕显示麦克风的声音 |
| `--source file --file a.wav --realtime` | 用 WAV 文件模拟实时输入 |
| `--device 名称` | 指定麦克风/扬声器，名称用 `--list-devices` 查 |
| `--furigana graded/all/none` | 分级注音（默认，N5–N4 汉字不注）/ 全部注音 / 不注音 |
| `--no-partial` | 只显示说完的句子，不显示边说边出的灰色字幕 |
| `--no-window` | 不开窗口，只在控制台输出 |
| `--end-silence 0.6` | 停顿多少秒算一句话结束 |

字幕窗：拖动移动位置，滚轮调字号，右键或 Esc 退出。白字是说完的句子，灰字是正在说的句子。

## 接话建议（第二阶段第一刀）

字幕下方显示 3 条可以直接念的回复（丁寧 / カジュアル / かんたん，⭐ = 推荐），规则来自 `skill/SKILL.md` 的 A 模式。需要一个 OpenAI 兼容的 API（例如 DeepSeek），在项目根目录建 `.env`：

```
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=模型名
```

```bash
python -m jsc_live --source mic --suggest --partner 上司
```

- `--partner`：对方是谁（上司 / 老师 / 店员 / 朋友 …），影响 ⭐ 和 ⚠；不写时按对方的说法判断。
- `--suggest-timeout 3`：几秒没到算超时。超时显示「タイムアウト」，之后到达的建议仍会显示并标「遅延」。
- 发送给 API 的只有文字：识别出的这句 + 前 3 句 + 对象标记。不发音频。
- 每句的耗时写在 `logs/live-*.jsonl`（不提交 Git），控制台也会打印一行 `[suggest #n] asr … | api … | render …`。

## 真人测试（请你来做）

目的：看「对真人说话 / 真实视频」时字幕和建议是否能用。结果决定下一刀做什么，在这之前不加新功能。

**启动**（在 `app` 目录，`conda activate jsc` 之后）：

```bash
python -m jsc_live --source mic --suggest
```

```bash
python -m jsc_live --source loopback --suggest
```

第一条：你自己对着麦克风说（或请朋友说）。第二条：放一段日语视频（日剧、YouTube 的日常对话、网课都可以）。各跑 3–5 分钟即可。

**看什么**（记下印象最深的 2–3 个例子就够，不用全记）：

1. **字幕**：说完一句后多久出来？有没有一句被切成两半、或两句粘在一起？错字多不多？
2. **建议出现的时间**：大多在 3 秒内吗？「タイムアウト」/「遅延」出现得多吗？
3. **建议能不能念**：⭐ 那条你敢直接说出口吗？有没有明显不自然或礼貌程度不对的？
4. **跟不跟得上**：对方连续说好几句时，建议是不是总是落后、来不及看？
5. **最想先改的一件事**是什么？

结束后把 `logs/` 里最新的 `live-*.jsonl` 的路径告诉我（里面有你说过的话，所以不提交 Git，我只在本地读），再加上面 5 条的印象。

## 实测（RTX 5060 Laptop 8 GB）

| 测试 | 结果 |
|---|---|
| 一句话说完到字幕出现 | 约 0.6 秒停顿判定 + 0.22–0.29 秒识别 |
| 20.8 秒、5 句的对话 | 5 句分句正确；识别错误 1 处（十時 → 届き，TTS 合成音） |
| 系统音频回采 | 与文件输入结果一致 |

单元测试：`python -m unittest discover -s tests`（分句、注音对齐、分级规则，不需要显卡）。

## 已知限制

- 能量 VAD 遇到背景音乐、多人同时说话时分句会不准。
- 识别模型偶尔会把读音相近的词认错；注音跟随识别结果，认错的词注音也会是错词的读音。
- 只在 Windows 上测试过（回采依赖 WASAPI）。
