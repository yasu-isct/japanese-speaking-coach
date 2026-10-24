# jsc_live — 实时日语字幕 + 接话建议

开着它和日本人说话（或看日语视频）时，屏幕下方会出现一个小窗口：

- **上半部分是字幕**：对方说的日语，难的汉字上面标假名；
- **下半部分是「怎么接话」**：对方说完一段话、停下来之后，大约 2.5 秒内给出 3 种可以直接念出来的回复：
  - 丁寧：礼貌版，对上司、老师、店员说；
  - カジュアル：朋友版；
  - かんたん：紧张时的保底版。
  
  标 ⭐ 的是推荐的那条；标 ⚠ 的表示「这条别对这个人说」。

  对方连续说好几句时，建议会等他停下来，再针对这一整段给出；中途不会每句都刷新。字幕区保留最近 5 句，可以回看。

![运行截图](../docs/images/live-suggest.png)

## 怎么用

### 第一次使用前（只做一次）

1. 确认装过依赖（已经装好的话跳过）：在 Anaconda Prompt 里运行
   ```
   conda activate jsc
   pip install -r D:\ClaudeProject\japanese-speaking-coach\app\requirements.txt
   ```
2. 想要「接话建议」的话，在 `japanese-speaking-coach` 文件夹里放一个名字**正好叫 `.env`** 的文件（不是 `.env.txt`。记事本保存时要把「保存类型」选成「所有文件」），内容是三行：
   ```
   LLM_API_KEY=你的 DeepSeek key
   LLM_BASE_URL=https://api.deepseek.com
   LLM_MODEL=deepseek-flash
   ```
   不放这个文件也能用字幕，只是没有建议。

### 每次使用

**双击 `japanese-speaking-coach` 文件夹里的 `jsc-live.bat`。**

默认是「给电脑正在播放的声音配字幕 + 建议」，适合看视频、网课、视频通话。

第一次启动要等十几秒加载识别模型，窗口里会显示「モデル読み込み中…」，出现「聞き取り中」就可以开始了。

想识别**麦克风**（面对面说话）时，在 PowerShell 里运行：

```powershell
D:\ClaudeProject\japanese-speaking-coach\jsc-live.bat --source mic --suggest
```

知道对方是谁时，可以告诉它，⭐ 会更准：

```powershell
D:\ClaudeProject\japanese-speaking-coach\jsc-live.bat --source mic --suggest --partner 上司
```

`--partner` 后面可以写 上司 / 老师 / 店员 / 朋友 等。

### 窗口操作

- 拖动：移动窗口
- 鼠标滚轮：调字号
- 右键或 Esc：退出
- 白字是说完的句子（保留最近 5 句），灰字是正在说、还没说完的句子（说完会更正）。
- 建议区显示「…」：正在生成。
- 显示「タイムアウト」：3 秒内没生成出来；之后再到的建议会标「遅延」。

### 没反应怎么办

- **直接打 `python ...` 没反应**：那个 `python` 是 Windows 自带的占位程序，不是装好依赖的 Python。请用上面的 `jsc-live.bat`。
- **双击后黑窗口一闪就关**：一般是出错了。脚本出错时会停住等你按键，把那段报错截图给我。
- **字幕出来了但没有建议**：检查 `.env` 的文件名和三行内容。

## 请你帮忙做的测试：真人说话 / 真实视频

我目前只用电脑合成的声音测试过。下一步该改什么，要看真人说话时好不好用。

**做法**：

1. 双击 `jsc-live.bat`，放一段日语视频（日剧、YouTube 的日常对话都行），看 3–5 分钟。
2. 用上面麦克风那条命令，自己对着麦克风说，或者请朋友说，3–5 分钟。

**留意这几点**（记下印象最深的 2–3 个例子就够）：

1. **字幕**：说完一句后多久出来？有没有一句被切成两半、或两句粘在一起？错字多不多？
2. **建议出来得快不快**：大多在 3 秒内吗？「タイムアウト」/「遅延」多吗？
3. **建议能不能用**：⭐ 那条你敢直接说出口吗？有没有不自然、或礼貌程度不对的？
4. **跟不跟得上**：对方连着说好几句时，建议是不是总慢一拍、来不及看？
5. **最想先改的一件事**是什么？

