# Results: unblinding and the spreadsheets

Results joins the measurements to the treatment groups and writes the spreadsheets. **The first stage unblinds the analysis**, and **Run all remaining never unblinds**: it always waits for you.

## Unblind

Do this once the regions and detection settings are final.

1. Press **Run…** on *Join the experiment* (marked ⚠ unblinds).
2. A box titled **Unblind the analysis?** opens. The **Sampling workbook (.xlsx)** field shows which workbook will be read. Leave it empty to use `large scale exp sampling data.xlsx` in the slides folder, or press **Choose…** to pick another.
3. Press **Unblind and run**.

![The Unblind the analysis? box, with the sampling workbook field](images/09-unblind-dialog.png)

The stage checks the cohort against the workbook's own record of which animals were sliced, and stops loudly if they disagree. If the workbook's header does not match, it names the mismatched columns.

## Build the spreadsheets

Then press **Run all remaining**. The R figures are switched off for now, so it builds the spreadsheets only; the R scripts are still in the `analysis/` folder and can be run by hand with `Rscript`.

| File | What it holds |
| --- | --- |
| `results/roi_dataset.xlsx` | One row per region per animal, plus per-disc, per-section and coverage sheets. **Use this one for statistics**: the animal is the unit of comparison. |
| `results/roi_dataset_by_slide.xlsx` | The same, one level finer, to show spread within an animal. Slides are not independent replicates; do not test on this sheet. |
| `results/roi_dataset.csv`, `results/animal_metadata.csv` | The joined data behind the spreadsheets |

The spreadsheets can be rebuilt any time, including while detection is still running.

![The Results screen: Join the experiment is marked as unblinding, then the spreadsheet stages](images/08-results.png)

## Check detection

*Detection overlays, for checking* (06i), the last group on Results, draws every nucleus the detector found over its marker picture, for every marker: positives filled magenta, negatives outlined cyan, outside the region yellow, artifact orange, off the tissue blue, and a white outline on a positive nucleus with clipped pixels. Detection has already drawn the region pictures (see [Measure](Measure)), so this mostly draws one picture of each whole scene, several at once, and fills any gaps. **Open pictures** opens `detection_overlays/`. It is made on request only, and it does not read the treatment groups.

- The counts drawn are 06a's. A section whose picture would disagree is not drawn; the run ends with *N not drawn* and `detection_overlays/failures.csv` says why.
- Each whole-scene picture is roughly 100 to 500 MB. **Skip the whole-scene pictures** draws only the region pictures; **Only the first N scenes of each marker** makes a trial. Open scene pictures in Fiji.
- For sections measured before the outlines were saved, the first run re-runs StarDist once, which takes about as long as detection did.

## Before you compare groups

- **Check measurement is finished**, or decide consciously to look at a partial result.
- **Look at the coverage sheet** in `roi_dataset.xlsx`. Some animals may have few measurable sections because of saturation; note this before comparing groups.
- **What not to claim.** The LS dataset has no no-primary controls, so absolute statements such as "30% of cells are positive" are not supported. Comparisons between groups at matched brain levels are.

## Citing

If you publish results, see [Citing](Citing). Results made with the salmon atlas must cite Marco A. Vindas.
