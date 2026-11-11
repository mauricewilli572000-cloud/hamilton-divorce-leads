# Hamilton County divorce leads

Homeowners in Hamilton County (Cincinnati), Ohio who just filed for divorce, dissolution or legal separation.

**Dashboard:** https://mauricewilli572000-cloud.github.io/hamilton-divorce-leads/

## How it works

1. **Cases**: reads new Domestic Relations cases from the Hamilton County Clerk of Courts (courtclerk.org).
   Case numbers run in order (`DR2601559`), so each day it checks only the numbers after the last one it saw.
   One request per case gives the filed date, case type, and both spouses' names and addresses.
   Only divorce, dissolution, legal separation and annulment cases are kept. Protection orders, custody
   and support cases are skipped, and no names are stored for them.
2. **Homes**: matches each spouse to the CAGIS county parcel file:
   - **home at spouse's address**: a spouse owns the parcel at the address they gave the court (strongest)
   - **owned by both spouses**: both names are on the deed
   - **name match**: one spouse's name is on a parcel elsewhere, kept only for rare names (3 parcels or fewer county-wide)
   Spouses who rent (the parcel at their address belongs to someone else) are dropped.
3. **Score**: 50 base, +20 owns the home they live in, +10 joint owners, +10 already living apart,
   +5 a spouse out of state, +15 foreclosure flag, +10 delinquent taxes, +10 filed in the last 30 days.
   HOT is 85+, WARM is 65+.

## Light on the court site

The clerk's site does not allow bulk downloads, so this scraper stays small: at most 100 lookups a run,
3 seconds apart. After a one-time back-fill of about 90 days (spread over the first week of runs) it only
reads the 5-15 new cases filed each day.

## Files

- `docs/hamilton_divorce.json`: dashboard data (also read by `ohio-lead-stacker`)
- `docs/ghl_hamilton_divorce.csv`: GoHighLevel import, one row per spouse on the deed
- `data/cases.json`, `data/state.json`: cases already read and where the scan left off

Runs daily at about 8:17 AM Eastern (GitHub Actions). Run it by hand from the Actions tab: **Run workflow**.
