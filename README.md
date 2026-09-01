# Paper Manager — 本地论文库（PDF → Markdown → 混合检索 → MCP）

导入论文 PDF，自动转成 Markdown、切块、向量化，并生成 3-5 句"摘要卡"。
Agent（或你自己在 CLI）检索时先拿摘要卡和最匹配片段，需要时再深入读章节——
**省 token** 是第一设计目标。

架构参考了 [PaperQA2](https://github.com/future-house/paper-qa)（引用对齐、
top-k 片段注入）与 [LightRAG](https://github.com/HKUDS/LightRAG)（图检索路线图，
见下方 Roadmap）。检索管线与 `other-local-project` 的记忆系统同构：
FTS5 + 向量 → RRF 融合 → Reranker 重排。

## 快速开始

```powershell
conda activate study
cd /path/to/paper_manager
pip install -r requirements.txt
Copy-Item .env.example .env   # 填好 LLM / SILICONFLOW key（或直接复制 other-local-project 的 .env）

# 导入（默认 datalab 引擎：高保真 Marker 转换，按页计费，多 key 自动轮询）
python cli.py ingest D:\papers\some-paper.pdf
python cli.py ingest D:\papers\notes.pdf --engine local  # 免费纯文本抽取（可选）

# 检索
python cli.py search "attention 机制的效率优化"
python cli.py read 1 --section method
python cli.py status
```

## 数据流

```
PDF ──convert──> Markdown(data/markdown/<sha>.md)
        datalab(默认): Marker 云端，高保真，按页计费，多 key 轮询
        local: PyMuPDF 文本抽取，插入 <!-- page:N --> 页码标记（免费）
      ──> 元数据（标题/作者/年份/DOI 正则 + PDF meta）
      ──> LLM 摘要卡 3-5 句（papers.summary，可 --no-summary）
      ──> 章节感知切块（300-800 tokens，带 section/page 元数据）
      ──> bge-m3 嵌入（批量 16，失败自动降级为纯 FTS5）
      ──> SQLite（data/papers.db: papers/chunks/FTS5/chunk_vectors）
```

### Datalab 多 key 轮询

`.env` 里用逗号分隔多个 key：

```dotenv
DATALAB_API_KEYS=key1,key2,key3
```

- 每次转换从第一个可用 key 开始；某个 key 返回 HTTP 402 或
  payment/credit/额度 类错误时，自动标记耗尽并切下一个，无需人工干预；
- 耗尽记录存在 `data/datalab_keys.json`（只存 key 的 sha256 前缀，不存明文），
  **6 小时后自动重试**（中途充值即可恢复）；
- CLI/MCP 的入库报告会显示本次用的 key 序号、剩余可用数和精确费用
  （`$0.0143/页` 级别，来自 Datalab 的 cost_breakdown）。

检索为**两阶段**（PaperQA2 风格）：

```
问题 ──LLM 改写──> 2-3 组中英文检索关键词（可选，失败退回原查询）
        ↓
阶段一（论文级）：papers_fts ⊕ 论文向量（标题+摘要+摘要卡）→ RRF → top-20 候选论文
        ↓
阶段二（章节级）：候选论文内 FTS ⊕ 向量 → RRF → 论文得分聚合
        ↓
论文得分 = 最佳 chunk 得分 × (1 + 0.1 × 额外命中段落数, 上限5) → bge-reranker 校正
        ↓
命中卡：标题 + 摘要卡 + 最佳片段（含章节/页码/相关段落数）
```

- 论文级索引在导入时自动构建；存量论文在首次检索或 `python cli.py backfill` 时补齐；
- 阶段一为空或无论文向量时自动降级为全局章节检索；所有外部服务失败均静默降级到 FTS5；
- 库内日志全部走 stderr，MCP stdio 协议不受 print 污染。

## MCP 接入（给 agent 用）

stdio 方式（other-local-project 的 mcp_bridge 直接支持），在 `.mcp.json` 加：

```json
"papers": {
  "command": "/path/to/python",
  "args": ["-m", "paper_manager.mcp_server"],
  "cwd": "/path/to/paper_manager"
}
```

暴露 4 个工具（均按 token 预算设计返回体）：

| 工具 | 作用 |
|------|------|
| `search_papers(query, top_k, year_min?, year_max?, author?, venue?)` | 两阶段检索（论文级召回→章节级聚合），摘要级命中卡，可叠加元数据过滤 |
| `read_paper_section(paper_id, section)` | 深入读某章节，默认 6000 字符截断 |
| `list_papers()` | 库清单 |
| `ingest_pdf(path, engine)` | 导入新 PDF |

HTTP 方式：`python -m paper_manager.mcp_server --http`（127.0.0.1:8820/mcp）。

## 环境变量

见 [.env.example](.env.example)。变量名与 other-local-project 完全一致，
两边可共用同一份 key 配置。全部服务可缺省：无 Embedding → 纯 FTS5；
无 LLM → 跳过摘要卡；无 DATALAB → 只用本地引擎。

## 检索评测（防回退）

`evals/` 内置 33 条标注用例（中文自然语言、英文关键词、中英混合、
易混淆论文对、元数据过滤），跑在 **10 篇确定性合成论文**上——
评测库位于 `evals/library/`，与你的真实 `data/` 完全隔离。

```powershell
python evals/build_library.py   # 一次性构建评测库（免费 local 引擎，无 LLM 摘要）
python evals/run_eval.py        # recall@5 / MRR / 延迟；低于阈值退出码 1
python evals/run_eval.py --k 3 --min-recall 0.9 --json
python evals/run_eval.py --rewrite   # 加上 LLM 查询改写（非确定性，默认关）
```

当前基线：**recall@5 = 100%，MRR = 1.000，中位延迟 ~0.7s**。
改任何检索参数（RRF k、chunk 大小、切块策略）后重跑一遍，防止调坏。
基线会随用例库更新而变化，重大改动后在 README 记录新基线。

## Roadmap

- **P1 引文图**：从 DOI 调 Semantic Scholar / OpenAlex 免费 API 拉
  references/citations，`related_papers` 工具走 SQL 图遍历（不花 LLM 钱）。
- **P2 概念图（LightRAG 式）**：LLM 从 chunk 抽方法/数据集/任务实体与关系，
  双层检索（实体邻域扩展 → 回取 chunk），pyvis 可视化。

## 设计说明

- 单 SQLite 文件 + 暴力余弦（numpy）：个人库规模（数百篇 × 数万 chunk）
  毫秒级返回，不需要向量数据库。
- `sha256` 去重：同一 PDF 重复导入直接跳过（`--force` 强制重建）。
- 所有外部服务调用都有降级路径，断网/欠费时库依然可检索。
