# 可选的本地多语言资料查找

这项模式帮助“问法不同、语言不同”时找回资料。实际回答仍由原来的API生成，并显示当时提供的文件与行号。不会自动开启，不会扫描私人存档，也不会把全部资料上传。

## 准备与启动

模型来自[Sentence Transformers官方仓库](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)，Apache-2.0；固定版本 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`。只需下面两个文件，保持文件夹结构，总计约127.54MB：

- [onnx/model_quint8_avx2.onnx](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/onnx/model_quint8_avx2.onnx)：放在模型目录的 `onnx` 子文件夹。
- [tokenizer.json](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/tokenizer.json)：放在模型目录。

应用会检查文件大小和官方SHA-256，不接受其它权重。现有应用依赖带有onnxruntime、tokenizers及numpy，无需安装PyTorch或新的界面框架。当前模型在CPU上运行，使用两线程。

```powershell
.\jsc-live.bat --suggest --materials "D:\我的资料" --semantic-model "D:\我的模型\multilingual-minilm"
```

本机开发副本已放在仓库忽略目录 `logs/goal/models/multilingual-minilm/`，作者可用这个完整路径试用，无需再下载。模型权重不在Git提交中。

模型首次加载约1.3秒（本机D-029测量）。之后选择或刷新资料会重新建索引，复用同一模型；当前回答仍保留原出处。模型缺失或损坏会回到关键词查找，推理失败也保留关键词功能；刷新失败保留整个旧索引并给出提示。检索开关尚未保存，普通双击启动仍用关键词模式。

## 本轮验证（D-030）

新增一组公开虚构文档：图书室、食堂、项目说明、设备借用、活动记录。先冻结17问，其中13问有目标片段；旧关键词找回0/13，D-029原型11/13。检查后发现长段落尾部被截断，以及分数差规则排除了补充条件。当前改为覆盖全文的重叠小窗口，同一原片段只发送一次；保留最多3个语义候选，再与关键词结果合并，总上限仍4片段/1800正文字符。

| 固定数据 | 原关键词找回 | 当前可选模式找回 | 当前无所问信息时空返回 |
|---|---:|---:|---:|
| 原通用资料组 | 7/7 | 7/7 | 1/3 |
| 自然问法组 | 5/16 | 16/16 | 2/4 |
| 新增文档组 | 0/13 | 13/13 | 1/4 |

这是47个固定问题的片段覆盖检查，新增组在检查后参与了修复，不是未见过的盲测。相似度不能判断资料是否足够回答。新组单次查询p50/p95为3.58/4.97ms；无API、无私人数据。

集中问答阶段只用6个公开问题：3个有信息、3个缺信息。按开发代理审查，推荐回复覆盖预期事实从旧关键词1/3到当前3/3，含长段落尾部的失物保管规则。资料缺失时直接说明未记载的最终结果为2/3；密码样例仍说“不能告知”，没有正确表达资料缺失，保留为失败。图书延期的简易回复还漏了“到期前”条件，因此不能说全部18条回复均完整正确，也没有真人日语审阅。

四段公开TTS完整链路：4/4完成、4/4预取命中；字幕到**隐藏Canvas完成**p50为2522.3ms、p95为2752.5ms。历史同4段运行p95约2730ms；是两次小样本，不用于归因22ms差异。当前实际可见3秒目标仍未通过，不能把后台数据替代真人体验。

完整测试230项（229通过、1项Windows权限跳过）。最终仅补了刷新错误恢复与诊断状态，之后离线核对47份检索载荷、6份问答来源与已测版本一致，没有再次调用API。

## 证据与重现

- 新组改前：`tests/live_suggest/semantic-independent-before-20261026-204803.json`。
- 当前检索：`tests/live_suggest/semantic-runtime-20261026-205933.json`。
- 阶段问答：`semantic-answers-20261026-205438.json`（12次对照）、`semantic-answers-20261026-205556.json`（规则修正后6次候选）。
- TTS：`e2e-references-20261026-205704.jsonl`及同名Markdown和run.json。
- 汇总/最后校验/图片来历：`semantic-stage-summary-20261026.json`。以上均在 `tests/live_suggest/`。

```powershell
python tools/eval_semantic_materials.py --model-dir logs/goal/models/multilingual-minilm
```

这条命令只读固定公开语料、只在本机检索，不调用API。付费问答验收工具默认不执行，需显式 `--run-api` 并先核对每日预算。下一步仍需更大资料库、首次准备和开关的窗口入口、PDF/Word支持、真实课堂阅读与可见延迟验收。
