---
name: stock-news-workflow
version: 1.5.0
description: "股市资讯定时抓取整理工作流：并行搜索科技股、港股基金021378持仓、大宗商品、市场震荡四个领域的最新资讯，经 LLM 结构化整理后批量写入用户指定的飞书多维表格。当用户说「抓取股市资讯」「运行股票新闻工作流」「更新飞书股市表格」「整理今日资讯」时触发。"
---

# 股市资讯工作流

**前置：先读 [`../lark-shared/SKILL.md`](../lark-shared/SKILL.md) 处理飞书认证。**

## 执行原则（强约束）

1. 仅使用 Web Search 工具 / 供应商 API、LLM、飞书 CLI/Skill。
2. 禁止在执行链路中编写或运行 Python 脚本。
3. 数据流必须可追踪：搜索原始结果 -> LLM 结构化结果 -> 飞书写入 payload。
4. 飞书写入默认优先使用 `lark-cli base`，且显式指定 `--as user`；不要静默切换到 bot / app / tenant 身份。
5. 除用户明确要求外，不自动建表或建字段；仅写入用户指定 `base_token` + `table_id`。
6. CLI 预检优先级最高：在任何 Web Search、LLM 调用、缓存刷新或批量写入前，必须先验证脚手架状态、user 登录态和目标 Base 读权限；任一项失败都要立即终止。
7. 当用户在调用时提供 `base_token` / `table_id`，应将其视为可复用参数：后续调用若未显式覆盖，优先复用已保存值（若调用宿主支持，否则仍需向用户索取），而不是每次重新向用户索取。

## 前置条件

用户必须提供以下信息（首次运行前确认）：

| 项目 | 来源 |
|---|---|
| 飞书 `base_token` | Base URL 中 `/base/{base_token}/` 部分 |
| 飞书 `table_id` | URL 参数 `table={table_id}` |

飞书表字段须已按 [FEISHU-SCHEMA.md](FEISHU-SCHEMA.md) 中的字段列表提前创建。工作流**不会**自动建表或建字段。

建议准备以下中间文件（便于审计与重试）：

- `assets/workflow/tech_stocks_news.json`
- `assets/workflow/hk_internet_news.json`
- `assets/workflow/commodities_news.json`
- `assets/workflow/market_events_news.json`
- `src/storage/cache/organized_news_cache.json`
- `src/storage/cache/records.batch-001.json`（超 200 条时递增）

说明：

1. `lark-cli base +...` 使用的是 `base_token`，不要把 `app_token` 传给 `--base-token`。
2. 若用户明确要求 user 身份，认证失败时必须直接报错并引导重新登录，不要偷偷降级到应用身份。

## 参数持久化（强约束）

当用户首次提供或更新 `base_token` 与 `table_id` 时，后续调用按以下规则处理：

1. `base_token` 必须以加密形式保存到本地安全存储中；禁止以明文写入工作区文件、日志、终端输出、回复正文、截图说明或普通 memory 文本。
2. `table_id` 作为非密钥参数，可与该配置一并保存，并在后续调用中自动回填。
3. 后续运行若用户未显式提供参数，先尝试读取已保存的默认 `base_token` 与 `table_id`；只有本地无保存值时，才向用户索取。
4. 若用户本次明确提供了新的 `base_token` 或 `table_id`，视为覆盖更新；成功通过 Step 0 预检后，用新值替换旧值。
5. 若用户明确要求“不保存”“清除已保存参数”或“切换到另一张表”，必须尊重用户意图，删除或更新已保存值。
6. 对外汇报时，`base_token` 只能脱敏展示；`table_id` 可按需原样展示。
7. 若当前运行环境不具备可靠的本地加密保存能力，则必须明确告知这一限制；不要退化为明文持久化。

## 工作流（4步）

### Step 0 — CLI 预检（最高优先级，失败即终止）

在开始搜索前，必须先完成以下 3 项检查；只有全部通过，才允许进入后续步骤。

1. 检查 `lark-cli` 脚手架状态。

首次运行或怀疑本机未完成初始化时，先按 [../lark-shared/SKILL.md](../lark-shared/SKILL.md) 执行：

```bash
lark-cli config init --new
```

2. 检查 user 登录态。

```bash
lark-cli auth status
```

若未登录、token 失效、或当前身份不是用户要求的 `user`，立即停止，并引导用户重新执行带范围的授权：

```bash
lark-cli auth login --scope "bitable:app:readonly bitable:app"
```

3. 检查目标 Base 的实际读权限与字段可见性。

```bash
lark-cli base +field-list --as user --base-token <base_token> --table-id <table_id>
```

处理规则：

1. 这一步是整个工作流的硬门槛；未通过前，禁止执行 Web Search、LLM、缓存清空或 `+record-batch-create`。
2. 若返回 401/403、refresh 失败、scope 不足或 `permission_violations`，立即终止，并按 [../lark-shared/SKILL.md](../lark-shared/SKILL.md) 优先处理 user 授权或权限开通。
3. 若 `+field-list` 失败，则视为“当前表不可写”而不是“稍后再试”；因为连字段都读不到，继续搜索和整理只会功亏一篑。
4. 只有在 Step 0 通过后，才允许进入搜索与 LLM 整理。
5. Step 0 使用的 `base_token` 与 `table_id`，优先顺序为：用户本次显式输入 > 本地安全存储中的已保存默认值；禁止回退到明文缓存或临时日志抄录值。

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
4. 搜索结果在进入 LLM 前只做去重与字段压缩，不按条数裁剪；去重后的消息都应保留并传给 LLM。
5. 将新的 LLM 结果写入缓存前，先清空现有缓存中的 `organized_news`，再写入新的 `organized_news` 到 `src/storage/cache/organized_news_cache.json`。
6. 缓存文件建议同时写入 `updated_at`，用于标记本次 LLM 整理完成时间。
7. Step 3 只负责读取并写入飞书，不再在写入成功后清空缓存；缓存刷新时机统一放在下一次 Step 2 写入新结果之前。
8. 输出后校验若发现潜在误合并，只通过日志提醒即可；日志需至少包含领域和对应的两条标题，供用户手动在飞书中处理。

