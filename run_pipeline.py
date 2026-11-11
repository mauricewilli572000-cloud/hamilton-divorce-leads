"""Daily run: read new Domestic Relations cases, match spouses to parcels they own, write the dashboard + GHL CSV."""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import sys
import time

import config
import court
import leads as L
import parcels as P

log = logging.getLogger("hamilton_divorce")


class Budget:
    def __init__(self, n):
        self.left = n
        self.failed = False


def make_lookup(cases: dict, budget: Budget, today: dt.date):
    def lookup(cn: str):
        if budget.left <= 0 or budget.failed:
            return None
        budget.left -= 1
        try:
            info = court.fetch_case(cn)
        except Exception as e:  # noqa: BLE001
            log.warning("%s lookup failed (%s) - stopping lookups for this run", cn, e)
            budget.failed = True
            return None
        finally:
            time.sleep(config.REQUEST_DELAY)
        if info is None:
            return None
        rec = {"found": True, "filed_date": info["filed_date"], "case_type": info.get("case_type", ""),
               "first_seen": cases.get(cn, {}).get("first_seen") or today.isoformat()}
        if court.is_kept_type(rec["case_type"]):     # only keep names/addresses for divorce-type cases
            rec.update({"caption": info.get("caption", ""), "judge": info.get("judge", ""),
                        "parties": info["parties"]})
        cases[cn] = rec
        return rec
    return lookup


def read_cases(state: dict, cases: dict, today: dt.date) -> None:
    budget = Budget(config.MAX_LOOKUPS_PER_RUN)
    lookup = make_lookup(cases, budget, today)
    years = state.setdefault("years", {})
    y = str(today.year)
    if y not in years:
        if str(today.year - 1) in years:     # new year: numbering restarts at 1
            years[y] = {"high": 0, "backfill_next": 0, "backfill_stop": 1}
        else:                                 # first ever run: find today's newest case, back-fill below it
            newest = court.find_newest(today.year, lambda cn: lookup(cn) is not None)
            if budget.failed:
                log.error("Could not reach the clerk's site; nothing read this run")
                return
            years[y] = {"high": newest, "backfill_next": newest,
                        "backfill_stop": max(1, newest - config.BACKFILL_CASES + 1)}
            log.info("Newest %s case is %s; back-filling down to %s", y, court.case_number(today.year, newest),
                     court.case_number(today.year, years[y]["backfill_stop"]))

    new = 0
    for yr in sorted(years, key=int)[-2:]:   # last year too, for late-December filings
        st = years[yr]
        n, misses = st["high"] + 1, 0
        while budget.left > 0 and not budget.failed and misses < config.MISSES_TO_STOP:
            if lookup(court.case_number(int(yr), n)):
                st["high"], misses, new = n, 0, new + 1
            else:
                misses += 1
            n += 1
    back = 0
    for yr in sorted(years, key=int):
        st = years[yr]
        while budget.left > 0 and not budget.failed and st["backfill_next"] >= st["backfill_stop"]:
            cn = court.case_number(int(yr), st["backfill_next"])
            if cn not in cases:
                lookup(cn)
                back += 1
            st["backfill_next"] -= 1
    left = sum(max(0, s["backfill_next"] - s["backfill_stop"] + 1) for s in years.values())
    log.info("Cases: %d new, %d back-filled, %d back-fill numbers left, %d lookups used",
             new, back, left, config.MAX_LOOKUPS_PER_RUN - budget.left)


def write_outputs(leads: list[dict], cases: dict, today: dt.date) -> None:
    kept = [c for c in cases.values() if c.get("found") and court.is_kept_type(c.get("case_type", ""))]
    meta = {"updated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "divorce_cases": len(kept), "homeowner_leads": len(leads),
            "hot": sum(1 for l in leads if l["tier"] == "HOT")}
    config.DOCS_DIR.mkdir(exist_ok=True)
    config.DASHBOARD_JSON.write_text(json.dumps({"meta": meta, "leads": leads}, separators=(",", ":")),
                                     encoding="utf-8")
    for path in (config.GHL_CSV, config.DOCS_CSV):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["First Name", "Last Name", "Mailing Address", "City", "State", "Postal Code",
                        "Spouse", "Property Address", "Property City", "Property Zip", "Parcel", "Market Value",
                        "Lead Type", "Case Type", "Case Number", "Filed Date", "Score", "Tier", "Match", "Tags"])
            for l in leads:
                owners = set(l["properties"][0]["owned_by"])
                for s in l["spouses"]:
                    if s["name"] not in owners:
                        continue
                    other = ", ".join(o["name"] for o in l["spouses"] if o["name"] != s["name"])
                    a = L.split_addr(s["address"])
                    parts = s["name"].split()
                    w.writerow([" ".join(parts[:-1]), parts[-1] if parts else "", a["street"].title(), a["city"],
                                a["state"], a["zip"], other, l["property_address"], l["city"], l["zip"], l["parcel"],
                                round(l["market_value"]), "Divorce", l["case_label"], l["case_number"],
                                l["filed_date"], l["score"], l["tier"], l["match"],
                                f"divorce,hamilton,{l['tier'].lower()}"])
    log.info("Leads: %d homeowner leads from %d divorce cases (%d hot)", len(leads), len(kept), meta["hot"])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = dt.date.today()
    config.DATA_DIR.mkdir(exist_ok=True)
    cases = json.loads(config.CASES_JSON.read_text()) if config.CASES_JSON.exists() else {}
    state = json.loads(config.STATE_JSON.read_text()) if config.STATE_JSON.exists() else {}

    read_cases(state, cases, today)
    config.CASES_JSON.write_text(json.dumps(cases, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    config.STATE_JSON.write_text(json.dumps(state, indent=1), encoding="utf-8")

    names, addrs = L.wanted(cases)
    if not names:
        log.info("No divorce cases yet")
        write_outputs([], cases, today)
        return 0
    try:
        path = P.download_cagis(today)
    except Exception as e:  # noqa: BLE001
        log.error("CAGIS parcel file unavailable (%s) - keeping the previous dashboard", e)
        return 0
    hits, counts = P.read_cagis(path, names, addrs)
    log.info("Parcels: %d candidate parcels for %d spouse names / %d addresses", len(hits), len(names), len(addrs))
    write_outputs(L.build_leads(cases, hits, counts, today), cases, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