**做完后**告诉我两样东西：

- 上面几点的印象；
- `japanese-speaking-coach\logs\` 里最新那个 `live-…jsonl` 文件。里面记录了每句识别出的文字、建议和耗时。因为有你说过的话，它不会被提交到 GitHub，我只在你电脑上读。

---

## 给开发者看的部分

### 处理流程

```
音频采集 (soundcard: 麦克风 / WASAPI 回采)
  → 分句 (能量 VAD，自适应噪声底)
  → 识别 (faster-whisper + kotoba-whisper-v2.0，GPU float16)
  → 注音 (MeCab/fugashi + UniDic 读音，按 N5–N4 基础汉字表分级)
  → 字幕窗 (tkinter Canvas 手动排 ruby)
  → 接话建议 (OpenAI 兼容 API，规则复用 skill/SKILL.md 的 A 模式；后台线程，不阻塞字幕)
```

- 识别在本机显卡上跑，音频不出电脑。接话建议要联网，只发送文字：识别出的这句、前 3 句和 `--partner`。
- DeepSeek 默认关闭「思考」模式（开着首字要多等约 2 秒）；用别的服务时，可以在 `.env` 加一行 `LLM_EXTRA_BODY={...}`（JSON，原样并入请求）。
- 每句耗时分三段记录在 `logs/live-*.jsonl`：识别、API 往返、渲染。控制台也会打印 `[suggest #n] asr … | api … | render …`。

### 全部参数

直接用 Python 运行时：先 `conda activate jsc`，再在 `app` 目录下运行 `python -m jsc_live …`。`jsc-live.bat` 会把参数原样传过去。

| 参数 | 说明 |
|---|---|
| `--source loopback` | 电脑正在播放的声音（不写参数时的默认） |
| `--source mic` | 麦克风 |
| `--source file --file a.wav --realtime` | 用 WAV 文件模拟实时输入 |
| `--suggest` | 打开接话建议（需要 `.env`） |
| `--partner 上司` | 对方是谁，影响 ⭐ 和 ⚠ |
| `--suggest-timeout 3` | 几秒没到算超时 |
| `--turn-gap 0.8` | 对方停下来后再等几秒才出建议（在 0.6 秒分句停顿之外）；调小更快，调大更不容易把一段话切开 |
| `--history 5` | 字幕区保留几句 |
| `--font-size 16` | 字号（也可以在窗口上用滚轮调） |
| `--device 名称` | 指定麦克风/扬声器，名称用 `--list-devices` 查 |
| `--furigana graded/all/none` | 分级注音（默认，N5–N4 汉字不注）/ 全部注音 / 不注音 |
| `--no-partial` | 只显示说完的句子 |
| `--no-window` | 不开窗口，只在控制台输出 |
| `--end-silence 0.6` | 停顿多少秒算一句话结束 |

### 实测（RTX 5060 Laptop 8 GB，合成语音）

| 测试 | 结果 |
|---|---|
| 一句话说完到字幕出现 | 约 0.6 秒停顿判定 + 0.22–0.29 秒识别 |
| 20.8 秒、5 句的对话 | 5 句分句正确；识别错误 1 处（十時 → 届き） |
| 系统声音回采 | 与文件输入结果一致 |
| 接话建议，一问一答 10 句（deepseek-flash） | 最后一句字幕 → 建议显示：中位数 2.43 s，最大 2.63 s；其中等待停顿 0.82 s、API 往返 1.55 s（首字 0.85 s）、渲染 72 ms（`tests/live_suggest/e2e-dialogue-*`） |
| 接话建议，独白 3 段 11 个片段 | 只请求 3 次，每次回应整段；2.49 / 2.65 / 3.09 s（`tests/live_suggest/e2e-monologue-*`） |

单元测试：在 `japanese-speaking-coach` 目录下运行 `python -m unittest discover -s tests`，不需要显卡和网络。

### 已知限制

- 有背景音乐、多人同时说话时，分句会不准。
- 识别偶尔会把读音相近的词认错；注音跟着识别结果走，认错的词，注音也是错词的读音。
- ⭐ 偶尔不按规则选；上文有时会串到下一句的建议里（详见 `docs/decisions.md` D-012）。
- 只在 Windows 上测试过（系统声音回采依赖 WASAPI）。
