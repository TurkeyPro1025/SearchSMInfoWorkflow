# 搜索词（24条）

每个领域执行 6 条 web search，参数：`count=10`，时间窗口由工作流输入决定。默认 `search_time_mode=rolling_24h`。  
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
存储芯片 行情
半导体 行情
光伏 行情
锂电 行情
储能 行情
AI算力 行情
```

---

## 领域 2：港股基金021378持仓（→ `hk_internet_news`）

> 基金021378重仓股：腾讯控股、阿里巴巴-W、小米集团-W、美团-W、商汤-W、快手-W、京东健康、贝壳-W、金蝶国际、哔哩哔哩-W

```
腾讯控股
阿里巴巴-W
小米集团-W
美团-W
商汤-W
快手-W
京东健康
贝壳-W
金蝶国际
哔哩哔哩-W
```

---

## 领域 3：大宗商品（→ `commodities_news`）

```
稀土 行情
原油 OPEC+ 地缘
黄金 行情
碳酸锂 行情
铜价 行情
有色金属 行情
```

---

## 领域 4：市场震荡（→ `market_events_news`）

```
A股 资金面
A股 放量 风格轮动
政策 监管 并购重组
地缘政治 中东 俄乌
美联储 官员讲话
美国 CPI 非农 美债收益率
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
