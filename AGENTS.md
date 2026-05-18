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
START ─┬→ search_tech_stocks ──┐
       ├→ search_hk_internet ──┤
       ├→ search_commodities ──┼→ organize_news → write_feishu → END
       └→ search_market_events┘
```
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
| app_token | str | 是 | 飞书多维表格的 app_token（已有 Base） |
| table_id | str | 否 | 数据表 table_id，为空时自动在 Base 中创建带完整字段的数据表 |

## 飞书多维表格自动建表
节点 `write_feishu` 在写入前会自动处理表格结构：
- 若未提供 `table_id`，自动调用 `create_table` API 创建数据表（表名：股市资讯_YYYYMMDD）
- 建表时一次性创建9个字段（含预定义选项、日期格式、超链接格式）
- 字段定义位于 `nodes/write_feishu_node.py` 中的 `FIELD_DEFINITIONS`

| 飞书字段 | 类型 | 说明 |
|---------|------|------|
| 领域 | 单选(type=3) | 预定义选项：科技股/港股基金021378持仓/大宗商品/市场震荡 |
| 标题 | 文本(type=1) | 资讯标题 |
| 内容摘要 | 文本(type=1) | 结构化概述（原因→经过→结果→预测及准确率） |
| 来源 | 文本(type=1) | 资讯来源 |
| 重要性 | 单选(type=3) | 预定义选项：高/中/低 |
| 链接 | 超链接(type=15) | 原文链接，格式 {"link":"url","text":"显示文本"} |
| 发布日期 | 日期(type=5) | 信息原始发布日期，无则使用写入日期 |
| 预测准确率 | 单选(type=3) | 预定义选项：高/中/低/不适用 |
| 真实性评估 | 单选(type=3) | 预定义选项：高/中/低（基于发布者权威性和多源交叉验证） |

## 工具类
| 工具名 | 文件位置 | 功能描述 |
|--------|---------|---------|
| FeishuBitable | `tools/feishu_bitable.py` | 飞书多维表格HTTP客户端封装，提供创建/查询表格、字段、记录等API |

## 定时执行说明