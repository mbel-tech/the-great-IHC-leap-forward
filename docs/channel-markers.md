# Common IHC / immunofluorescence markers by color channel

Channel assignment is a panel-design decision (fluorophore conjugate choice), not an
intrinsic property of a marker. The conventions below are what is most often encountered
in 3-channel immunofluorescence, and are the reasonable priors when channel metadata is
missing.

## Blue channel (~405 nm ex / 420-480 nm em)

Dyes: DAPI, Hoechst 33342/33258, Alexa Fluor 405, eFluor 450, BV480, DyLight 405.

Almost always reserved for a **nuclear counterstain** rather than an antibody target:

| Marker | Type | Notes |
|---|---|---|
| DAPI | DNA minor-groove dye | Default nuclear counterstain; segmentation reference |
| Hoechst 33342 | DNA dye | Cell-permeant, live-cell compatible |
| Hematoxylin | Chromogenic counterstain | The blue in brightfield DAB IHC |
| Vector Blue / BCIP-NBT | AP chromogen | Blue precipitate, brightfield multiplex |
| Lamin B1, Histone H3, pHistone H3 | Antibody | Occasionally placed here when nuclei are stained elsewhere |
| Pan-cytokeratin, Collagen IV | Antibody | Abundant structural targets, when blue is free |

Caveat: blue dyes are dim-detected on many CMOS cameras and bleed into green; avoid
low-abundance antibody targets here.

## Green channel (~488 nm ex / 500-550 nm em)

Dyes: FITC, Alexa Fluor 488, GFP/EGFP, CF488, DyLight 488, ATTO 488.

Typically the brightest/best-corrected channel on most microscopes, so it often carries the
primary marker of interest. Common assignments by field:

| Marker | Typical context |
|---|---|
| Ki-67 (MKI67) | Proliferation index, tumor pathology |
| Pan-cytokeratin (AE1/AE3), E-cadherin | Epithelial / tumor compartment |
| CD3, CD8, CD20 | T / B lymphocyte infiltrate |
| GFAP, NeuN, MAP2, β-III-tubulin | Neuroscience (astrocytes, neurons) |
| Iba1 | Microglia |
| α-SMA (ACTA2) | Smooth muscle, myofibroblasts |
| Vimentin | Mesenchymal / stromal |
| CD31 (PECAM1) | Endothelium, vessel density |
| Phalloidin-488 | F-actin cytoskeleton |
| BrdU / EdU | S-phase labelling |
| GFP, YFP, Venus | Genetic reporters / lineage tracing |
| HRP-green, Vina Green | Chromogenic green, brightfield multiplex |

Caveat: tissue autofluorescence (lipofuscin, elastin, aldehyde-fixation products, red blood
cells) is strongest in the green band. For a weak target, red or far-red usually gives better
signal-to-background than green despite the brighter dye.

## Red channel (~555-594 nm ex / 570-630 nm em)

Dyes: Cy3, TRITC, Alexa Fluor 555 / 568 / 594, Texas Red, rhodamine, CF594, mCherry.

| Marker | Typical context |
|---|---|
| CD68, CD163, F4/80 | Macrophages |
| CD45 | Pan-leukocyte |
| CD8 / CD4 | T-cell subsets (whichever is not in green) |
| Pan-cytokeratin | Tumor mask when immune markers occupy green |
| GFAP / Iba1 | Glia (counterpart to the green neuronal marker) |
| Synaptophysin, PGP9.5 (UCHL1), chromogranin A | Neuroendocrine / nerve fibers |
| Desmin, myosin heavy chain | Muscle |
| S100, SOX10, Melan-A | Melanocytic / neural crest |
| Collagen I / IV, fibronectin | Extracellular matrix |
| TOM20, MTCO1, HSP60 | Mitochondria |
| LAMP1, LC3B | Lysosome / autophagy |
| Phalloidin-TRITC, propidium iodide | F-actin; dead-cell nuclei |
| tdTomato, mCherry, DsRed | Genetic reporters |
| AEC, Fast Red, Permanent Red | Chromogenic red, brightfield |

## Frequently used 3-color combinations

| Application | Blue | Green | Red |
|---|---|---|---|
| Tumor immune infiltrate | DAPI | CD8 | Pan-cytokeratin |
| Proliferation in epithelium | DAPI | Ki-67 | Pan-cytokeratin |
| Neuro — glia vs neurons | DAPI | NeuN or MAP2 | GFAP or Iba1 |
| Vasculature | DAPI | CD31 | α-SMA |
| Fibrosis / stroma | DAPI | α-SMA | Collagen I |
| Cultured cells, cytoskeleton | DAPI | Phalloidin | TOM20 or tubulin |
| Lineage tracing | DAPI/Hoechst | GFP | tdTomato |

## Notes on brightfield (chromogenic) IHC

Color there comes from the enzyme substrate, not a fluorophore:

- Brown — DAB (HRP)
- Red — AEC, Fast Red, Permanent Red (AP or HRP)
- Blue — Vector Blue, BCIP/NBT (AP); hematoxylin for counterstain
- Green — Vina Green, HRP-green
- Black — DAB-nickel, silver-enhanced

Any antibody can be paired with any chromogen, so marker-to-color mapping here is purely
a protocol choice.

## Design rules that drive the assignment

1. Put the **lowest-abundance target** on the brightest, best-separated dye (AF488 or AF647).
2. Keep nuclei in blue for segmentation; it frees the high-SNR channels for antigens.
3. Avoid green for weak targets in autofluorescent tissue (brain, liver, kidney, FFPE).
4. For >3 plex, separate spectrally adjacent dyes across non-adjacent targets and acquire
   sequentially or unmix.
