# 第一阶段进度（2026-10-24）

## 完成标准对照

| # | 完成标准 | 状态 | 依据 |
|---|---|---|---|
| 1 | 20 句测试句，回复建议格式全部稳定 | ✅ 达成 | v6 整体回归：格式 20/20、⭐ 20/20、⚠ 20/20，日语硬性问题 0（`tests/sample_dialogues.md` 第 7 轮） |
| 2 | 至少 6 个情景能完整走完一轮陪练 | ✅ 达成 | 8/8 情景走完并正常收尾；v6 下重跑的 4 个全部在 8 轮内结束（`tests/roleplay_2026-10-24.md`） |
| 3 | 2–3 个朋友试用并收集反馈 | ⏳ 待你执行 | 需要真人。试用指南和反馈表已备好：`docs/trial_guide.md`；安装包：`python tools/package_skill.py` → `dist/japanese-speaking-coach.zip` |
| 4 | 发布到 GitHub，README 含安装方法和效果截图 | 🟡 只差截图 | 已发布到公开仓库 yasu-isct/japanese-speaking-coach（MIT）；README（英/日/中）含安装与 Anki 导出说明。**未做**：效果截图/GIF（需要在 Claude 里实际运行后截图） |

## 已完成的产出

| 类别 | 文件 |
|---|---|
| Skill 本体 | `skill/SKILL.md`（4 种模式）、`skill/references/politeness.md`、`skill/references/scenarios.md`（8 个情景）、`skill/scripts/export_anki.py` |
| 工具 | `tests/check_format.py`（自动格式检查）、`tools/package_skill.py`（打包上传用 zip） |
| 测试 | 28 个单元测试（`python -m unittest discover -s tests`，全部通过）；A 模式 7 轮测试记录；C 模式 2 轮陪练记录；B、D 模式抽查 |
| 文档 | `README.md`、`docs/design.md`、`docs/decisions.md`（D-001〜D-009）、`docs/trial_guide.md` |

## 还差什么（按优先级）

1. **朋友试用（标准 3）**：把 zip 和 `docs/trial_guide.md` 发给 2–3 个朋友，反馈存到 `tests/feedback/`。最好有一位日语母语者帮忙看日语——目前日语正确性只经过模型审查。
2. **截图/GIF（标准 4）**：在 Claude 里装好 Skill，各截一张 A 模式回复建议、C 模式陪练、D 模式复盘的图，放到 `docs/images/`，替换 README 顶部的「coming soon」。
3. **推送到 GitHub（标准 4）**：已完成——公开仓库 `yasu-isct/japanese-speaking-coach`，MIT 许可证。
4. **用真实句子替换测试句**：CLAUDE.md 要求「20 句真实对话测试句」，现在是起草的覆盖性样本，试用时收集的真实句子可以替换进去。

## 已知的小问题（下一轮改进）

- **反问/寒暄不总是对对方成立**：如对日本人反问「〇〇さんは？（来日本多久）」、两人同在一处时问「そっちは？」、对方先说「お疲れ様です」时没先回一句。审查员两轮都指出过，可规则化为「反问和寒暄要对对方也成立」，改完需再回归一轮。
- **陪练偶尔一轮说 3 句**：约 30 轮里 2 轮，都在收尾前转场处。
- **かんたん 的措辞偶尔生硬**（「はい、待ちます」等），属软性问题。
- **S3、S8 情景**是旧规则（v5）下跑的（9 轮收尾），没有在 v6 下重跑。

## 测试方法的局限

- 测试中的「用户」「学生」都是模型扮演，真实输入会更乱。
- 生成用 Sonnet、审查用 Opus；真实使用时 Claude.ai 的默认模型和 Claude Code 子代理的系统提示词都可能不同。
- 每个情景、每句话大多只跑了 1–2 次，偶发问题可能没暴露。

---

# 第二阶段：实时字幕 + 接话建议（`app/jsc_live`）

| 项目 | 状态 |
|---|---|
| 麦克风 / 系统音频采集 | ✅ 两种都实测通过（回采用 WASAPI） |
| 实时日语识别（GPU） | ✅ kotoba-whisper-v2.0 + faster-whisper，每句约 0.25 s |
| 带假名的字幕（分级注音） | ✅ MeCab 词级读音 + 对齐，复用基础汉字表 |
| 置顶字幕窗 | ✅ 可拖动、滚轮调字号、显示临时结果 |
| 测试 | ✅ 共 65 个单元测试通过（含建议解析、流式拼行、乱序/漏栏、网络错误） |
| 用真人说话 / 真实视频测试 | ⏳ 需要你在自己电脑上跑一次（见 `app/README.md`） |
| 接话建议（第一刀：API 生成 3 条回复，显示在字幕下方） | ✅ TTS 验收通过：deepseek-flash（关思考），显示出的 11 句全部在 3 秒内（字幕→建议中位数 1.69 s，最大 1.84 s）；⭐ 风格与测试集预期一致 9/11（#2 店员句 ⭐ かんたん、#11 朋友邀约 ⭐ 丁寧；#9 风格对但内容串到上文的排班），详见 `tests/live_suggest/e2e-20261024-223748.md` |
| 延迟分三段记录（识别 / API 往返 / 渲染） | ✅ `logs/live-*.jsonl`，每句一行 |
| 真人测试（麦克风 / 真实视频） | ⏳ 等你执行，清单见 `app/README.md`「真人测试」；结果决定下一刀 |
