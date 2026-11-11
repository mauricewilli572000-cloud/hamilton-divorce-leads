"""Match divorcing spouses to Hamilton County parcels they own (CAGIS quarterly parcel CSV)."""
from __future__ import annotations

import csv
import datetime as dt
import logging
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

import config
from addr import normalize_address

log = logging.getLogger(__name__)
csv.field_size_limit(10_000_000)

try:
    from curl_cffi import requests as _http
    S = _http.Session(impersonate="chrome")
except Exception:  # pragma: no cover
    import requests as _http
    S = _http.Session()


# ----------------------------------------------------------------- names
def is_entity(name: str) -> bool:
    n = f" {re.sub(r'[^A-Z0-9 ]', ' ', (name or '').upper())} "
    return bool(re.search(r"\d", n)) or any(f" {w} " in n for w in config.ENTITY_WORDS)


def _tokens(s: str) -> list[str]:
    return [t for t in re.sub(r"[^A-Z ]", " ", (s or "").upper()).split() if t not in config.NAME_SUFFIXES]


def party_person(name: str) -> dict | None:
    """Court party 'KENNETH CHASE EASTERLING' -> {first: KENNETH, middle: C, last: EASTERLING}."""
    if is_entity(name):
        return None
    t = _tokens(name)
    if len(t) < 2:
        return None
    return {"first": t[0], "middle": t[1][0] if len(t) > 2 else "", "last": t[-1]}


def owner_people(owner: str) -> list[dict]:
    """Auditor owner 'SAGGUS CHARLIE & ROBERTA' -> [SAGGUS CHARLIE, SAGGUS ROBERTA].

    Format is LAST FIRST [MIDDLE] for the first person; later people after '&' are
    FIRST [MIDDLE] [LAST] and share the first person's last name unless they give their own.
    """
    if not owner or is_entity(owner):
        return []
    people, first_last = [], ""
    for i, seg in enumerate(p for p in re.split(r"\s*&\s*|\s+AND\s+", owner.upper()) if p.strip()):
        t = _tokens(seg)
        if not t:
            continue
        if i == 0:
            if len(t) < 2:
                return []
            first_last = t[0]
            people.append({"last": t[0], "first": t[1], "middle": t[2][0] if len(t) > 2 else ""})
        else:
            if len(t) >= 3:      # FIRST MIDDLE LAST
                people.append({"last": t[-1], "first": t[0], "middle": t[1][0]})
            elif len(t) == 2:    # FIRST LAST  or  FIRST MIDDLE-INITIAL
                if len(t[1]) == 1:
                    people.append({"last": first_last, "first": t[0], "middle": t[1]})
                else:
                    people.append({"last": t[1], "first": t[0], "middle": ""})
            else:
                people.append({"last": first_last, "first": t[0], "middle": ""})
    return people


def same_person(party: dict, owner: dict) -> bool:
    if party["last"] != owner["last"] or party["first"] != owner["first"]:
        return False
    # Conflicting middle initials = different people (e.g. JOHN A SMITH vs SMITH JOHN R).
    return not (party["middle"] and owner["middle"] and party["middle"] != owner["middle"])


def name_key(p: dict) -> tuple[str, str]:
    return (p["last"], p["first"])


# ----------------------------------------------------------------- CAGIS file
NAME_RE = re.compile(r"Hamilton[ _%20]*County[ _%20]*Parcels[ _%20]*([A-Za-z]+)[ _%20]*(\d{4})\.csv", re.I)
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def _get(url, timeout=300):
    last = None
    for attempt in range(4):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def _file_date(name: str) -> tuple[int, int]:
    m = NAME_RE.search(unquote(name))
    if not m or m.group(1).lower() not in MONTHS:
        return (0, 0)
    return (int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1)


def find_cagis_url(today: dt.date) -> str:
    try:
        page = _get(config.CAGIS_FOLDER, timeout=60).text
        names = [h for h in re.findall(r'href=["\']([^"\']+\.csv)["\']', page, re.I) if NAME_RE.search(unquote(h))]
        if names:
            return urljoin(config.CAGIS_FOLDER, max(names, key=_file_date))
    except Exception as e:  # noqa: BLE001
        log.info("CAGIS folder listing not readable (%s) - trying recent month names", e)
    for back in range(15):
        y, m = today.year, today.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        url = config.CAGIS_FOLDER + quote(f"Hamilton County Parcels {MONTHS[m - 1].title()} {y}.csv")
        try:
            if S.head(url, timeout=60).status_code == 200:
                return url
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("No 'Hamilton County Parcels <Month> <Year>.csv' found at CAGIS")


