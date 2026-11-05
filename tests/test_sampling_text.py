"""The sampling sheet as a text table: .csv/.tsv files and a clipboard paste.

Run:  python tests/test_sampling_text.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import ls_sampling as S  # noqa: E402

fails = 0


def chk(what, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else f"\n     got {got!r}\n     want {want!r}"))


# Copied from Excel: tab separated, dotted dates, Windows line ends.
PASTE = ("ID\tPair\tGroup\tTank\tDate to tank\tTreatment\tSex\r\n"
         "CD5\t12\tControl\t13\t25.11.04\tDemonstrator\tM\r\n"
         "CO5\t12\tControl\t12\t25.11.04\tObserver\tM\r\n"
         "ND5\t16\tNegative\t3\t25.11.17\tDemonstrator\tM\r\n"
         "\r\n")
rows = S.parse_text(PASTE)
chk("a paste: header plus rows, blank line dropped", len(rows), 4)
chk("a paste: dotted dates stay text", rows[1][4], "25.11.04")

for sep, ending in ((",", ".csv"), (";", ".csv"), ("\t", ".tsv")):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sheet" + ending)
        body = PASTE.replace("\t", sep)
        if sep == ",":
            body = body.replace("Demonstrator", '"Demo, strator"', 1)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(body)
        chk(f"{sep!r}{ending}: is a text table", S.is_text(path), True)
        chk(f"{sep!r}{ending}: one sheet", len(S.sheet_names(path)), 1)
        headers, data = S.table(path, "anything")
        chk(f"{sep!r}{ending}: headers", headers,
            ["ID", "Pair", "Group", "Tank", "Date to tank", "Treatment", "Sex"])
        if sep == ",":
            chk("a quoted cell keeps its comma", data[0][5], "Demo, strator")
        m = {"id_column": "ID", "group_columns": ["Group", "Treatment"], "match": "exact"}
        rep = S.check(path, {"CD5": 1, "CO5": 2, "ND5": 1}, m)
        chk(f"{sep!r}{ending}: all three fish matched by name",
            (rep["error"], sorted(rep["matched"])), (None, ["CD5", "CO5", "ND5"]))

with tempfile.TemporaryDirectory() as tmp:
    path = S.write_text(os.path.join(tmp, S.PASTED_NAME), rows)
    chk("a paste saved and read back is the same table", S.read_sheet(path), rows)
    chk("no .part file is left", os.listdir(tmp), [S.PASTED_NAME])
    with open(os.path.join(tmp, "ansi.csv"), "wb") as f:
        f.write("ID;Gruppe\nCD5;Kontroll\u00e9\n".encode("cp1252"))
    chk("an Excel ANSI csv is read", S.table(os.path.join(tmp, "ansi.csv"))[1][0][1],
        "Kontroll\u00e9")
    with open(os.path.join(tmp, "one.csv"), "w") as f:
        f.write("ID\n")
    rep = S.check(os.path.join(tmp, "one.csv"), {"CD5": 1})
    chk("a one-column file is an error, not a crash", bool(rep["error"]), True)

for bad in ("", "hello", "ID\tGroup\n"):
    try:
        S.parse_text(bad)
        chk(f"{bad!r} is refused", "accepted", "ValueError")
    except ValueError:
        chk(f"{bad!r} is refused", True, True)

chk("an .xlsx is not a text table", S.is_text("D:/x/sampling.xlsx"), False)

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
