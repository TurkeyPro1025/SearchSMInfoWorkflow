from typing import Optional, List, Dict, Any
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


# ==================== 全局状态 ====================

class GlobalState(BaseModel):
    """工作流全局状态"""
    model_config = ConfigDict(populate_by_name=True)

    tech_stocks_news: str = Field(default="", description="科技股搜索结果原始文本")
    hk_internet_news: str = Field(default="", description="港股基金021378持仓公司搜索结果原始文本")
    commodities_news: str = Field(default="", description="大宗商品搜索结果原始文本")
    market_events_news: str = Field(default="", description="市场震荡事件搜索结果原始文本")
    organized_news: Dict[str, Any] = Field(default={}, description="按领域分类整理后的资讯数据")
    base_token: str = Field(
        default="",
        validation_alias=AliasChoices("base_token", "app_token"),
        description="飞书 Base token；优先使用环境变量 FEISHU_BASE_TOKEN",
    )
    table_id: str = Field(default="", description="飞书多维表格的 table_id（CLI 写入必填）")
    write_result: str = Field(default="", description="飞书写入结果信息")


# ==================== 图输入输出 ====================

class GraphInput(BaseModel):
    """工作流输入参数"""
    model_config = ConfigDict(populate_by_name=True)

    base_token: str = Field(
        default="",
        validation_alias=AliasChoices("base_token", "app_token"),
        description="飞书 Base token；优先使用环境变量 FEISHU_BASE_TOKEN",
    )
    table_id: str = Field(default="", description="飞书多维表格的 table_id（CLI 写入必填）")


class GraphOutput(BaseModel):
    """工作流输出结果"""
    model_config = ConfigDict(populate_by_name=True)

    organized_news: Dict[str, Any] = Field(default={}, description="按领域分类整理后的资讯数据")
    write_result: str = Field(default="", description="飞书写入结果信息")
    base_token: str = Field(default="", description="本次写入使用的 base_token")
    table_id: str = Field(default="", description="使用的飞书多维表格 table_id")


# ==================== 搜索节点入参出参 ====================

class SearchBaseInput(BaseModel):
    """搜索节点基础输入（无需外部参数，搜索关键词内置）"""
    pass


class SearchTechStocksOutput(BaseModel):
    """科技股搜索节点输出"""
    tech_stocks_news: str = Field(..., description="科技股搜索结果原始文本")


class SearchHkInternetOutput(BaseModel):
    """港股基金021378持仓搜索节点输出"""
    hk_internet_news: str = Field(..., description="港股基金021378持仓公司搜索结果原始文本")


class SearchCommoditiesOutput(BaseModel):
    """大宗商品搜索节点输出"""
    commodities_news: str = Field(..., description="大宗商品搜索结果原始文本")


class SearchMarketEventsOutput(BaseModel):
    """市场震荡事件搜索节点输出"""
    market_events_news: str = Field(..., description="市场震荡事件搜索结果原始文本")


# ==================== 资讯整理节点入参出参 ====================

class OrganizeNewsInput(BaseModel):
    """资讯整理节点输入"""
    tech_stocks_news: str = Field(default="", description="科技股搜索结果原始文本")
    hk_internet_news: str = Field(default="", description="港股基金021378持仓公司搜索结果原始文本")
    commodities_news: str = Field(default="", description="大宗商品搜索结果原始文本")
    market_events_news: str = Field(default="", description="市场震荡事件搜索结果原始文本")


class OrganizeNewsOutput(BaseModel):
    """资讯整理节点输出"""
    organized_news: Dict[str, Any] = Field(..., description="按领域分类整理后的资讯数据，每条含title/summary/source/importance/url/publish_date/prediction_accuracy/authenticity")


# ==================== 飞书写入节点入参出参 ====================

class WriteFeishuInput(BaseModel):
    """飞书多维表格写入节点输入"""
    model_config = ConfigDict(populate_by_name=True)

    base_token: str = Field(
        default="",
        validation_alias=AliasChoices("base_token", "app_token"),
        description="飞书 Base token；优先使用环境变量 FEISHU_BASE_TOKEN",
    )
    table_id: str = Field(default="", description="飞书多维表格的 table_id（CLI 写入必填）")
    organized_news: Dict[str, Any] = Field(default={}, description="按领域分类整理后的资讯数据")


class WriteFeishuOutput(BaseModel):
    """飞书多维表格写入节点输出"""
    model_config = ConfigDict(populate_by_name=True)

    write_result: str = Field(..., description="飞书写入结果信息")
    base_token: str = Field(default="", description="本次写入使用的 base_token")
    table_id: str = Field(default="", description="使用的飞书多维表格 table_id")
