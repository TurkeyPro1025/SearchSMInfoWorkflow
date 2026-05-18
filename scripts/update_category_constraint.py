#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import sys

try:
    with open('config/organize_news_llm_cfg.json', 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # 修改系统提示词，加入领域约束
    sp = cfg['sp']
    constraint_text = """# 【重要约束】领域字段规则
必须且只能使用以下四个值作为领域字段的 key。不允许创建、修改或添加任何其他领域值：
- "科技股"
- "港股基金021378持仓"
- "大宗商品"
- "市场震荡"

如果某条资讯不属于这四个领域，则完全不输出该条资讯，不要创建新分类。

# 任务目标"""

    sp = sp.replace('# 任务目标', constraint_text)
    cfg['sp'] = sp

    with open('config/organize_news_llm_cfg.json', 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    print('✓ 配置文件已更新，领域约束已加入提示词')
    sys.exit(0)
except Exception as e:
    print(f'✗ 错误: {e}')
    sys.exit(1)
