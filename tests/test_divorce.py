import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import court  # noqa: E402
import leads as L  # noqa: E402
import parcels as P  # noqa: E402

PAGE = """<table><tr><td>Case Number:</td><td>DR2601559</td></tr>
<tr><td>Case Caption:</td><td>KENNETH CHASE EASTERLING vs. ELLEN MICHELLE ISAAC</td></tr>
<tr><td>Judge:</td><td>AMY L. SEARCY</td></tr>
<tr><td>Filed Date:</td><td>11/10/2026</td></tr>
<tr><td>Case Type:</td><td>J220 - DISSOLUTION OF MARRIAGE W/O CHILDREN</td></tr></table>
<table><tr><th>Party/Attorney Info Name</th><th>Address</th><th>Party</th><th>Attorney</th></tr>
<tr><td>KENNETH CHASE EASTERLING</td><td>4512 RAVENWOOD COURT CINCINNATI OH 45244</td><td>P 1</td><td>MORRIS/TIMOTHY/J</td></tr>
<tr><td>ELLEN MICHELLE ISAAC</td><td>412 E 13TH ST CINCINNATI OH 45202</td><td>D 1</td><td></td></tr></table>"""

TODAY = dt.date(2026, 11, 11)


def row(no, st, sf, owner, **kw):
    r = {"ADDRNO": no, "ADDRST": st, "ADDRSF": sf, "OWNNM1": owner, "PROPTYID": kw.pop("pid", "0010001000100"),
         "MKTLND": "20000", "MKTIMP": "150000"}
    r.update(kw)
    return r


def test_parse_case():
    c = court.parse_case(PAGE)
    assert c["filed_date"] == "2026-11-10"
    assert c["case_type"].startswith("J220")
    assert [p["role"] for p in c["parties"]] == ["P", "D"]
    assert c["parties"][0]["address"].startswith("4512 RAVENWOOD")
    assert court.parse_case("<html>No case found</html>") is None


def test_case_types():
    assert court.is_kept_type("J210 - TERMINATION WITH CHILD(REN)/ALIMONY")
    assert court.is_kept_type("J220 - DISSOLUTION OF MARRIAGE W/O CHILDREN")
    assert not court.is_kept_type("DOMESTIC VIOLENCE CIVIL PROTECTION ORDER")
    assert not court.is_kept_type("CUSTODY")
    assert L.case_label("J220 - DISSOLUTION OF MARRIAGE W/O CHILDREN") == "Dissolution"
    assert L.case_label("J210 - TERMINATION WITH CHILD(REN)/ALIMONY") == "Divorce with children"


def test_find_newest():
    assert court.find_newest(2026, lambda cn: int(cn[4:]) <= 1559) == 1559
    assert court.find_newest(2027, lambda cn: False) == 0


def test_names():
    assert P.party_person("KENNETH CHASE EASTERLING") == {"first": "KENNETH", "middle": "C", "last": "EASTERLING"}
    assert P.party_person("JOHN SMITH JR") == {"first": "JOHN", "middle": "", "last": "SMITH"}
    assert P.party_person("ACME HOLDINGS LLC") is None
    people = P.owner_people("SAGGUS CHARLIE & ROBERTA")
    assert [(p["last"], p["first"]) for p in people] == [("SAGGUS", "CHARLIE"), ("SAGGUS", "ROBERTA")]
    people = P.owner_people("GUY JOHN FREDERICK GUY & ANNA RAE JOHNSON")
    assert ("JOHNSON", "ANNA") in [(p["last"], p["first"]) for p in people]
    assert P.owner_people("100 PARKSON PL LLC") == []
    assert P.same_person({"last": "ESTES", "first": "STEVE", "middle": "H"}, {"last": "ESTES", "first": "STEVE", "middle": ""})
    assert not P.same_person({"last": "SMITH", "first": "JOHN", "middle": "A"}, {"last": "SMITH", "first": "JOHN", "middle": "R"})


def test_split_addr():
    a = L.split_addr("4512 RAVENWOOD COURT CINCINNATI OH 45244")
    assert a == {"street": "4512 RAVENWOOD COURT", "city": "Cincinnati", "state": "OH", "zip": "45244"}


def _cases(page=PAGE):
    c = court.parse_case(page)
    return {"DR2601559": {"found": True, "first_seen": "2026-11-11", **c}}


def test_home_at_spouse_address():
    cases = _cases()
    names, addrs = L.wanted(cases)
    rows = [row("4512", "RAVENWOOD", "CT", "EASTERLING KENNETH C"),          # their home
            row("412", "E 13TH", "ST", "SOME LANDLORD LLC", pid="0020002000200"),  # ellen rents
            row("99", "OAK", "ST", "JONES MARY", pid="0030003000300")]       # unrelated
    hits, counts = P.scan_parcels(rows, names, addrs)
    out = L.build_leads(cases, hits, counts, TODAY)
    assert len(out) == 1
    l = out[0]
    assert l["match"] == "home at spouse's address"
    assert l["property_count"] == 1
    assert l["city"] == "Cincinnati" and l["zip"] == "45244"
    assert l["score"] >= 85 and l["tier"] == "HOT"


def test_renters_and_common_names_are_dropped():
    cases = _cases()
    names, addrs = L.wanted(cases)
    rows = [row("4512", "RAVENWOOD", "CT", "LANDLORD JAMES")] + \
           [row(str(i), "ELM", "ST", "EASTERLING KENNETH", pid=f"00900{i:02d}000100") for i in range(5)]
    hits, counts = P.scan_parcels(rows, names, addrs)
    assert L.build_leads(cases, hits, counts, TODAY) == []


def test_rare_name_match_kept_but_marked():
    cases = _cases()
    names, addrs = L.wanted(cases)
    hits, counts = P.scan_parcels([row("77", "HILL", "RD", "ISAAC ELLEN M")], names, addrs)
    out = L.build_leads(cases, hits, counts, TODAY)
    assert out and out[0]["match"] == "name match"
    assert any("name only" in r for r in out[0]["score_reasons"])


def test_name_match_with_other_coowner_dropped():
    cases = _cases()
    names, addrs = L.wanted(cases)
    hits, counts = P.scan_parcels([row("77", "HILL", "RD", "ISAAC ELLEN M & ROBERT JONES")], names, addrs)
    assert L.build_leads(cases, hits, counts, TODAY) == []


def test_city_from_owner_address():
    cases = _cases()
    names, addrs = L.wanted(cases)
    r = row("8", "PINE", "LN", "EASTERLING KENNETH & ELLEN", OWNAD1="8 PINE LANE", OWNADCITY="LOVELAND",
            OWNADSTATE="OH", OWNADZIP="45140")
    hits, counts = P.scan_parcels([r], names, addrs)
    out = L.build_leads(cases, hits, counts, TODAY)
    assert out[0]["match"] == "owned by both spouses"
    assert (out[0]["city"], out[0]["zip"]) == ("Loveland", "45140")
