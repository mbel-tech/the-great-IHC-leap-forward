# Measure: counting nuclei in your regions

Measure puts your regions back onto the original slides, finds every nucleus inside them, and measures the marker in each. It is still blind: nothing here knows which animal is in which group. Press **Run all remaining**.

**Goes in:** the regions you exported from the ROI curator. **Comes out:** `results/roi_nuclei.csv` (one row per nucleus) and the per-region numbers built from it.

![The Measure screen](images/07-measure.png)

| Stage | What it does | Makes |
| --- | --- | --- |
| 05a Verify the inverse transform | Checks that regions map back onto the slides accurately, before anything is measured | A pass or fail in the log |
| 05a Place the regions on the slides | Maps every region from the 256-pixel curation picture back onto the slide's own 0.65 µm pixels | Region geometry for each marker |
| 05c Detect and measure nuclei | Reads each region at full resolution, finds nuclei with StarDist, measures the marker in each, and flags nuclei on clipped pixels | `results/roi_nuclei.csv` |
| 06a Per-region dataset | Turns nuclei into per-region numbers: how many cells, how many positive, per mm² | `results/roi_measurements.csv`, `results/detector_specificity.csv` |

## How detection works, in plain words

- **Nuclei are found on DAPI, not on the marker** (PCNA is the exception, below). The marker is measured inside each nucleus afterwards, and "positive" is decided later still, so the positivity rule can change without re-reading any scans.
- **StarDist** separates touching nuclei. It is used as published, without tuning.
- **It takes hours.** Stop with **Stop after this stage** or close the app, and run again later. Finished sections are not redone.
- **Measurement order.** *balanced* (default) alternates treatment and control, so a part-finished run is already a fair comparison; it reads the group key only to choose the order. *uid* keeps the run fully blind. The finished file is identical either way.
- **Check pictures** save one picture per region with nuclei outlined: green counted, red found but outside the region. Turn them on for a first run on a new study.

## How your regions reach the slide

You draw on a small standardised picture (about 25 µm per pixel), but nuclei (about 7 µm) can only be counted on the original scan. So 05a carries every corner of every region back: from the 256 grid to the overview (by re-running the reformat and undoing it), from the overview onto the slide, then into a reading box at 0.65 µm per pixel. 05c finds nuclei in the box and keeps only those whose centre falls inside the outline you drew. Your outline decides what counts, not the box.

## How "positive" is decided

Each section's own background discs set its cut: the background median plus three times its spread. It is worked out per section, because background brightness varies. The share of background nuclei above the cut is reported as a false-positive rate in `detector_specificity.csv`.

Two densities are reported: all nuclei and positive nuclei, each per mm² of region on the slide. Counts are also corrected for nuclei cut across two sections (the Abercrombie correction). Raw and corrected numbers are both kept.

## PCNA: counted on its own channel

In the LS PCNA scans the DAPI plane is noise at full resolution, so PCNA-stained nuclei are found directly on the green channel. pERK still uses DAPI. For a marker counted this way:

- **You get positive density only.** With no counterstain there is no count of all cells, so *fraction positive* and *total cell density* are left blank, and `denominator_withheld_reason` says why.
- **Positivity is still set per section from the background discs.** Instead of a false-positive rate, it reports false positives per mm² of background.
- **Brightness is scaled once per section**, so a bright proliferative zone cannot hide dim nuclei.

**Do a pilot before the full PCNA run.** This route has been tested on synthetic data only. Run 3 sections with check pictures on, look at periventricular regions and background discs, hand-count about six regions and compare with `n_positive`, and check positive nuclei are roughly 7 to 9 µm. The full checklist is in `docs/nuclei-detection-after-roi-curation.md`, section 4.8.

## Out of date

A finished stage here shows an amber **Out of date**, with the reason, when what it was made from has changed since it ran: your ROI export, an earlier stage's output, or a setting it reads. Results from before the fixes of 14 October 2026 (a half-pixel offset in region placement, and the saturated-nucleus flag now read from the scan's own pixels) show it too. Press **Run all remaining**: it runs them again in order, and detection measures every section again rather than resuming. Nothing is deleted until a stage runs.

## What good looks like

The check pictures show nuclei outlined one by one, with few red ones. The false-positive rate is low and similar across animals.

## Common problems

| What you see | What to do |
| --- | --- |
| Detection is greyed out | This copy has no StarDist. Use the installed app, or `run_app.bat` with `work/appenv`. |
| A section is left out of positivity | It has no background disc. Add one in the ROI curator and export. |
| 05a refuses a section | Its geometry cannot be rebuilt. Check it in the curator's Review. |

Next: [Results](Results).
