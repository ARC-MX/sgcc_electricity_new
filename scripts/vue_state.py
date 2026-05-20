"""Vue状态注入辅助工具，用于读取95598页面数据。

向浏览器注入JavaScript，扫描所有DOM元素查找__vue__属性，
从Vue组件实例中提取结构化数据。
"""

from __future__ import annotations

from typing import Any, Optional


SELECTED_VUE_DATA_SCRIPT = """
const clone = (value) => {
  try { return JSON.parse(JSON.stringify(value)); } catch (e) { return null; }
};
const wantedKeys = [
  'mixinGetYuEdata',
  'consInfoobj',
  'consInfo',
  'electric',
  'powerData',
  'mothData',
  'tableData',
  'tableData_t',
  'sevenEleList',
  'sevenEleList_t',
  'new_sevenEleList',
  'tariffC',
  'start',
  'end',
  'queryYear',
  'activeName',
  'billNumberList',
  'BillList',
  'billList',
  'billMonth',
  'NewtotalBillProvince',
  'optionalYearArray',
  'selectYear',
  'listData'
];
return Array.from(document.querySelectorAll('*'))
  .map((el, index) => {
    const vm = el.__vue__;
    if (!vm) return null;
    const data = {};
    wantedKeys.forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(vm, key)) {
        data[key] = clone(vm[key]);
      }
    });
    if (!Object.keys(data).length) return null;
    return {
      index,
      tag: el.tagName,
      id: el.id || '',
      className: String(el.className || '').slice(0, 160),
      text: (el.innerText || el.textContent || '').trim().slice(0, 500),
      data
    };
  })
  .filter(Boolean);
"""


def selected_vue_data(driver) -> list[dict[str, Any]]:
    """执行JS脚本，从当前页面提取Vue状态数据。"""
    return driver.execute_script(SELECTED_VUE_DATA_SCRIPT) or []


def normalize_user_info(components: list[dict[str, Any]]) -> dict[str, Any]:
    """从Vue状态中提取用户信息（姓名、地址）。"""
    cons = _first_data_value(components, "consInfoobj") or _first_data_value(components, "consInfo") or {}
    if not isinstance(cons, dict):
        cons = {}
    return {
        "user_name": cons.get("consName") or cons.get("custName") or "",
        "address": cons.get("elecAddr") or cons.get("addr") or "",
        "user_id": cons.get("consNo") or cons.get("consId") or "",
    }


def normalize_balance(components: list[dict[str, Any]]) -> dict[str, Any]:
    """从Vue状态中提取应交金额（应交金额）。"""
    raw = _first_data_value(components, "mixinGetYuEdata") or {}
    return {
        "as_of": raw.get("amtTime"),
        "amount_due": _safe_float(raw.get("historyOwe")),
        "user_id": raw.get("consNo"),
    }


def normalize_usage(components: list[dict[str, Any]]) -> dict[str, Any]:
    """从Vue状态中提取用电量数据（年度汇总、月度、每日分时）。"""
    power_data = _first_data_value(components, "powerData") or _first_data_value(components, "tableData_t") or {}
    info = power_data.get("dataInfo") or {}
    month_rows = power_data.get("mothEleList") or _first_data_value(components, "mothData") or []

    daily_rows = []
    for key in ("tableData", "new_sevenEleList", "sevenEleList"):
        for row in _data_values(components, key):
            if isinstance(row, list) and row and any(
                item.get("thisVPq") is not None for item in row if isinstance(item, dict)
            ):
                daily_rows = row
                break
        if daily_rows:
            break

    return {
        "year": str(info.get("year") or _first_data_value(components, "queryYear") or ""),
        "yearly_usage": _safe_float(info.get("totalEleNum")),
        "yearly_charge": _safe_float(info.get("totalEleCost")),
        "recent_total_usage": _safe_float(_first_data_value(components, "tariffC")),
        "daily_range": {
            "start": _first_data_value(components, "start"),
            "end": _first_data_value(components, "end"),
        },
        "months": [_normalize_usage_month(row) for row in month_rows if isinstance(row, dict)],
        "daily": [_normalize_daily_row(row) for row in daily_rows if isinstance(row, dict) and _normalize_daily_row(row)],
        "raw": power_data,
    }


def normalize_bill_detail(components: list[dict[str, Any]]) -> dict[str, Any]:
    """从Vue状态中提取月度账单详情，含分时电价明细。"""
    bill = (_first_data_value(components, "billList") or [{}])[0]
    if not isinstance(bill, dict):
        bill = {}
    basic = bill.get("basicInfo") or {}
    pv_qty = (bill.get("pvQtyList") or [{}])[0]
    return {
        "month": _normalize_ym(bill.get("ym")),
        "user_id": basic.get("consNo"),
        "begin_date": basic.get("begDate"),
        "end_date": basic.get("endDate"),
        "usage": _safe_float(basic.get("monthPq")),
        "charge": _safe_float(basic.get("monthAmt")),
        "year_usage": _safe_float(basic.get("yearPq")),
        "year_charge": _safe_float(basic.get("yearAmt")),
        "valley_usage": _safe_float(pv_qty.get("valQty")),
        "flat_usage": _safe_float(pv_qty.get("flatQty")),
        "peak_usage": _safe_float(pv_qty.get("peakQty")),
        "tip_usage": _safe_float(pv_qty.get("sharpQty")),
        "raw": bill,
    }


def _first_data_value(components: list[dict[str, Any]], key: str) -> Any:
    for component in components:
        data = component.get("data") or {}
        if key in data:
            return data[key]
    return None


def _data_values(components: list[dict[str, Any]], key: str) -> list[Any]:
    return [(component.get("data") or {}).get(key) for component in components if key in (component.get("data") or {})]


def _normalize_usage_month(row: dict[str, Any]) -> dict[str, Any]:
    total = _safe_float(row.get("monthEleNum"))
    charge = _safe_float(row.get("monthEleCost"))
    return {
        "month": _normalize_ym(row.get("month")),
        "total_usage": total,
        "total_charge": charge,
        "begin_date": row.get("begDate"),
        "end_date": row.get("endDate"),
        "meter_read_time": row.get("mrDate"),
        "is_max": bool(row.get("max")),
    }


def _normalize_daily_row(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    date = str(row.get("day") or "").strip()
    if not date:
        return None
    total = _safe_float(row.get("dayElePq"), default=0.0)
    return {
        "date": date,
        "total_usage": total,
        "valley_usage": _safe_float(row.get("thisVPq"), default=0.0),
        "flat_usage": _safe_float(row.get("thisNPq"), default=0.0),
        "peak_usage": _safe_float(row.get("thisPPq"), default=0.0),
        "tip_usage": _safe_float(row.get("thisTPq"), default=0.0),
    }


def _normalize_ym(value: Any) -> str:
    text = str(value or "").strip().replace("/", "-")
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}-{text[4:]}"
    if len(text) >= 7:
        return text[:7]
    return text


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        text = str(value).strip()
        if text in ("", "-", "—", "None"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default
