"""Hamilton County (Cincinnati), OH divorce leads - homeowners going through a divorce or dissolution.

Cases:  Hamilton County Clerk of Courts (courtclerk.org), Domestic Relations.
        Case numbers run in order (DR2601559 = 2026, case 1559). One request per case:
        POST /data/case_summary.php  casenumber=DR26xxxxx, sec=party
        -> filed date, case type, both spouses' names and addresses.
Owners: CAGIS quarterly Hamilton County parcel CSV (owner names, site and mailing addresses).

The clerk's site forbids bulk data mining, so this scraper stays small on purpose:
it only reads brand-new case numbers each day (about 5-15) plus a slow one-time back-fill.
"""
from pathlib import Path

BASE = "https://www.courtclerk.org"
SUMMARY_URL = BASE + "/data/case_summary.php"
REFERER = BASE + "/records-search/case-number-search/"
CASE_URL = BASE + "/data/case_summary.php?casenumber={case}&sec=party&validate_case_number=1"

MAX_LOOKUPS_PER_RUN = 100   # new cases first, then back-fill; keeps traffic near normal-user levels
REQUEST_DELAY = 3.0         # seconds between requests
MISSES_TO_STOP = 3          # this many missing case numbers in a row = we've reached the newest case
BACKFILL_CASES = 550        # on first run, also read about the last 90 days of cases (spread over several runs)
KEEP_DAYS = 365             # leads older than this drop off the dashboard

CAGIS_FOLDER = "https://cagis.org/Opendata/Quarterly_GIS_Data/"
AUDITOR_URL = "https://wedge.hcauditor.org/view/re/{parcel}/{year}/summary"

# Case types we keep (matched against the clerk's case type text). Everything else is skipped.
KEEP_TYPE_WORDS = ["DISSOLUTION", "TERMINATION", "DIVORCE", "LEGAL SEPARATION", "ANNULMENT"]
SKIP_TYPE_WORDS = ["DOMESTIC VIOLENCE", "PROTECTION", "CIVIL STALKING", "UIFSA", "REGISTRATION OF FOREIGN"]

# A spouse matched to a parcel by name only (not at their own address) is trusted only when
# the name is rare: at most this many parcels in the county carry that last + first name.
NAME_ONLY_MAX_PARCELS = 3

ENTITY_WORDS = ["LLC", "L L C", "INC", "CORP", "COMPANY", "LTD", "TRUST", "TRUSTEE", "PROPERTIES", "HOLDINGS",
                "INVESTMENTS", "BANK", "CHURCH", "ASSOCIATION", "PARTNERS", "LP", "CITY OF", "BOARD OF",
                "ESTATE OF", "HOUSING", "AUTHORITY", "MINISTRIES", "FOUNDATION", "VILLAGE OF", "TOWNSHIP"]
NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "JR.", "SR."}

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
CASES_JSON = DATA_DIR / "cases.json"
STATE_JSON = DATA_DIR / "state.json"
DASHBOARD_JSON = DOCS_DIR / "hamilton_divorce.json"
GHL_CSV = DATA_DIR / "ghl_hamilton_divorce.csv"
DOCS_CSV = DOCS_DIR / "ghl_hamilton_divorce.csv"
