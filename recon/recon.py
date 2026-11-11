import json, re, html, time
from curl_cffi import requests
S = requests.Session(impersonate="chrome")
B = "https://www.courtclerk.org"
H = {"Referer": B+"/records-search/case-number-search/"}
def out(tag, obj): print(f"@@{tag}@@ " + json.dumps(obj)[:8000], flush=True)
def body(page):
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S|re.I)
    return page
def txt(s): return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s))).strip()
def get(cn, sec="party"):
    time.sleep(3)
    r = S.post(B+"/data/case_summary.php", data={"casenumber": cn, "validate_case_number": "1", "sec": sec}, headers=H, timeout=60)
    return body(r.text)
def exists(cn):
    return "Filed Date" in txt(get(cn, "sum"))
for cn in ["DR2601559", "DR2601540"]:
    for sec in ["sum", "party"]:
        p = get(cn, sec)
        i = p.find("Filed Date")
        main = p[max(0, i-3000): i+8000]
        rows = [[txt(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)] for r in re.findall(r"<tr[^>]*>(.*?)</tr>", main, re.S)]
        out("page", {"cn": cn, "sec": sec, "rows": rows[:60]})
