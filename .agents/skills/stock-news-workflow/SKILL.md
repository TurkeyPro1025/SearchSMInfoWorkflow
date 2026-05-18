---
name: stock-news-workflow
version: 1.1.0
description: "股市资讯定时抓取整理工作流：并行搜索科技股、港股基金021378持仓、大宗商品、市场震荡四个领域的最新资讯，经 LLM 结构化整理后批量写入用户指定的飞书多维表格。当用户说「抓取股市资讯」「运行股票新闻工作流」「更新飞书股市表格」「整理今日资讯」时触发。"
metadata:
  requires:
    bins: ["lark-cli"]
---

# 股市资讯工作流

**前置：先读 [`../lark-shared/SKILL.md`](../lark-shared/SKILL.md) 处理飞书认证。**

## 执行原则（强约束）

1. 仅使用 Web Search 工具 / 供应商 API、LLM、飞书 CLI/Skill。
2. 禁止在执行链路中编写或运行 Python 脚本。
3. 数据流必须可追踪：搜索原始结果 -> LLM 结构化结果 -> 飞书写入 payload。
4. 除用户明确要求外，不自动建表或建字段；仅写入用户指定 app_token + table_id。

## 前置条件

用户必须提供以下信息（首次运行前确认）：

| 项目 | 来源 |
|---|---|
| 飞书 `app_token` | Base URL 中 `/base/{app_token}/` 部分 |
| 飞书 `table_id` | URL 参数 `table={table_id}` |

飞书表字段须已按 [FEISHU-SCHEMA.md](FEISHU-SCHEMA.md) 中的字段列表提前创建。工作流**不会**自动建表或建字段。

建议准备以下中间文件（便于审计与重试）：

- `assets/workflow/tech_stocks_news.json`
- `assets/workflow/hk_internet_news.json`
- `assets/workflow/commodities_news.json`
- `assets/workflow/market_events_news.json`
- `assets/workflow/organized_news.json`
- `assets/workflow/records.batch-001.json`（超 200 条时递增）

## 工作流（3步）

### Step 1 — 搜索（4领域并行）

读取 [SEARCH-QUERIES.md](SEARCH-QUERIES.md) 获取全部 24 条搜索词。

对每个领域的 6 条查询逐一执行 web search（`count=10, time_range=1d`），按标题去重后合并为该领域的结果列表。4 个领域产出 4 个原始结果变量：

- `tech_stocks_news`（科技股）
- `hk_internet_news`（港股基金021378持仓）
- `commodities_news`（大宗商品）
- `market_events_news`（市场震荡）

执行要求：

1. 每条查询都要命中一个可用供应商，遇到 429/403 立即切换下游供应商。
2. 单领域内按标题去重，保留首条更完整来源。
3. 每条结果最少保留字段：`title/url/snippet/source/publish_date`。
4. 领域结果保存为 JSON 数组，写入对应中间文件。

### Step 2 — LLM 整理

读取 [ORGANIZE-PROMPT.md](ORGANIZE-PROMPT.md)，将 Step 1 的 4 个变量填入用户提示词，调用 LLM，解析返回的 JSON。

LLM 输出格式：`{"科技股": [...], "港股基金021378持仓": [...], "大宗商品": [...], "市场震荡": [...]}`

每条资讯含：`title / industry / impact / summary / source / importance / url / publish_date / prediction_accuracy / authenticity`

执行要求：

1. system prompt 与 user prompt 模板必须来自本 skill 附件文档，不临时改字段。
2. 若首轮返回非 JSON：仅重试一次，并附加约束“只返回合法 JSON”。
3. 解析后仅保留四个领域 key：科技股、港股基金021378持仓、大宗商品、市场震荡。
4. 输出落盘到 `assets/workflow/organized_news.json`。

### Step 3 — 写入飞书

读取 [FEISHU-SCHEMA.md](FEISHU-SCHEMA.md) 了解字段值格式，使用用户提供的 `app_token` + `table_id` 执行：

```bash
lark-cli base +record-batch-create \
  --base-token <app_token> \
  --table-id <table_id> \
  --json @records.json
```

每批 ≤ 200 条，超出则分批执行。

执行要求：

1. 写入前先执行字段核对：

```bash
lark-cli base +field-list --base-token <app_token> --table-id <table_id> --as user
```

2. 使用 FEISHU-SCHEMA 的字段顺序生成 `fields + rows`，空值统一 `null`。
3. 分批文件命名 `records.batch-00N.json`，逐批写入并记录成功/失败批次。
4. 若用户要求测试写入，仅生成并写入 1 条假数据，不执行全量写入。

## 输出交付

完成后必须给出：

1. 四个领域各自检索条数（去重后）。
2. LLM 整理后四个领域条数。
3. 飞书写入成功条数、失败条数、失败原因。
4. 使用的 app_token 与 table_id（可脱敏展示）。

## 常见问题

| 现象 | 解决 |
|---|---|
| 搜索返回空 | 检查 web search 工具是否可用；尝试减少每批查询数 |
| LLM 输出非 JSON | 重试一次，提示"只返回 JSON，不要其他文字" |
| 飞书写入 403 | 运行 `lark-cli auth login` 切换到 user 身份 |
| 字段写入被忽略 | 用 `lark-cli base +field-list` 确认字段名与 FEISHU-SCHEMA.md 一致 |
| 执行中出现 Python 调试脚本 | 删除临时脚本，回到纯 CLI/Skill 链路 |
