import logging
import requests
from functools import wraps
from typing import Any, Dict, List, Optional
from cozeloop.decorator import observe
from coze_workload_identity import Client as WIClient

logger = logging.getLogger(__name__)


def _get_access_token() -> str:
    """获取飞书多维表格（Bitable）的租户访问令牌。"""
    wi_client = WIClient()
    access_token: str = wi_client.get_integration_credential("integration-feishu-base")
    return access_token


def _require_token(func):
    """装饰器：确保方法调用前刷新 access_token"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.access_token = _get_access_token()
        if not self.access_token:
            raise ValueError("获取飞书 access_token 失败")
        return func(self, *args, **kwargs)
    return wrapper


class FeishuBitable:
    """
    飞书多维表格（Bitable）HTTP 客户端。
    所有方法返回值均为 Feishu OpenAPI 标准响应。
    基础 URL 默认 "https://open.larkoffice.com/open-apis"。
    """

    def __init__(self, base_url: str = "https://open.larkoffice.com/open-apis", timeout: int = 30):
        self.base_url: str = base_url.rstrip("/")
        self.timeout: int = timeout
        self.access_token: str = _get_access_token()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}" if self.access_token else "",
            "Content-Type": "application/json; charset=utf-8",
        }

    @observe
    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            url = f"{self.base_url}{path}"
            resp = requests.request(method, url, headers=self._headers(), params=params, json=json, timeout=self.timeout)
            resp_data: Dict[str, Any] = resp.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"FeishuBitable API request error: {e}")
        if resp_data.get("code") != 0:
            raise Exception(f"FeishuBitable API error: {resp_data}")
        return resp_data

    @_require_token
    def create_base(self, name: Optional[str] = None, folder_token: Optional[str] = None, time_zone: Optional[str] = None) -> Dict[str, Any]:
        """
        创建多维表格 Base
        接口：POST /bitable/v1/apps
        """
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if folder_token is not None:
            body["folder_token"] = folder_token
        if time_zone is not None:
            body["time_zone"] = time_zone
        return self._request("POST", "/bitable/v1/apps", json=body)

    @_require_token
    def get_base_info(self, app_token: str) -> Dict[str, Any]:
        """获取 Base 信息"""
        return self._request("GET", f"/bitable/v1/apps/{app_token}")

    @_require_token
    def list_tables(self, app_token: str, page_token: Optional[str] = None, page_size: Optional[int] = None) -> Dict[str, Any]:
        """列出 Base 下所有数据表"""
        params: Dict[str, Any] = {}
        if page_token is not None:
            params["page_token"] = page_token
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", f"/bitable/v1/apps/{app_token}/tables", params=params)

    @_require_token
    def create_table(self, app_token: str, table_name: str, fields: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        创建数据表（可同时定义初始字段）
        接口：POST /bitable/v1/apps/:app_token/tables
        """
        body: Dict[str, Any] = {"table_name": table_name}
        if fields is not None:
            body["fields"] = fields
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables", json=body)

    @_require_token
    def list_fields(
        self, app_token: str, table_id: str,
        view_id: Optional[str] = None, text_field_as_array: Optional[bool] = None,
        page_token: Optional[str] = None, page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """列出数据表字段"""
        params: Dict[str, Any] = {}
        if view_id is not None:
            params["view_id"] = view_id
        if text_field_as_array is not None:
            params["text_field_as_array"] = text_field_as_array
        if page_token is not None:
            params["page_token"] = page_token
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params=params)

    @_require_token
    def add_field(self, app_token: str, table_id: str, field: Dict[str, Any], client_token: Optional[str] = None) -> Dict[str, Any]:
        """新增字段"""
        params: Dict[str, Any] = {}
        if client_token is not None:
            params["client_token"] = client_token
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params=params, json=field)

    @_require_token
    def add_records(
        self, app_token: str, table_id: str, records: List[Dict[str, Any]],
        user_id_type: Optional[str] = None, client_token: Optional[str] = None,
        ignore_consistency_check: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """批量新增记录，单次最多 1000 条"""
        params: Dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        if client_token is not None:
            params["client_token"] = client_token
        if ignore_consistency_check is not None:
            params["ignore_consistency_check"] = ignore_consistency_check
        body: Dict[str, Any] = {"records": records}
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create", params=params, json=body)

    @_require_token
    def search_base(self, query: Optional[str] = None, count: Optional[int] = None, offset: Optional[str] = None) -> Dict[str, Any]:
        """查找多维表格"""
        body: Dict[str, Any] = {"docs_types": ["bitable"]}
        if query is not None:
            body["search_key"] = query
        if count is not None:
            body["count"] = count
        if offset is not None:
            body["offset"] = offset
        return self._request("POST", "/suite/docs-api/search/object", json=body)
