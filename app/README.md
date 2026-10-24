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
