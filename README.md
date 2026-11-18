# 课件校对建议台

课件校对建议台处理本地合成教研文本：用词表扫描错别字、术语、标点建议，记录教师的采纳/拒绝决定，并维护建议、原文位置、教师决定与词表版本之间的关系，供校内复核使用。

## 关系模型

- **词表（版本化）**：条目为“原词 → 推荐词”，带类别（错别字/术语/标点）与备注。版本不可变，更新词表即注册新版本，旧版本保留，供回查采纳来源。
- **建议（即时计算，不落库）**：按“当前文本 × 当前词表版本”扫描得出。建议身份是内容指纹 `sha256(课件, 原词, 前文16字, 后文16字)`，与绝对位置无关：课件编辑导致的位置平移不影响指纹；语境文字一改，指纹即变。
- **教师决定（追加式日志）**：按建议指纹记录采纳/拒绝、语境快照与当时的词表版本；同一指纹以最新决定为准（先拒绝后采纳可以纠正）。
- **采纳审计**：采纳时校验指纹仍命中当前文本（否则报“建议已失效”，不会把旧决定套到不同文字上），随后改写文本、修订号加一，并写入审计：原句、位置、来源词表版本、决定时间。

由此保证：

- 重新扫描或词表更新后，已拒绝的建议不会再次出现（拒绝按指纹匹配，与词表版本无关）；
- 拒绝只作用于对应语境——同一术语在另一门学科、另一句话中的建议不受影响；
- 课件编辑后，语境未变的决定继续生效；语境已变的决定自动失效（见 `stale`），相应建议重新进入待处理；
- 采纳结果可按原句与来源版本回查。

## 环境

需要 Python 3.11 或更新版本。应用运行不依赖第三方包；运行测试和构建前，安装固定版本的开发工具：`python -m pip install -r requirements-dev.txt`。

## 运行

```bash
# 导入课件记录并注册词表（状态保存在 var/proofreading.json）
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli ingest fixtures/proofreading_records.json
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli wordlist fixtures/wordlist.json

# 查看仍待处理的建议（含原词、推荐词、所在句子、词表版本）
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli scan

# 按建议编号采纳 / 按语境拒绝
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli adopt <建议编号>
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli reject <建议编号>

# 课件编辑后更新正文（修订号加一，决定按语境自动重新锚定）
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli edit <课件编号> --file 新正文.txt

# 教师视图：已采纳变化及来源版本 / 生效中的拒绝 / 已失效决定 / 总览
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli adopted
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli rejected
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli stale
PYTHONPATH=src python -m nlp_lesson_correction_assistant.cli status
```

词表更新：准备新版本号的词表文件后再次执行 `wordlist` 命令即可，旧版本与历史决定仍然保留。所有命令支持 `--state` 指定状态文件路径（默认 `var/proofreading.json`）。

## 测试

执行 `python -m pytest -q` 检查文本规范化、记录处理、建议扫描、决定作用域、编辑重锚定、版本视图及本地持久化。
