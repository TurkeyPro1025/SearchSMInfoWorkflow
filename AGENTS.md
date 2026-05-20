## 项目概述
- **名称**: 多领域股市资讯抓取整理工作流
- **功能**: 定时抓取科技股、港股基金021378持仓、大宗商品、市场震荡事件四大领域股市资讯，按领域分类整理（含真实性评估、预测准确率）后写入飞书多维表格

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| search_tech_stocks | `nodes/search_tech_stocks_node.py` | task | 搜索存储芯片、半导体、新能源、AI算力、消费电子等科技股资讯（多关键词多查询） | - | - |
| search_hk_internet | `nodes/search_hk_internet_node.py` | task | 搜索基金021378前十大重仓股资讯（腾讯/阿里/美团/小米/商汤/金蝶/快手/贝壳/京东健康/哔哩哔哩） | - | - |
| search_commodities | `nodes/search_commodities_node.py` | task | 搜索稀土、石油、黄金、锂、铜等大宗商品资讯 | - | - |
| search_market_events | `nodes/search_market_events_node.py` | task | 搜索A股震荡、全球市场联动、政策变动、黑天鹅等重大事件资讯 | - | - |
| organize_news | `nodes/organize_news_node.py` | agent | 使用LLM按领域分类、去重、提炼结构化概述（原因/经过/结果/预测+准确率）、评估信息真实性 | - | `config/organize_news_llm_cfg.json` |
| write_feishu | `nodes/write_feishu_node.py` | task | 将整理后资讯批量写入飞书多维表格 | - | - |

**类型说明**: task(task节点) / agent(大模型) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

## 工作流DAG结构
```
START → cli_preflight ─┬→ search_tech_stocks ──┐
                       ├→ search_hk_internet ──┤
                       ├→ search_commodities ──┼→ organize_news → write_feishu → END
                       └→ search_market_events┘
```
- `cli_preflight` 是硬门槛：先检查 `lark-cli` 脚手架状态、`lark-cli auth status` 的 user 登录态，以及 `lark-cli base +field-list --as user` 对目标 Base/Table 的只读权限
- 只有 `cli_preflight` 通过，4 路搜索节点才允许启动；若预检失败，整个工作流立即终止，避免先消耗 Web Search/LLM 再在飞书写入阶段失败
- 4路搜索节点从START并行触发
- 4路搜索全部完成后汇聚到 organize_news
- organize_news 完成后进入 write_feishu

## 技能使用
- 节点 `search_tech_stocks` / `search_hk_internet` / `search_commodities` / `search_market_events` 使用 Web Search 技能
- 节点 `organize_news` 使用 大语言模型 技能（百联平台：qwen-plus）
- 节点 `write_feishu` 使用 飞书多维表格 集成

## 输入参数
| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| base_token | str | 是 | 飞书多维表格的 base_token，供 `lark-cli base +...` 使用 |
| table_id | str | 是 | 目标数据表 table_id，工作流只向现有表写入 |

## 飞书多维表格写入约束
节点 `write_feishu` 只负责把整理后的资讯写入用户指定的现有数据表：
- 项目入口会先执行 CLI 预检；若 `auth status` 或 `+field-list` 不通过，不会进入搜索、整理和写入阶段
- 运行时通过 `lark-cli base +field-list / +record-list / +record-batch-create --as user` 与飞书交互
- 写入前会按 `链接` 字段去重：先去当前批次重复，再过滤目标表中已存在的链接
- 不自动建表、不自动建字段；目标表字段需提前按工作流约定准备好

| 飞书字段 | 类型 | 说明 |
|---------|------|------|
| 领域 | 单选(type=3) | 预定义选项：科技股/港股基金021378持仓/大宗商品/市场震荡 |
| 标题 | 文本(type=1) | 资讯标题 |
| 行业 | 文本(type=1) | 行业归类 |
| 内容摘要 | 文本(type=1) | 结构化概述（原因→经过→结果→预测及准确率） |
| 影响 | 单选(type=3) | 预定义选项：好/坏 |
| 来源 | 文本(type=1) | 资讯来源 |
| 重要性 | 单选(type=3) | 预定义选项：高/中/低 |
| 链接 | 超链接(type=15) | 原文链接，去重主键 |
| 发布日期/时间 | 日期(type=5) | 信息原始发布日期，无则使用写入日期 |
| 预测准确率 | 数字(type=2) | 0~1 浮点值，显示为百分比 |
| 真实性评估 | 单选(type=3) | 预定义选项：高/中/低（基于发布者权威性和多源交叉验证） |

## 定时执行说明