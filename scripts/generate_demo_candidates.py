"""Deterministic policy/logistics candidates, never loaded into an active application."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate(out, *, spec_path=ROOT / "data/demo_generation_v1/spec.json",
             business_path=ROOT / "data/demo/shop_realism_v1_1/business.json"):
    spec, business = json.loads(spec_path.read_text()), json.loads(business_path.read_text())
    out.mkdir(parents=True, exist_ok=False)
    zone = timezone(timedelta(hours=8))
    snapshot = datetime.combine(datetime.fromisoformat(business["reference_date"]).date(),
                                time(12), zone)
    rows = []
    for order in business["orders"]:
        if not order["shipped_at"]:
            rows.append(dict(order_id=order["id"], trace=[], reason="not_shipped"))
            continue
        seed = hashlib.sha256(f"{spec['seed']}:{order['id']}".encode()).hexdigest()
        rng = random.Random(int(seed, 16))
        route = rng.choice(spec["routes"])
        origin, transit, destination = route["cities"]
        day = datetime.fromisoformat(order["shipped_at"]).date()
        start = datetime.combine(day, time(7 + rng.randrange(4), rng.randrange(60)), zone)
        nodes = [(origin + "仓库", "已揽收"), (origin + "分拨中心", "已出库"),
                 (transit + "转运中心", "运输中"), (destination + "分拨中心", "已到达"),
                 (destination + "配送网点", "派送中"), (destination + "配送网点", "已签收")]
        if order["delivered_at"]:
            end = datetime.combine(datetime.fromisoformat(order["delivered_at"]).date(),
                                   time(15 + rng.randrange(5), rng.randrange(60)), zone)
        else:
            nodes = nodes[:rng.randint(2, 4)]
            end = min(snapshot-timedelta(hours=rng.randint(1, 6)),
                      start+timedelta(hours=rng.randint(8, 20), minutes=rng.randrange(60)))
        if end <= start or end > snapshot:
            raise ValueError("Order timestamps cannot support this synthetic route")
        weights = [rng.randint(2, 12) for _ in nodes[1:]]
        elapsed, total = 0, sum(weights)
        trace = []
        for i, (place, status) in enumerate(nodes):
            if i:
                elapsed += weights[i-1]
            timestamp = start + (end-start)*elapsed/total
            trace.append(dict(time=timestamp.isoformat(timespec="seconds"), status=status,
                              location=place, text=f"模拟包裹：{place}，{status}"))
        rows.append(dict(order_id=order["id"], route_id=route["id"], trace=trace,
                         synthetic=True, timestamp_source="derived_from_order_dates"))
    payload = dict(version=spec["version"], seed=spec["seed"], status="candidate_not_active",
                   logical_snapshot=snapshot.isoformat(), policy=spec["policy"], logistics=rows)
    (out / "candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
    manifest = dict(version=spec["version"], seed=spec["seed"], paid_requests=0,
        spec_sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        business_sha256=hashlib.sha256(business_path.read_bytes()).hexdigest(),
        candidates_sha256=hashlib.sha256((out / "candidates.json").read_bytes()).hexdigest(),
        activation="none; separate review and version required")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    generate(parser.parse_args().out)
