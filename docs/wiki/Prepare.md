# Prepare: the automatic part

Prepare reads the slides, checks them, builds the atlas, and turns every section into a clean, upright, masked picture ready for curation. Press **Run all remaining** and let it go. It runs straight through to the ROI curator, with one short stop for the atlas plate boxes if you rebuild the salmon atlas plates.

**Goes in:** the slide files and your Set up settings. **Comes out:** one standard picture per section, the list of sections that can be measured, and the pages the curators show. Everything is written under your Analysis output folder.

![The Prepare screen: Your part, Run all remaining, and the first stages](images/05-prepare.png)

## What happens, in order

| Part | What it does | Stages |
| --- | --- | --- |
| 1. Read and check the slides | Lists every section from the file headers, checks every file is complete and readable, saves a small picture of each section, and pairs the two marker scans of the same section (paired studies) | 00, 00b, 00d, 01, 01d, 02, 00c, 01k, 01g |
| 2. Build the atlas | Makes the atlas plates the curator shows. Only the stages for your atlas run. | 04a to 04a4 (salmon), 04w and 04x (Wullimann) |
| 3. Make the section images | Masks bubbles, clumps and fibres; levels, centres and resizes each section to a 256-pixel square; proposes sections with no usable tissue | 04g, 04a, 04f |
| 4. Final images | Sets aside sections with too much saturated tissue, flags saturated pixels in the rest, and carries everything onto the second marker | 04j, 04a, 04i, 04m |
| 5. Get ready for the ROI curator | Orders the curation queue, draws the colour pictures and thumbnails, and traces every section's fate for Review | 04n, 04o, 04p |

Each stage's **(i)** says what it makes and where. `docs/pipeline-guide.md` explains every stage in depth.

### Optional: pictures of every scene (01l)

Draws every scene straight from the scan, so you can look at the raw tissue before anything is cropped or rotated. Nothing later reads these pictures. It takes a long time, so **Run all remaining skips it**: press its own **Run** when you want them. Settings are on Set up's *Pictures* card.

## Common options

Many stages share these switches under **Options**:

- **Only the first N sections**: a quick trial run. 0 means all.
- **Redo … already done**: normally finished work is skipped. Turn this on after changing a setting that affects the stage, then turn it off again.
- **Include excluded sections**: also makes pictures of excluded sections, so the curator's Review can show them.

What you change is remembered in the project. **Reset to defaults** undoes it.

## Decisions Prepare makes for you

- **Sections with no usable tissue** (largest piece under 2.5 mm², or no resolvable nuclear detail) are set aside automatically. You bring back any wrong one in the ROI curator's Review.
- **Saturation.** A section with at least 1% of its tissue saturated is set aside. Saturated pixels in sections that stay are flagged, not removed, and nuclei under them count as positive. Changing this tolerance changes which sections are analysed, so agree it before measuring.
- **Levelling.** Each section is turned so its long axis is level. There is no separate rotation curator; you can still rotate the view in the ROI curator.

## What good looks like

- The self-test reports no failures.
- The contact sheets show whole, in-focus sections in a sensible brain order for each animal.
- Every scan has a pair (paired studies).
- In the curator, every section is upright, centred and the same size, with debris gone.

## Common problems

| What you see | What to do |
| --- | --- |
| Fewer sections than expected | File names that do not fit the pattern. Go back to the Slides tab. |
| A self-test failure | A slide file itself is faulty. Do not go on until you understand it. |
| Unpaired scans | Usually a missing or misnamed file. |
| A good section missing from the curator | It was set aside automatically. Open **Review** in the ROI curator, select it and press **Reinstate**, then export. |
| Many sections set aside for saturation in one animal | A property of that animal's scans, not a bug. Note it for the analysis. |

Next: [Curate](Curate).