### 缓存生命周期（强约束）

`organized_news_cache.json` 是 Step 2 与 Step 3 之间的唯一重试依据，处理规则如下：

1. LLM 成功输出并通过 JSON 结构校验后，先清空现有缓存中的 `organized_news`，再立即写入新的 `src/storage/cache/organized_news_cache.json`。
2. 若当前新路径不存在，可兼容读取旧路径 `assets/workflow/organized_news_cache.json`；但后续新写入必须统一写到新路径。
3. Step 3 启动时，若内存中没有 `organized_news`，优先从 `src/storage/cache/organized_news_cache.json` 读取，不重新触发 Web Search 或 LLM。
4. 若字段校验失败、CLI 写入失败、用户认证失败、网络失败，必须保留缓存文件，供后续直接重试写入。
5. Step 3 无论成功还是失败，都不主动清空 `src/storage/cache/organized_news_cache.json`；缓存刷新只发生在下一次 Step 2 写入新 LLM 结果之前。
6. 若是部分批次成功、部分批次失败，视为整体未完成，缓存必须保留。
7. 若用户明确要求“只用缓存重试写入”，则直接跳过 Step 1 和 Step 2，基于缓存生成 `records.batch-00N.json` 并执行 CLI 写入。

### Step 3 — 写入飞书

读取 [FEISHU-SCHEMA.md](FEISHU-SCHEMA.md) 了解字段值格式，优先复用 `src/storage/cache/organized_news_cache.json` 中的 `organized_news`，并使用用户提供的 `base_token` + `table_id` 执行：

```bash
lark-cli base +record-batch-create \
  --as user \
  --base-token <base_token> \
  --table-id <table_id> \
  --json "@./src/storage/cache/records.batch-001.json"
```

每批 ≤ 200 条，超出则分批执行。

执行要求：

1. 写入前先执行字段核对：

```bash
lark-cli base +field-list --as user --base-token <base_token> --table-id <table_id>
```

  说明：Step 0 已做过一次预检；这里再次执行属于写入前的最终保护，不可替代 Step 0。

2. 使用 FEISHU-SCHEMA 的字段顺序生成 `fields + rows`，空值统一 `null`。
3. `lark-cli` 读取 `@file.json` 时要求相对路径；先切到项目目录，再用 `"@./src/storage/cache/records.batch-00N.json"` 传参。
4. 分批文件命名 `src/storage/cache/records.batch-00N.json`，逐批写入并记录成功/失败批次。
5. 写入前必须按“链接”字段做去重：
  - 先去掉本次待写入数据中链接完全一致的重复项；
  - 再读取目标飞书表中现有记录的“链接”字段，过滤掉已经存在的链接。
6. 执行前优先读取缓存中的 `organized_news`，不要重新组织数据；仅在缓存不存在时才视为无法重试。
7. 若用户要求测试写入，仅生成并写入 1 条假数据，不执行全量写入。
8. 若用户已明确要求使用 user 身份，任何 401/403/refresh 失败都应终止并提示重新执行 `lark-cli auth login`，不要静默切换身份。
9. 全部批次成功后清空 `src/storage/cache/organized_news_cache.json` 中的 `organized_news`；只要存在失败批次，就保留缓存文件内容。

## 输出交付

完成后必须给出：

1. 四个领域各自检索条数（去重后）。
2. LLM 整理后四个领域条数。
3. 飞书写入成功条数、失败条数、失败原因。
4. 使用的 base_token 与 table_id（可脱敏展示）。

## 常见问题

| 现象 | 解决 |
|---|---|
| 搜索返回空 | 检查 web search 工具是否可用；尝试减少每批查询数 |
| LLM 输出非 JSON | 重试一次，提示"只返回 JSON，不要其他文字" |
| 飞书写入 401/403 或 refresh 失败 | 运行 `lark-cli auth login` 重新完成 user 授权；不要回退到 app 身份 |
| `param baseToken is invalid` | 检查是否误把 `app_token` 传给了 `--base-token`；CLI 只接受 `base_token` |
| 字段写入被忽略 | 用 `lark-cli base +field-list` 确认字段名与 FEISHU-SCHEMA.md 一致 |
| 反复写入同一批资讯 | 写入前先按“链接”去重，并过滤飞书表里已存在的相同链接 |
| 想直接重试写入 | 直接复用 `src/storage/cache/organized_news_cache.json` 重新生成 `records.batch-00N.json` 并执行 `+record-batch-create --as user`；不要重新跑 Web Search / LLM |
| 缓存文件存在但内容为空 | 说明新一轮 LLM 结果写入前已先清空旧缓存，或被手动清空；若要重试写入，需先恢复 `organized_news` 内容 |
| 执行中出现 Python 调试脚本 | 删除临时脚本，回到纯 CLI/Skill 链路 |
