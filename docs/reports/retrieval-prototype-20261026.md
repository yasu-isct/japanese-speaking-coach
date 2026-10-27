# 通用资料自然问法：本机语义检索原型

本页保留D-029时的原型对照。D-030已接入可选应用模式，当前用法和后续阶段验收见 [本地多语言资料查找](../local-semantic-materials.md)；默认仍用关键词。

这一步只研究外部资料的检索，应用仍使用D-024的关键词/BM25。没有改个人事实规则，没有调用生成API，也没有读取私人存档。

## 先冻结的输入

资料沿用公开虚构的课程、会议、设备手册、学习文章，4文件16片段；原10问保留不动。另写20个自然问法：16问有对应答案，4问没有所问信息，包括“同主题、所问属性缺失”。先保存文件哈希和旧版结果，再运行新原型。新增问题是作者编写的诊断集，不能代表通用准确率，也不是独立的盲测。

- 固定问题/哈希：`tests/fixtures/materials/general-followup/`。
- 改前：`tests/live_suggest/general-followup-baseline-20261026-203827.json`，应用187b7b5。
- 原型：`tests/live_suggest/semantic-probe-20261026-204138.json`。

## 模型与实现

使用Sentence Transformers官方的[paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)，Apache-2.0；[官方ONNX文件](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/onnx)中的AVX2量化版本118,453,870字节，tokenizer 9,081,518字节，总下载约127.54MB。固定版本 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，校验官方LFS SHA-256。只下载这些权重和小型配置，未下载仓库全部模型。

本机已有onnxruntime 1.31.0、tokenizers 0.23.3和numpy；未安装新依赖、PyTorch或transformers。CPU限制两线程，按官方mean pooling做带attention mask的平均后归一化，384维，128 token上限，分批4片段。模型文件在忽略目录 `logs/goal/models/multilingual-minilm/`，不提交。应用没有自动下载或加载行为。

检索条件在看分数前固定：余弦至少0.35、距离最佳分数不超过0.15，最多4片段；混合候选使用RRF（k=60，语义权重1，词法0.5）。复用现有1800字符发送预算与来源快照校验，没有放大模型上下文预算。

## 结果与取舍

| 数据 | 原BM25 | 语义 | 混合 |
|---|---:|---:|---:|
| 原组相关范围找回 | 7/7 | 7/7 | 7/7 |
| 原组无所问信息时空返回 | 3/3 | 1/3 | 1/3 |
| 新自然问法相关范围找回 | 5/16 | 16/16 | 16/16 |
| 新组无所问信息时空返回 | 2/4 | 2/4 | 2/4 |
| 新组平均资料JSON字符 | 215.3 | 493.25 | 545.2 |

20问查询向量计算单次样本p50/p95为2.87/3.97ms；另10问为3.16/5.24ms。模型加载1.30秒，16片段建向量98.62ms。这些是本机CPU原型测量，未与实时识别并行，没有API、画面或端到端3秒结论。

自然改写和跨语言召回的改进足够支持继续研究；但相关片段不一定包含所问答案。原组两题增加了不含答案的片段，载荷也增加，所以**暂不切换默认检索**。语义相似度既不是事实正确率，也不是“能回答”的概率；不能仅把阈值调到这30题表现最好就宣布完成。

下一步：用独立文档核对片段筛选和完整性，再接入可选本地索引与现有预取；验证128 token截断、较大资料库、并发识别时耗时、首次加载体验。外部资料回答的内容/出处质量放在同一阶段验收，个人16题不再反复微调。

## 重现

先从以上固定官方版本取得 `onnx/model_quint8_avx2.onnx` 和 `tokenizer.json`，放入同一模型目录；工具会核对两者SHA-256，并只读取固定公开资料。已有本机副本可直接执行：

```powershell
python tools/probe_semantic_materials.py --model-dir logs/goal/models/multilingual-minilm
```

工具不会联网、不会下载、不会读取默认用户资料目录，也不会改应用配置。新结果写独立时间戳JSON，原基线和原型结果保留。
