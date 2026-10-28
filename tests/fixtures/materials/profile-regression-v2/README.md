# 资料回归语料 v2：仅修正打包文件名

本目录是原 16 题公开虚构语料的独立完整副本。原目录和历史结果留在本机，不是运行本目录所需的依赖。

- 派生自原 manifest SHA-256：`cdcad7ba076dd5508aed7e4a2dae771ce6d8dd1a892444d4181b191df9c71e96`。
- 唯一语料改动是将 `data/profile.md` 改名为 `data/test_profile.md`，并同步查询文件中的来源路径与基线文件名。
- 九份资料的正文逐字节保持一致；问题文本、顺序、预期行号、禁入范围、原因和主题没有改动。新 manifest 保留原哈希映射与派生说明。
- 原文件名会命中仓库的隐私忽略规则，因此没有强制添加任何 `profile.md`。本目录可以完整提交和独立复现。

显式运行：`python tools/eval_materials.py --fixture profile-regression-v2 --phase after`。不传 `--fixture` 时运行通用文档语料 `general`。

基线仍是旧版每题发送整份 basic profile 正文的资料发送方式；评测中的公开虚构文件名为 `test_profile.md`。它不是旧版模型回答重放。BM25 使用相同组件与参数，不针对本次包装变更修改算法。

路径改名会改变路径派生的 source ID；默认 owner_hint 会从 profile_candidate 变为 unverified。该字段不参与索引或排序，也不是身份或事实验证。完整 JSON 载荷的字符数可能因路径和提示字段长度变化，不能将这个变化称作检索优化。

标注衡量相关范围召回、列出的禁入范围、发送字符数和本地检索时间，不代表回答事实正确率。全部人物、经历、机构与安排均为测试虚构内容。
