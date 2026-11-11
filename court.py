"""Read Domestic Relations cases from the Hamilton County Clerk of Courts, one case number at a time."""
from __future__ import annotations

import html
import logging
import re
import time

import config

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as _http
    S = _http.Session(impersonate="chrome")
except Exception:  # pragma: no cover
    import requests as _http
    S = _http.Session()
HEADERS = {"Referer": config.REFERER}


def _req(data):
    last = None
    for attempt in range(3):
        try:
            r = S.post(config.SUMMARY_URL, data=data, timeout=90, headers=HEADERS)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"case lookup failed: {last}")


def _cell(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ")).strip()


def _rows(page: str) -> list[list[str]]:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    return [[_cell(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
            for r in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S)]


def case_number(year: int, n: int) -> str:
    return f"DR{year % 100:02d}{n:05d}"


def parse_case(page: str) -> dict | None:
    """Case header + parties from the party tab. None when the case number doesn't exist (yet)."""
    info, parties = {}, []
    for cells in _rows(page):
        if len(cells) >= 2 and cells[0].endswith(":"):
            key = cells[0].rstrip(":").strip().lower()
            if key == "filed date":
                m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", cells[1])
                if m:
                    info["filed_date"] = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
            elif key == "case type":
                info["case_type"] = cells[1]
            elif key == "case caption":
                info["caption"] = cells[1]
            elif key == "judge":
                info["judge"] = cells[1]
        elif len(cells) >= 3 and re.match(r"^[PD]\s*\d+$", cells[2]):
            parties.append({"name": cells[0], "address": cells[1], "role": cells[2][0],
                            "attorney": cells[3] if len(cells) > 3 else ""})
    if not info.get("filed_date"):
        return None
    info["parties"] = parties
    return info


def fetch_case(cn: str) -> dict | None:
    return parse_case(_req({"casenumber": cn, "validate_case_number": "1", "sec": "party"}))


def is_kept_type(case_type: str) -> bool:
    t = (case_type or "").upper()
    return any(w in t for w in config.KEEP_TYPE_WORDS) and not any(w in t for w in config.SKIP_TYPE_WORDS)


def find_newest(year: int, exists) -> int:
    """Highest case number filed so far this year (binary search, about 14 lookups)."""
    lo, hi = 0, 1
    while exists(case_number(year, hi)):
        lo, hi = hi, hi * 2
        if hi > 99999:
            break
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if exists(case_number(year, mid)):
            lo = mid
        else:
            hi = mid
    return lo
