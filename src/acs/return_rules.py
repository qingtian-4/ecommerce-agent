"""Shared policy parsing and calendar boundaries; no service or persistence dependency."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime

from .config import Settings


def read_return_policy(settings: Settings) -> dict:
    path = settings.docs_dir / "return_policy.md"
    data = path.read_bytes()
    text = data.decode("utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    windows, shipping = {}, {}
    for key, heading in (("no_reason", "一、七天无理由退货"), ("quality", "二、质量问题退货")):
        section = next((s for s in sections if s.startswith(heading)), "")
        match = re.search(r"签收之日起 \*\*(\d+) 天内\*\*", section)
        table_name = "七天无理由退货" if key == "no_reason" else "质量问题退货"
        table = re.search(rf"\| {table_name} \| (\d+) 天 \| 签收日 \| ([^|]+) \|", text)
        if not match or not table or match[1] != table[1]:
            raise ValueError("退货政策的期限结构不一致，需核对政策后再办理")
        windows[key] = int(match[1])
        shipping[key] = table[2].strip()
    conditions = re.search(r"不影响二次销售指：\s*(.*?)\n\*\*退货运费", text, re.DOTALL)
    quality = next(s for s in sections if s.startswith("二、质量问题退货"))
    quality_condition = re.search(r"若商品出现(.+?)，可选择退货", quality)
    if not conditions or not quality_condition or "质量问题需上传照片或视频" not in text:
        raise ValueError("政策条件格式变化，请核对后台规则与政策")
    version = re.search(r"^version: (.+)$", text, re.MULTILINE)
    if version is None:
        raise ValueError("退货政策缺少版本，不能生成申请预览")
    scope = re.search(r"本政策适用于本店铺全部([^。\n]+)类商品。", text)
    categories = re.split(r"与|、", scope[1]) if scope else []
    return {"hash": hashlib.sha256(data).hexdigest(), "windows": windows, "shipping": shipping,
            "applicable_categories": categories,
            "secondary_sale_conditions": [s.strip("- ;；") for s in conditions[1].splitlines()
                                          if s.strip()], "quality_condition": quality_condition[1],
            "source": "return_policy.md",
            "version": version[1]}


def policy_scope(settings: Settings, category: str) -> dict:
    """Evidence availability, not a legal determination of warranty/return rights."""
    try:
        policy = read_return_policy(settings)
    except (OSError, ValueError):
        policy = {"applicable_categories": []}
    path = settings.docs_dir / "warranty_policy.md"
    text = path.read_text() if path.exists() else ""
    covered = set(re.findall(r"^\| ([^|]+) \| \d+ 个月 \| 签收日 \|", text, re.M))
    warranty = category in covered or (category == "充电配件"
                                      and {"充电器", "充电宝"} <= covered)
    scope = {"return": category in policy["applicable_categories"], "warranty": warranty,
             "installation": False, "exchange": False}
    if consultation_policy(settings, category):
        scope["consultation"] = True
    return scope


UNSUPPORTED_POLICY = ("缺少适用于此商品类别的售后政策，不能判断退货或保修资格，"
                      "也不能承诺安装、换货。")

CONSULTATION_NOTICE = ("仅支持模拟政策咨询，规则见appliance_consultation；"
                       "不代表真实资格，不支持家电预览、申请、退款、安装或维修换货办理。")


def consultation_policy(settings: Settings, category: str | None = None) -> dict | None:
    """Separate evidence contract; this function cannot enable operational rules."""
    try:
        value = json.loads((settings.docs_dir / "appliance_consultation.json").read_text())
        document = (settings.docs_dir / "appliance_consultation.md").read_bytes()
        if (value.get("mode") != "consultation_only"
                or value.get("consultation_enabled") is not True
                or value.get("application_enabled") is not False
                or value.get("simulation_only") is not True
                or value.get("document_sha256") != hashlib.sha256(document).hexdigest()
                or not isinstance(value.get("categories"), list)
                or any(not isinstance(c, str) for c in value["categories"])
                or any(type(value.get(k)) is not int or value[k] <= 0 for k in
                       ("no_reason_days", "quality_return_days", "warranty_months"))
                or category is not None and category not in value["categories"]):
            return None
        return value
    except (OSError, ValueError, AttributeError):
        return None


def policy_notice(settings: Settings, category: str | None) -> str:
    return CONSULTATION_NOTICE if consultation_policy(settings, category) else UNSUPPORTED_POLICY



def delivery_days(value: str | None, today: date) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        day = (date.fromisoformat(value) if len(value) == 10
               else datetime.fromisoformat(value).date())
        return (today - day).days
    except ValueError:
        return None


def within_window(days: int | None, window: int | None) -> bool:
    return days is not None and window is not None and 0 <= days <= window
