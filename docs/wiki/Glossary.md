# Glossary

| Term | Meaning |
| --- | --- |
| Abercrombie correction | Scales a count down to allow for nuclei cut across two neighbouring sections, using section thickness and measured nucleus size |
| Analysis set | The sections that can be measured, after exclusions and saturation set-asides |
| Artifact mask | Marks bubbles, clumps and fibres inside a section so they are not measured |
| Atlas plate | One labelled page of the brain atlas. Matching a section to a plate says how far along the brain it is. |
| Background disc | A circle you place on tissue with no real signal. It sets that section's positivity cut and measures the false-positive rate. |
| Blinding | No stage reads an animal's treatment group until Results, so no decision can be biased by it |
| Censored (clipped) pixel | A pixel where the camera hit its maximum, so its true brightness is unknown. Nuclei under one count as positive. |
| Channel | One colour recorded by the scanner, such as DAPI or a marker |
| Counterstain (DAPI) | Stains every nucleus. Nuclei are usually found on it; it is not itself measured. |
| Curator | An interactive page where you make decisions. It saves as you go and hands decisions on with Export. |
| CZI | Zeiss's slide file: one slide, several sections, every channel and tile |
| Detection overlay | A picture with every nucleus the detector found drawn over the marker, coloured by whether it counted as positive; one per region (drawn by 05c) and one per whole scene (06i) |
| Exclusion | A decision that a section should not be used. It is a flag, not a deletion. |
| False-positive rate | The share of background-disc nuclei above the positivity cut |
| Favourite | A section you want in the quantified subset or the Shotgun deck |
| Landmark | A matching pair of points on the section and the plate, showing how well they fit |
| Marker | The stain being measured, such as pERK or PCNA |
| Multiplex, paired | Multiplex: one scan holds every marker. Paired: one scan per marker, of different sections. |
| Overview | A small picture of one section made once, so nothing reopens the large slide file to look at it |
| Plate set | A folder of atlas plates with the region outlines on them |
| Positivity cut | The marker brightness above which a nucleus counts as positive, set per section from its background discs |
| Project | One study in one file: settings, curator decisions and a record of each run |
| Reformat | The standard picture of a section: cleaned, upright, centred and resized to 256 × 256 pixels |
| ROI (region) | A brain region you outlined on a section, measured as one area |
| Scene, section | Scene: one area the scanner imaged, in practice one brain section. Section: one thin slice of tissue (14 µm in LS). |
| scene\_uid | The ID every table uses for a section, built from animal, slide and scene, for example `LS105_s10a_sc03` |
| Stage | One step of the pipeline, with a number such as `04g` and a script in `scripts/` |
| StarDist | The published model that finds nuclei, used without tuning |
| Tutorial | A guided walk through a whole study on the lab's demo study, started from Project or Help |
| Tile | One camera frame; the scanner stitches many into a section |
| Unblinding | Joining animals to their treatment groups, the first stage on the Results screen |
| Worklist | The order the ROI curator shows sections in, balanced across animals |
