import json, re, html, time
from curl_cffi import requests
S = requests.Session(impersonate="chrome")
B = "https://www.courtclerk.org"
def out(tag, obj): print(f"@@{tag}@@ " + json.dumps(obj)[:6000], flush=True)
def txt(s): return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s))).strip()
for path in ["/records-search/case-number-search/", "/records-search/name/"]:
    try:
        r = S.get(B+path, timeout=60)
        forms = re.findall(r"<form.*?</form>", r.text, re.S)
        out("form"+path, {"status": r.status_code, "forms": [f[:3000] for f in forms]})
    except Exception as e: out("err", {"path": path, "e": str(e)})
H = {"Referer": B+"/records-search/case-number-search/"}
found = []
for yr in ["26", "25"]:
    for n in [1, 100, 1000, 2000, 3000, 3500, 4000, 4500, 5000]:
        for fmt in ["DR{y}{n:05d}", "DR{y}{n:04d}", "DR{y}0{n:04d}"]:
            cn = fmt.format(y=yr, n=n)
            try:
                r = S.post(B+"/data/case_summary.php", data={"casenumber": cn, "validate_case_number": "1", "sec": "party"}, headers=H, timeout=60)
                t = txt(r.text)
                ok = "Filed Date" in t or "Case Type" in t
                out("case", {"cn": cn, "status": r.status_code, "ok": ok, "len": len(t), "text": t[:1500]})
                if ok:
                    found.append(cn); break
            except Exception as e: out("err", {"cn": cn, "e": str(e)})
            time.sleep(1)
if found:
    cn = found[-1]
    for sec in ["", "sum", "hist", "doc", "sched", "party", "fin"]:
        try:
            r = S.post(B+"/data/case_summary.php", data={"casenumber": cn, "validate_case_number": "1", "sec": sec}, headers=H, timeout=60)
            out("sec", {"cn": cn, "sec": sec, "text": txt(r.text)[:2500], "links": re.findall(r"sec=\w+|name=\"sec\"[^>]*", r.text)[:20]})
        except Exception as e: out("err", {"sec": sec, "e": str(e)})
        time.sleep(1)
