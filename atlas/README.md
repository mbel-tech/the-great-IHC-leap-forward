# Atlas plates shipped with the app

The app needs atlas plates to curate regions and register sections. They ship here so a
new study has them without anyone extracting them first: the first time a study asks
for a plate set it does not have, the set is copied from this folder into
`<out_root>/atlas/<set>/`, byte for byte (`scripts/ls_atlas.py`, `seed_from_bundle`).
A study that already has its own copy of a set keeps it; nothing here overwrites it.

| Folder / file | What it is |
|---|---|
| `Salmon Atlas full.pdf` | The salmon atlas. Used only to extract the plates again (`04a`). An empty `atlas_pdf` setting points here. |
| `plates/` | Salmon plates as extracted by `04a` (47 figures, with region seeds). |
| `plates_final/` | Salmon plates as curated (64 plates). The set the ROI curator and registration use by default on the analysed study. |
| `plates_wullimann/` | Wullimann 1996 zebrafish cross sections (42), arranged by `04w`, with the region outlines `04x` found (`polygons.csv`, 750 outlines). The outlines are unreviewed: names are OCR guesses, and `04l` shows only reviewed ones, so review them with `04x --review` first. |
| `wullimann1996/` | The 42 cross sections as cut from the book's PDF by `04v` (embedded images at their own resolution), their legends (`legends.csv`, OCR) and the Fig. 5 diagram of the section levels. The input to `04w`. |

## Citing the atlases

Both atlases are included with their authors' permission. If you use either one in
research, cite it.

### Salmon atlas

The salmon brain atlas was written by **Marco A. Vindas**. It is unpublished. Any
research that uses it, including results produced with this app on the salmon atlas,
must cite Marco A. Vindas as its author. Until it is published, cite it as:

> Vindas, M. A. Atlantic salmon brain atlas. Unpublished.

### Wullimann zebrafish atlas

> Wullimann, M. F., Rupp, B., & Reichert, H. (1996). *Neuroanatomy of the Zebrafish
> Brain: A Topological Atlas*. Basel: Birkhäuser Verlag. ISBN 3-7643-5120-9.
> https://doi.org/10.1007/978-3-0348-8979-7

## Do not edit in place

Curation records each plate's fingerprint (a digest of the image bytes). Re-saving,
compressing or re-cropping a plate here changes that fingerprint, and every section
curated against the old picture will then read as changed. Replace a plate only
together with a new plate set name.
