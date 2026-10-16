# Atlases

Matching each section to an atlas plate says how far along the brain it is, and the plate's outlines say where each region is. You choose the study's atlas on Set up's **Atlas** card.

| Atlas | Species | Plates | Notes |
| --- | --- | --- | --- |
| **salmon** (default) | Atlantic salmon | 64 plates, 31 with regions | Written by Marco A. Vindas, unpublished. Covers the forebrain and the tuberal hypothalamus. |
| **wullimann1996** | Zebrafish | 42 cross sections, 399 reviewed region outlines | Wullimann, Rupp and Reichert (1996). Outlines cover one side of the brain, so keep *Mirror outlines to both sides* on (Set up > Atlas, right under the atlas choice). |
| Your own | Any | Any | Drawn in Fiji, or imported from tables. See [Adding your own atlas](Adding-Your-Own-Atlas). |

**Both built-in atlases ship with the app.** The first time a study needs a plate set it does not have, the app copies it into `<output>/atlas/`. A study that already has its own copy keeps it, and nothing is overwritten.

**Cite the atlas you use.** See [Citing](Citing).

## Atlas settings

| Setting | What it means |
| --- | --- |
| Atlas | salmon, wullimann1996, or one you added |
| Atlas PDF | Salmon only: the PDF the plates were taken from. Only needed to extract them again. |
| Mirror outlines to both sides | For an atlas whose outlines cover one side of the brain |
| Atlas plate set | Which set of plates the ROI curator shows. Leave at the default. |
| Two-section figures | Salmon only: the figures that show two sections each, for example `31-47` |

## Do not edit plates in place

Curation records a fingerprint of each plate picture. Re-saving, compressing or cropping a plate changes it, and every section curated against the old picture then reads as changed. To change an atlas, add it as a new one.

## Limits of the salmon atlas

33 of its 64 plates carry no region identification, so the caudal two-thirds of the brain needs labelling before regional counts extend there. A section matched to a plate without regions has nothing to draw. You can label it yourself with **ADD ROI** in the ROI curator, or make your own atlas.
