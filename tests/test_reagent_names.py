"""Reagent controls: the config keys, the file-name scan and the lookup.

    python3 tests/test_reagent_names.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts")]

import ls_config as C                                           # noqa: E402
import ls_reagent_controls as R                                 # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<60} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


MARKERS = ["pERK", "PCNA"]

print("--- real names never match ---")
REAL = ["LS105_10a.czi", "LS105_10b.czi", "LS22_1a.czi", "LS45_8a.czi", "LS53_2b-_A568.czi",
        "LS45_8b_A488.czi", "LS85_7b-.czi", "AB12_1a.czi", "AB12_C_3.czi", "LS01_s01a.czi",
        "not_a_slide.czi", "D:/slides/LS45_9b.czi", "LS61_4c.czi"]
chk("real_names_never_match", R.scan(REAL, MARKERS), [])

print("--- strong kinds ---")
STRONG = {
    "LS45_3a_noPrimary.czi": "no_primary", "LS45 no 1ary.czi": "no_primary",
    "LS45_w-o-primary.czi": "no_primary", "LS45_3_-1Ab.czi": "no_primary",
    "LS45_3_senza_primario.czi": "no_primary", "LS45_uten_primaer.czi": "no_primary",
    "LS45_ohne-1AK.czi": "no_primary", "LS45_2ndOnly.czi": "no_primary",
    "LS22_sec-only_A568.czi": "no_primary", "NegCtrl_LS45_3.czi": "no_primary",
    "LS45_controllo_negativo.czi": "no_primary",
    "LS45_isotype.czi": "isotype", "LS45_rbIgG.czi": "isotype",
    "LS45_preadsorbed.czi": "preadsorbed",
    "LS45_unstained.czi": "unstained", "LS45_noAb.czi": "unstained",
}
for name, kind in STRONG.items():
    m = R.scan([name], MARKERS)
    chk(f"strong {name}", [(x.kind, x.tier) for x in m], [(kind, "strong")])

print("--- marker specific, folder, weak ---")
m = R.scan(["LS45_no-pERK.czi"], MARKERS)
chk("marker_specific markers", m[0].markers if m else None, ["pERK"])
chk("marker_specific kind", m[0].kind if m else None, "no_primary")
m = R.scan(["Negative controls/LS45_3a.czi"], MARKERS)
chk("folder_match", [(x.kind, x.tier) for x in m], [("no_primary", "strong")])
for n in ("LS45_NC.czi", "LS45_neg.czi", "LS45_ctrl.czi"):
    chk(f"weak {n}", [x.tier for x in R.scan([n], MARKERS)], ["weak"])
chk("group_label_suppresses_weak",
    R.scan(["LS45_ctrl.czi"], [], group_labels=["ctrl", "ex"]), [])
chk("never_tokens", R.scan(["LS45_AF488.czi", "LS45_IgG.czi", "LS45_C_3.czi", "LS45_3b-.czi"], []), [])
m = R.scan(["LS45_blankslide.czi"], [], extra=[{"regex": "blankslide", "kind": "unstained"}])
chk("extra_patterns", [(x.kind, x.tier) for x in m], [("unstained", "strong")])
m = R.scan(["a\\Negative controls\\LS45_3a.czi", "LS45_isotype_NC.czi"], [])
chk("backslash path + strongest wins", [(x.kind, x.tier) for x in m],
    [("no_primary", "strong"), ("isotype", "strong")])
chk("one match per path", len(R.scan(["LS45_isotype_unstained_NC.czi"], [])), 1)

print("--- naming presets: how a lab marks reagent controls ---")
DASH = ["LS85_7b-.czi", "LS53_2b-_A568.czi", "LS53_5b-_A568.czi"]
chk("preset off: LS re-scan names ending '-' never match", R.scan(DASH, MARKERS), [])
chk("preset off: the never-match list still gives none", R.scan(REAL, MARKERS), [])
chk("trailing_dash preset is first", list(R.NAMING_PRESETS)[:1], ["trailing_dash"])
P = R.NAMING_PRESETS.get("trailing_dash") or {}
chk("trailing_dash label", P.get("label"),
    "A '-' at the end of the name (before .czi, or before a marker suffix such as _A568)")
chk("trailing_dash regex", P.get("regex"), r"-(?=(_[A-Za-z0-9]+)?\.czi$)")
chk("trailing_dash kind", P.get("kind"), "no_primary")
ON = [R.naming_entry(preset="trailing_dash")] if hasattr(R, "naming_entry") else []
for n in ("LS45_3a-.czi", "LS53_2b-_A568.czi", "archive.zip/LS45_3a-.czi"):
    chk(f"preset on: {n}", [(x.kind, x.tier, x.token) for x in R.scan([n], MARKERS, extra=ON)],
        [("no_primary", "strong", "-")])
for n in ("LS45_8b_A488.czi", "LS105_10a.czi", "X-1_3a.czi", "AB12_2b-s0"):
    chk(f"preset on, still none: {n}", R.scan([n], MARKERS, extra=ON), [])

tp = getattr(R, "token_pattern", lambda t: "(?!)")
NEG2 = [{"regex": tp("NEG2"), "kind": "no_primary"}]
chk("token NEG2 matches LS45_NEG2_3.czi",
    [(x.tier, x.token) for x in R.scan(["LS45_NEG2_3.czi"], [], extra=NEG2)],
    [("strong", "NEG2")])
chk("token is case-insensitive", len(R.scan(["LS45_neg2_3.czi"], [], extra=NEG2)), 1)
chk("token NEG2 does not match inside NEG23",
    [x.tier for x in R.scan(["LS45_NEG23.czi"], [], extra=NEG2) if x.tier == "strong"], [])
AB = [{"regex": tp("a+b"), "kind": "no_primary"}]
chk("token escapes metacharacters: a+b matches LS45_a+b_3.czi",
    len(R.scan(["LS45_a+b_3.czi"], [], extra=AB)), 1)
chk("...and not LS45_aab_3.czi", R.scan(["LS45_aab_3.czi"], [], extra=AB), [])

print("--- naming entry round trip ---")
ne = getattr(R, "naming_entry", lambda **k: {})
nof = getattr(R, "naming_of", lambda c: "missing")
wn = getattr(R, "with_naming", lambda x, **k: "missing")
chk("naming_entry preset", ne(preset="trailing_dash"),
    {"regex": r"-(?=(_[A-Za-z0-9]+)?\.czi$)", "kind": "no_primary", "preset": "trailing_dash"})
chk("naming_entry token", ne(token="NEG2"),
    {"regex": tp("NEG2"), "kind": "no_primary", "token": "NEG2"})
chk("naming_entry nothing", ne(), None)
chk("naming_of nothing", nof({}), (None, None))
HAND = [{"regex": "blankslide", "kind": "unstained"}, {"regex": "iso2", "kind": "isotype"}]
cfgn = {"reagent_controls": {"extra_patterns": wn(HAND, preset="trailing_dash")}}
chk("naming_of preset", nof(cfgn), ("trailing_dash", None))
chk("...hand-written patterns kept, in order, before it",
    cfgn["reagent_controls"]["extra_patterns"][:2], HAND)
cfgn["reagent_controls"]["extra_patterns"] = wn(cfgn["reagent_controls"]["extra_patterns"],
                                                token="NEG2")
chk("naming_of token replaces the preset", nof(cfgn), (None, "NEG2"))
chk("...only one naming entry", len(cfgn["reagent_controls"]["extra_patterns"]), 3)
cleared = wn(cfgn["reagent_controls"]["extra_patterns"])
chk("clearing leaves the hand-written ones exactly", cleared, HAND)
chk("with_naming does not change its input", len(cfgn["reagent_controls"]["extra_patterns"]), 3)

print("--- lookup ---")
ROWS = [{"scene_uid": "A_s01_sc01", "file": "A_1.czi", "marker_channel": "pERK"},
        {"scene_uid": "A_s01_sc01", "file": "A_1.czi", "marker_channel": "PCNA"},
        {"scene_uid": "B_s01_sc01", "file": "B_1.czi", "marker_channel": "pERK"}]
cfg = {"reagent_controls": {"files": {"A_1.czi": {"kind": "no_primary", "markers": ["pERK"]}}}}
lk = R.expand(cfg, ROWS)
chk("file entry, one marker", sorted(lk), [("A_s01_sc01", "pERK")])
chk("is_control yes", R.is_control(lk, "A_s01_sc01", "pERK"), True)
chk("is_control no", R.is_control(lk, "A_s01_sc01", "PCNA"), False)
cfg["reagent_controls"]["scenes"] = {"A_s01_sc01": {"kind": "no_primary", "markers": []}}
chk("scene overrides file", sorted(R.expand(cfg, ROWS)),
    [("A_s01_sc01", "PCNA"), ("A_s01_sc01", "pERK")])
chk("entries defaults", R.entries({}), {"declared": "no", "files": {}, "scenes": {},
                                        "paired_with": {}, "rejected": [], "extra_patterns": []})
cfg2 = {"reagent_controls": {"files": {"X.czi": {"kind": "isotype", "subject": "RC1", "slide": 2,
                                                   "replicate": "nc"}}}}
chk("control_subject", R.control_subject(cfg2, "X.czi"),
    {"subject": "RC1", "slide": 2, "replicate": "nc"})
chk("control_subject none", R.control_subject(cfg2, "Y.czi"), None)
ms = R.scan(["LS45_isotype.czi", "LS45_unstained.czi", "LS45_noAb2.czi", "Neg/LS45_NC.czi",
             "LS45_preadsorbed.czi"], [])
cfg3 = {"reagent_controls": {"files": {"LS45_isotype.czi": {"kind": "isotype"}},
                             "rejected": ["LS45_unstained.czi"]}}
chk("unanswered", [x.path for x in R.unanswered(cfg3, ms)], ["LS45_preadsorbed.czi"])

print("--- config ---")
def errs(rc):
    e, _ = C.validate({"reagent_controls": rc})
    return [x for x in e if "reagent_controls" in x]

chk("empty block ok", errs({"declared": "unsure"}), [])
chk("declared bad", bool(errs({"declared": "maybe"})), True)
chk("subject with _ refused", bool(errs({"files": {"x.czi": {"kind": "isotype", "subject": "A_1"}}})), True)
chk("good subject ok", errs({"files": {"x.czi": {"kind": "isotype", "subject": "RC1", "slide": 3}}}), [])
chk("unknown kind refused", bool(errs({"files": {"x.czi": {"kind": "bogus"}}})), True)
chk("unknown scene kind refused", bool(errs({"scenes": {"u": {"kind": "bogus"}}})), True)
chk("bad regex refused", bool(errs({"extra_patterns": [{"regex": "(", "kind": "unstained"}]})), True)
chk("extra kind refused", bool(errs({"extra_patterns": [{"regex": "x", "kind": "zzz"}]})), True)
chk("good extra ok", errs({"extra_patterns": [{"regex": "blank", "kind": "unstained"}]}), [])
chk("rejected must be list of str", bool(errs({"rejected": [1]})), True)
chk("a preset entry ok", errs({"extra_patterns": [ne(preset="trailing_dash") or {}]}), [])
chk("a token entry ok", errs({"extra_patterns": [ne(token="NEG2") or {}]}), [])
chk("unknown preset refused", bool(errs({"extra_patterns": [
    {"regex": "-", "kind": "no_primary", "preset": "bogus"}]})), True)
chk("empty token refused", bool(errs({"extra_patterns": [
    {"regex": "x", "kind": "no_primary", "token": "  "}]})), True)

if failures:
    print(f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("\nall ok")
