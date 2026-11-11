"""Turn divorce cases + matched parcels into scored homeowner leads."""
from __future__ import annotations

import datetime as dt
import re

import config
from addr import normalize_address
from court import is_kept_type
from parcels import name_key, party_person, same_person

TAIL_RE = re.compile(r"^(.*?)\s+([A-Z]{2})\s+(\d{5})(?:-?\d{4})?$")
STREET_RE = re.compile(r"^(.*?\b(?:ST|STREET|AVE|AVENUE|AV|RD|ROAD|DR|DRIVE|CT|COURT|LN|LANE|PL|PLACE|BLVD|BOULEVARD|"
                       r"WAY|PIKE|TER|TERRACE|CIR|CIRCLE|PKWY|PARKWAY|HWY|TRL|TRAIL|RUN|ROW|SQ|ALY|LOOP|PT|POINT|"
                       r"XING|CV|COVE|HL|HILL|HTS)\b\.?(?:\s+(?:APT|UNIT|#|STE|LOT)\s*\S+|\s+#\S+)?)\s+(.+)$")


def split_addr(a: str) -> dict:
    """'4512 RAVENWOOD COURT CINCINNATI OH 45244' -> street / city / state / zip (best effort)."""
    a = re.sub(r"\s+", " ", a or "").strip().upper()
    m = TAIL_RE.match(a)
    if not m:
        return {"street": a, "city": "", "state": "", "zip": ""}
    pre, state, zipc = m.groups()
    sm = STREET_RE.match(pre)
    if sm:
        street, city = sm.group(1), sm.group(2)
    else:
        street, _, city = pre.rpartition(" ")
    return {"street": street.strip(), "city": city.strip().title(), "state": state, "zip": zipc}


def case_label(case_type: str) -> str:
    t = (case_type or "").upper()
    kids = "W/O" not in t and ("CHILD" in t)
    if "DISSOLUTION" in t:
        return "Dissolution" + (" with children" if kids else "")
    if "SEPARATION" in t:
        return "Legal separation"
    if "ANNULMENT" in t:
        return "Annulment"
    return "Divorce" + (" with children" if kids else "")


def spouses(case: dict) -> list[dict]:
    out = []
    for p in case.get("parties") or []:
        person = party_person(p.get("name", ""))
        if not person:
            continue
        a = split_addr(p.get("address", ""))
        key = normalize_address(a["street"]) if a["state"] == "OH" else ""
        out.append({**person, "name": p["name"].title(), "role": p.get("role", ""), "address": p.get("address", ""),
                    "street": a["street"], "city": a["city"], "state": a["state"], "zip": a["zip"],
                    "addr_key": key, "attorney": p.get("attorney", "")})
    return out


def wanted(cases: dict) -> tuple[set, set]:
    names, addrs = set(), set()
    for c in cases.values():
        if not c.get("found") or not is_kept_type(c.get("case_type", "")):
            continue
        for s in spouses(c):
            names.add(name_key(s))
            if s["addr_key"]:
                addrs.add(s["addr_key"])
    return names, addrs


def _days(iso: str, today: dt.date) -> int:
    try:
        return (today - dt.date.fromisoformat(iso[:10])).days
    except (TypeError, ValueError):
        return 9999


def match_case(sp: list[dict], by_name: dict, by_addr: dict, name_counts: dict) -> list[dict]:
    """Parcels owned by a spouse, each tagged with how sure the match is."""
    cands = {}
    for s in sp:
        for h in by_name.get(name_key(s), []) + by_addr.get(s["addr_key"], []):
            cands[h["parcel"]] = h
    out = []
    for h in cands.values():
        owners = [s for s in sp if any(same_person(s, o) for o in h.get("owners", []))]
        if not owners:
            continue        # someone else owns it (a landlord, a parent) - not their home to sell
        home = next((s for s in sp if s["addr_key"] and s["addr_key"] in h["keys"]), None)
        at_home = home is not None
        joint = len(sp) >= 2 and len(owners) >= 2
        if not at_home and not joint:
            if any(name_counts.get(name_key(s), 99) > config.NAME_ONLY_MAX_PARCELS for s in owners):
                continue    # common name, no address to back it up
        match = ("home at spouse's address" if at_home else "owned by both spouses" if joint else "name match")
        out.append({**{k: v for k, v in h.items() if k not in ("owners", "keys")},
                    "match": match, "owned_by": [s["name"] for s in owners],
                    "city": (home or {}).get("city", ""), "zip": (home or {}).get("zip", "") or h.get("zip", "")})
    rank = {"home at spouse's address": 0, "owned by both spouses": 1, "name match": 2}
    return sorted(out, key=lambda p: (rank[p["match"]], -p["market_value"]))


