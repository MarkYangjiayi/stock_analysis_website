"""Minimal xlsx reader (stdlib zipfile + xml) for issuer NAV-history files."""
import sys, zipfile, re, xml.etree.ElementTree as ET
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
def read(path):
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for row in ET.fromstring(z.read(sheet)).iter("{%s}row" % NS["m"]):
        vals = []
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS); t = c.get("t")
            if t == "s" and v is not None: vals.append(ss[int(v.text)])
            elif t == "inlineStr": vals.append("".join(x.text or "" for x in c.iter("{%s}t" % NS["m"])))
            else: vals.append(v.text if v is not None else None)
        rows.append(vals)
    return rows
if __name__ == "__main__":
    rows = read(sys.argv[1]); print(len(rows))
    for r in rows[:8]: print(r)
    print("...", rows[-2:])
