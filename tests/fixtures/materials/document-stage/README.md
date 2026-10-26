# 通用文档回答阶段检查（D-037）

`answer-cases.json` 的八个问题及三组公开虚构文档在第一次 API 检查前冻结，字节散列见 `manifest.json`。七题资料有答案，一题没有设备品牌/型号。延期题是 D-030 已知失败，不是未见测试。五次运行都保留，不删失败结果；同题发送的来源载荷逐字相同。

`transfer-cases.json` 四题在最终提示词完成后编写，用已有公开文档检查新问法和对话对象；发送前冻结，散列见 `transfer-manifest.json`。这不是独立盲测，也没有改前对照。

默认不联网：`python tools/eval_document_answers.py`。显式八题：`python tools/eval_document_answers.py --run-api --model-dir <本机已准备模型目录> --label <标签>`；四题另加 `--case-set transfer`。固定 deepseek-flash、关闭思考、400 tokens、temperature 0.3；不热身、不重试，两次请求失败就停止。只读这些公开文件，不读私人资料或录音。

审核见 `tests/live_suggest/document-answer-review-20261026.json`。保留两种口径：原 `required_facts` 全清单覆盖，以及事后明确、对所有版本统一使用的“所问关键要点”覆盖（不要求重复未问的免费/电话禁用等）。后者不是预注册评测；两者都是开发代理审核，不是独立人工正确率。语法、语体、角色、篇幅问题另外列出，不能由覆盖数字推导“可以放心直接念”。方法和实际截图见 `docs/document-answers-20261026.md`。
