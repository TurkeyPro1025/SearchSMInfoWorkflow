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
存储芯片 DRAM NAND HBM 价格 供需 库存 涨价 最新
半导体 先进封装 国产替代 晶圆制造 资本开支 最新
新能源 光伏 锂电池 储能 排产 价格 政策 最新
AI算力 GPU 服务器 光模块 液冷 国产算力 最新
半导体设备 刻蚀 薄膜 沉积 光刻 离子注入 订单 最新
科技成长 A股 芯片 消费电子 AI 业绩 预告 景气 最新
```

---

## 领域 2：港股基金021378持仓（→ `hk_internet_news`）

> 基金021378重仓股：腾讯控股、阿里巴巴-W、小米集团-W、美团-W、商汤-W、快手-W、京东健康、贝壳-W、金蝶国际、哔哩哔哩-W

```
港股 互联网科技 腾讯 阿里 美团 小米 财报 指引 最新
021378 港股通互联网指数 重仓股 涨跌 驱动 最新
腾讯控股 回购 游戏 广告 视频号 财报 最新
阿里巴巴 美团 京东健康 快手 商汤 业务 数据 最新
港股 科网股 南向资金 回购估值 机构观点 最新
小米 贝壳 金蝶 哔哩哔哩 用户增长 盈利 预期 最新
```

---

## 领域 3：大宗商品（→ `commodities_news`）

```
稀土 氧化镨钕 北方稀土 出口 配额 价格 最新
原油 WTI 布伦特 OPEC+ 库存 地缘政治 最新
黄金 金价 美债收益率 美联储 避险 资金流向 最新
碳酸锂 锂矿 盐湖 供给 价格 新能源车 最新
铜价 LME 沪铜 电网 地产 制造业 需求 最新
大宗商品 有色 贵金属 能源 化工 库存 期货 最新
```

---

## 领域 4：市场震荡（→ `market_events_news`）

```
A股 大盘 震荡 放量 缩量 风格轮动 最新
美联储 降息 加息 CPI 非农 美债收益率 最新
地缘政治 中东 俄乌 关税 制裁 全球市场 最新
北向资金 两融 杠杆 量化 资金面 A股 最新
A股 政策 监管 国九条 IPO 退市 并购重组 最新
黑天鹅 系统性风险 VIX 汇率 债市 波动 最新
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