def score(case: dict, sp: list[dict], props: list[dict], today: dt.date) -> tuple[int, list[str]]:
    pts, why = 50, ["Divorce / dissolution filed +50"]
    top = props[0]
    if top["match"] == "home at spouse's address":
        pts += 20; why.append("Owns the home a spouse lives at +20")
    if any(p["match"] == "owned by both spouses" or len(p["owned_by"]) >= 2 for p in props):
        pts += 10; why.append("Owned jointly by both spouses +10")
    if top["match"] == "name match":
        pts -= 10; why.append("Matched by name only -10")
    keys = {s["addr_key"] or s["street"] for s in sp if s["street"]}
    if len(sp) >= 2 and len(keys) >= 2:
        pts += 10; why.append("Spouses already living apart +10")
    if any(s["state"] and s["state"] != "OH" for s in sp):
        pts += 5; why.append("A spouse lives out of state +5")
    if any(p["in_foreclosure"] for p in props):
        pts += 15; why.append("Foreclosure flag on a property +15")
    if any(p["delinquent_taxes"] > 0 for p in props):
        pts += 10; why.append("Delinquent taxes +10")
    if len(props) > 1:
        pts += 5; why.append(f"{len(props)} properties +5")
    d = _days(case.get("filed_date", ""), today)
    if d <= 30:
        pts += 10; why.append("Filed in the last 30 days +10")
    elif d <= 90:
        pts += 5; why.append("Filed in the last 90 days +5")
    return max(0, min(100, pts)), why


def build_leads(cases: dict, hits: list[dict], name_counts: dict, today: dt.date) -> list[dict]:
    by_name, by_addr = {}, {}
    for h in hits:
        for o in h.get("owners", []):
            by_name.setdefault(name_key(o), []).append(h)
        for k in h.get("keys", []):
            by_addr.setdefault(k, []).append(h)
    leads = []
    for cn, c in cases.items():
        if not c.get("found") or not is_kept_type(c.get("case_type", "")):
            continue
        if _days(c.get("filed_date", ""), today) > config.KEEP_DAYS:
            continue
        sp = spouses(c)
        props = match_case(sp, by_name, by_addr, name_counts)
        if not props:
            continue
        pts, why = score(c, sp, props, today)
        top = props[0]
        leads.append({
            "lead_id": cn, "case_number": cn, "case_type": c.get("case_type", ""),
            "case_label": case_label(c.get("case_type", "")), "filed_date": c.get("filed_date", ""),
            "judge": c.get("judge", ""),
            "spouses": [{"name": s["name"], "address": s["address"].title(), "role": s["role"],
                         "attorney": s["attorney"].replace("/", " ").title()} for s in sp],
            "property_address": top["address"].title(),
            "city": top["city"], "zip": top["zip"],
            "parcel": top["parcel"], "owner_name": top["owner_name"], "mailing_address": top["mailing_address"],
            "market_value": top["market_value"],
            "delinquent_taxes": sum(p["delinquent_taxes"] for p in props),
            "in_foreclosure": any(p["in_foreclosure"] for p in props),
            "match": top["match"], "properties": props, "property_count": len(props),
            "score": pts, "score_reasons": why, "tier": "HOT" if pts >= 85 else "WARM" if pts >= 65 else "COLD",
            "case_url": config.CASE_URL.format(case=cn),
            "auditor_url": config.AUDITOR_URL.format(parcel=top["parcel"], year=today.year - 1) if top["parcel"] else "",
            "added_date": c.get("first_seen", ""),
        })
    leads.sort(key=lambda l: l["filed_date"], reverse=True)   # newest first within the same score
    leads.sort(key=lambda l: l["score"], reverse=True)
    return leads
