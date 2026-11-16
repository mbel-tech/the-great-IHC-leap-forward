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

# ---- one sheet row is one animal -------------------------------------------
# By number, LS1 and LS01 are both row "1", and LS22 and LS22b both row "22".
# Only an animal on two rows used to be refused, so 06b wrote one row's group
# for two animals and exited 0. A row matched by two animals is the same
# mistake the other way round, and is refused the same way, naming both.
H = ["Fish", "Group"]
R = [{0: "1", 1: "control"}, {0: "22", 1: "exercise"}, {0: "5", 1: "control"}]
m = {"id_column": "Fish", "group_columns": ["Group"], "match": "number"}
j = S.join(["LS1", "LS01", "LS22", "LS22b", "LS5"], H, R, m)
chk("a row two animals match is named, with both animals",
    j.get("shared"), [("1", ["LS01", "LS1"]), ("22", ["LS22", "LS22b"])])
chk("...neither of them is joined to it", sorted(j["meta"]), ["LS5"])
chk("...and neither is called missing", j["missing"], [])
# With more columns than the ID and the group, the other columns are carried
# under their own names beside a refused row (the refusal once reused the
# column-name table for its animals: IndexError, or a column called "LS1").
H3 = ["Fish", "Group", "Tank", "Sex"]
R3 = [{0: "1", 1: "control", 2: "305", 3: "F"}, {0: "5", 1: "exercise", 2: "303", 3: "M"}]
j = S.join(["LS1", "LS01", "LS5"], H3, R3, m)
chk("a refused row leaves the other columns carried by name",
    (j["shared"], {k: v for k, v in j["meta"]["LS5"].items() if k not in S.RESERVED}),
    ([("1", ["LS01", "LS1"])], {"group": "exercise", "tank": "303", "sex": "M"}))
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "s.tsv")
    S.write_text(path, [dict(enumerate(H))] + R)
    rep = S.check(path, {"LS1": 1, "LS01": 1, "LS5": 1}, m)
    chk("check: the shared row is reported, not matched",
        (rep.get("shared"), sorted(rep["matched"])), ([("1", ["LS01", "LS1"])], ["LS5"]))
    chk("check: the words name both animals",
        any("LS1" in ln and "LS01" in ln for ln in S.words(rep)), True)
# A cell with two numbers in it is not one number: "Tank 3 fish 1" is not 31.
found, _rule, _dup = S.match_ids(["Tank 3 fish 1", "7"], ["LS31", "LS7"], "number")
chk("a cell that is not a single number is not read as one", found, {"LS7": 1})

# ---- a fill rule's `is` -----------------------------------------------------
# A string is one value. Iterated, "303" was the set {"3", "0"}: tank 3 filled
# and tank 303 did not.
H = ["Fish", "Group", "Tank"]
R = [{0: "1", 2: "3"}, {0: "2", 2: "303"}]
m = {"id_column": "Fish", "group_columns": ["Group"], "match": "number",
     "fill": [{"when": "Tank", "is": "303", "set": "Group", "to": "exercise"}]}
j = S.join(["LS1", "LS2"], H, R, m)
chk("fish_id follows the same rule: one run of digits, else none",
    [S.fish_id(a) for a in ("LS22", "LS-022", "T1F22", "LS2-1")], ["22", "022", "", ""])
chk("a string `is` is that one value",
    {a: x["treatment"] for a, x in j["meta"].items()}, {"LS1": "", "LS2": "exercise"})

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
