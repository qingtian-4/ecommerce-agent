"""Shared policy parsing and calendar boundaries; no service or persistence dependency."""
from __future__ import annotations

import hashlib
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
    return {"hash": hashlib.sha256(data).hexdigest(), "windows": windows, "shipping": shipping,
            "secondary_sale_conditions": [s.strip("- ;；") for s in conditions[1].splitlines()
                                          if s.strip()], "quality_condition": quality_condition[1],
            "source": "return_policy.md",
            "version": version[1]}



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
