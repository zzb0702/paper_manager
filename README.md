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

# 导入
python cli.py ingest D:\papers\some-paper.pdf            # 免费本地引擎（文本型 PDF）
python cli.py ingest D:\papers\scan.pdf --engine datalab # 扫描件/公式/表格（按页计费）

# 检索
python cli.py search "attention 机制的效率优化"
python cli.py read 1 --section method
python cli.py status
```

## 数据流

```
PDF ──convert──> Markdown(data/markdown/<sha>.md)
        local: PyMuPDF 文本抽取，插入 <!-- page:N --> 页码标记（免费）
        datalab: Marker 云端（高保真，计费）
      ──> 元数据（标题/作者/年份/DOI 正则 + PDF meta）
      ──> LLM 摘要卡 3-5 句（papers.summary，可 --no-summary）
      ──> 章节感知切块（300-800 tokens，带 section/page 元数据）
      ──> bge-m3 嵌入（批量 16，失败自动降级为纯 FTS5）
      ──> SQLite（data/papers.db: papers/chunks/FTS5/chunk_vectors）
```

检索：`FTS5(trigram, 中文安全) ⊕ 向量余弦 → RRF(k=60) → bge-reranker → 去重出卡`。

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
| `search_papers(query, top_k)` | 摘要级命中卡：标题+摘要+最佳片段+章节页码出处 |
| `read_paper_section(paper_id, section)` | 深入读某章节，默认 6000 字符截断 |
| `list_papers()` | 库清单 |
| `ingest_pdf(path, engine)` | 导入新 PDF |

HTTP 方式：`python -m paper_manager.mcp_server --http`（127.0.0.1:8820/mcp）。

## 环境变量

见 [.env.example](.env.example)。变量名与 other-local-project 完全一致，
两边可共用同一份 key 配置。全部服务可缺省：无 Embedding → 纯 FTS5；
无 LLM → 跳过摘要卡；无 DATALAB → 只用本地引擎。

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
