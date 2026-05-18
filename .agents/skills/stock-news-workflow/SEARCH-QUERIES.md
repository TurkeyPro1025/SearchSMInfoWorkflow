# 搜索词（24条）

每个领域执行 6 条 web search，参数：`count=10, time_range=1d`。  
同一领域内按标题去重（`seen_titles` set）后合并结果。

## Web Search 供应商优先级与回退

执行每次查询时，按以下顺序尝试供应商：

1. Serper（Google Serper API）
2. Bing（Azure Bing News v7）
3. Brave Search API
4. Google CSE

当上游供应商出现配额耗尽（429/403）或不可用时，立即切到下一供应商；只有配置了对应 API Key 的供应商会被启用。

---

## 领域 1：科技股（→ `tech_stocks_news`）

```
存储芯片 DRAM NAND 行情 涨价 2026
半导体 先进封装 国产替代 最新消息
新能源 光伏 锂电池 储能 行业动态
AI算力 芯片 英伟达 国产算力 最新
半导体设备 刻蚀 薄膜 离子注入 2026
科技股 A股 芯片 存储板块 行情分析
```

---

## 领域 2：港股基金021378持仓（→ `hk_internet_news`）

> 基金021378重仓股：腾讯控股、阿里巴巴-W、小米集团-W、美团-W、商汤-W、快手-W、京东健康、贝壳-W、金蝶国际、哔哩哔哩-W

```
港股 互联网科技 腾讯 阿里 美团 小米 最新消息 2026
港股通互联网指数 021378 持仓 重仓股 行情
腾讯控股 财报 业绩 回购 最新动态
阿里巴巴-W 京东健康 快手 商汤 2026
港股 科网股 南向资金 持仓变动 2026
小米集团 哔哩哔哩 金蝶国际 贝壳 最新消息
```

---

## 领域 3：大宗商品（→ `commodities_news`）

```
稀土价格 北方稀土 氧化镨钕 行情 2026
国际油价 WTI 布伦特原油 OPEC 最新
黄金价格 国际金价 纽约金 最新行情 2026
碳酸锂价格 锂矿 新能源金属 行情
铜价 LME铜 沪铜 供需分析 2026
大宗商品 期货 贵金属 工业金属 行情分析
```

---

## 领域 4：市场震荡（→ `market_events_news`）

```
A股 大盘 震荡 行情分析 2026
美联储 加息 降息 CPI 通胀 最新
地缘政治 中东 局势 对股市影响 2026
两融 杠杆资金 北向资金 A股资金面
A股 政策 监管 IPO 退市 最新消息
黑天鹅 系统性风险 全球市场 恐慌指数 VIX
```

---

## 搜索执行伪代码

```
providers = [Serper, Bing, Brave, GoogleCSE]

def search_with_fallback(query, count=10, time_range="1d"):
    for p in providers:
        if not p.is_configured():
            continue
        try:
            return p.search(query, count=count, time_range=time_range)
        except QuotaExhaustedOrUnavailable:
            continue
    return []

for each domain in [科技股, 港股021378, 大宗商品, 市场震荡]:
    seen_titles = set()
    results = []
    for query in domain.queries:          # 6条
        items = search_with_fallback(query, count=10, time_range="1d")
        for item in items:
            if item.title and item.title not in seen_titles:
                seen_titles.add(item.title)
                results.append(item)
    domain.variable = JSON.stringify(results)
```
