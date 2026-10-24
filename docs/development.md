# 开发与检查

## 单元测试（CPU）

在仓库根目录，用 Python 3.12 建立独立环境。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

macOS / Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests
```

`requirements-dev.txt` 只安装 NumPy（分句、重采样测试）和 fugashi + unidic-lite（注音测试）。导出 Anki、格式检查和打包工具只用 Python 标准库。测试不需要显卡、麦克风、音频设备或 API Key，不下载语音识别模型；不要为测试安装 `app/requirements.txt` 中的完整应用依赖。

当前 `tests/test_live.py` 只检查 CPU 逻辑，没有 GPU 或语音识别模型测试。已有的 `skipUnless` 只在缺少 fugashi 时跳过 `FuriganaTest` 的 5 个注音测试，其余测试继续执行。CI 安装完整的开发依赖后运行所有单元测试。

## 生成 Skill 上传包

在仓库根目录运行（使用上述环境中的 Python 也可以）：

```bash
python tools/package_skill.py
```

工具没有命令行参数，输出路径固定为 `dist/japanese-speaking-coach.zip`，重复执行会重新生成该文件。它递归读取 `skill/`，只排除 `__pycache__/` 和 `.pyc`，不会读取 `app/`、`tests/` 或 `docs/`。目前包内应只有以下 6 个发布文件：

```text
japanese-speaking-coach/LICENSE.txt
japanese-speaking-coach/SKILL.md
japanese-speaking-coach/references/basic_kanji.md
japanese-speaking-coach/references/politeness.md
japanese-speaking-coach/references/scenarios.md
japanese-speaking-coach/scripts/export_anki.py
```

上传前可用标准库列出包内文件：

```bash
python -m zipfile -l dist/japanese-speaking-coach.zip
python -m zipfile -t dist/japanese-speaking-coach.zip
```

`skill/` 是发布目录，请只放需要随 Skill 分发的文件。日志、录音、模型和凭证不要放进该目录；打包工具并不会自动过滤这些文件。