def download_cagis(today: dt.date) -> Path:
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.RAW_DIR / "cagis_parcels.csv"
    url = find_cagis_url(today)
    log.info("Downloading CAGIS parcels: %s", unquote(url))
    dest.write_bytes(_get(url, timeout=900).content)
    log.info("  %.0f MB", dest.stat().st_size / 1e6)
    return dest


def _num(v) -> float:
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v or "")) or 0)
    except ValueError:
        return 0.0


def _g(row, k) -> str:
    return re.sub(r"\s+", " ", str(row.get(k) or "")).strip()


def parcel_info(row) -> dict:
    no, st, sf, dr = _g(row, "ADDRNO"), _g(row, "ADDRST"), _g(row, "ADDRSF"), _g(row, "LOC_ST_DIR")
    book, page, parcel = _g(row, "BOOK"), _g(row, "PAGE"), _g(row, "PARCEL")
    parcel_id = _g(row, "PROPTYID") or (f"{book.zfill(3)}{page.zfill(4)}{parcel.zfill(4)}00"
                                        if book and page and parcel else "")
    own1 = " ".join(p for p in (_g(row, "OWNAD1"), _g(row, "OWNAD1A")) if p)
    own_csz = " ".join(p for p in (_g(row, "OWNADCITY"), _g(row, "OWNADSTATE"), _g(row, "OWNADZIP")[:5]) if p)
    mail = ", ".join(p for p in ((own1, own_csz) if own1 and own_csz else (_g(row, "MLADR1"), _g(row, "MLADR2"))) if p)
    return {
        "parcel": re.sub(r"\D", "", parcel_id),
        "address": " ".join(p for p in (no, dr, st, sf) if p),
        "keys": sorted({k for k in (normalize_address(" ".join(p for p in (no, st, sf) if p)),
                                    normalize_address(" ".join(p for p in (no, dr, st, sf) if p))) if k}),
        "zip": _g(row, "ZIPCODE")[:5] or _g(row, "ZIP")[:5],
        "owner_name": " ".join(p for p in (_g(row, "OWNNM1"), _g(row, "OWNNM2")) if p),
        "mailing_address": mail,
        "market_value": _num(row.get("MKTLND")) + _num(row.get("MKTIMP")),
        "land_use": _g(row, "CLASS"),
        "in_foreclosure": _g(row, "FORECL_FLAG").upper() in ("Y", "YES", "1", "T"),
        "delinquent_taxes": _num(row.get("DELQ_TAXES") or row.get("DELIQ_TAXES")),
        "homestead": _g(row, "HMSD_FLAG").upper() in ("Y", "YES", "1", "T"),
        "has_mortgage_bank": bool(_g(row, "BANKCD").strip("0")),
    }


def scan_parcels(rows, wanted_names: set[tuple[str, str]], wanted_addrs: set[str]
                 ) -> tuple[list[dict], dict[tuple[str, str], int]]:
    """Parcels owned by a wanted (last, first) name or sitting at a wanted address,
    plus how many parcels county-wide carry each wanted name (to judge how common it is)."""
    hits, name_counts, seen = [], {}, set()
    for row in rows:
        owner = " ".join(p for p in (_g(row, "OWNNM1"), _g(row, "OWNNM2")) if p)
        people = owner_people(owner)
        names = {name_key(p) for p in people} & wanted_names
        for n in names:
            name_counts[n] = name_counts.get(n, 0) + 1
        info = None
        if names:
            info = parcel_info(row)
        else:
            no, st, sf, dr = _g(row, "ADDRNO"), _g(row, "ADDRST"), _g(row, "ADDRSF"), _g(row, "LOC_ST_DIR")
            k1 = normalize_address(" ".join(p for p in (no, st, sf) if p))
            k2 = normalize_address(" ".join(p for p in (no, dr, st, sf) if p))
            if (k1 and k1 in wanted_addrs) or (k2 and k2 in wanted_addrs):
                info = parcel_info(row)
        if info and info["parcel"] not in seen:
            seen.add(info["parcel"])
            info["owners"] = people
            hits.append(info)
    return hits, name_counts


def read_cagis(path: Path, wanted_names, wanted_addrs):
    with path.open(encoding="latin-1", newline="") as f:
        return scan_parcels(csv.DictReader(f), wanted_names, wanted_addrs)
