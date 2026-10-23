# Troubleshooting

## Re-running safely

- **Stages skip finished work.** Pressing Run again only fills the gaps.
- **Out of date.** From *Place the ROIs on the slide* on, a finished stage made from something that has changed since (your export, an earlier stage, a setting it reads) shows **Out of date** and why. **Run all remaining** runs it and what follows from it again; nothing is deleted first. See [Measure](Measure#out-of-date).
- **After changing a setting** read by an earlier stage, re-run the stages that read it with **Redo … already done** switched on in Options. The setting's (i) says which stages read it. Then switch Redo off again.
- **Changing a curator decision after Measure has run:** export again; Measure's stages show Out of date, then press Run all remaining there.
- **Nothing is deleted.** Excluded sections can be reinstated in the ROI curator's Review. Repair tools back up a results file before changing it.
- **Stop cleanly** with **Stop after this stage** on the status bar rather than closing the app mid-stage.

## Common problems

| What you see | Likely cause | What to do |
| --- | --- | --- |
| Red rows on the Slides tab | File names do not fit the naming pattern | Fix the pattern with **Set up from an example…** on *Slide file names*, or rename the files |
| Fewer sections than expected | Unmatched file names, or an incomplete copy | Check the Slides tab and the 00b result |
| Run all remaining stops at a stage | That stage failed, or it waits for a curator | Read the screen's note and **Show log**. A waiting stage names the curator to export. |
| "Finish Set up" on the Next up card | A required setting is empty | The card names each Set up card and the settings it lacks |
| A curator's decisions are ignored | It was not exported | Open the curator and press Export, or **Done: export and go to Measure** |
| A good section is missing from the curator | It was set aside automatically | ROI curator > **Review**, select it, **Reinstate**, export |
| Curation made in a browser is missing | Browser work is stored in that browser only | **Tools > Migrate browser curation**, or **Bring in browser exports** |
| Nucleus detection is greyed out | No StarDist in this Python | Use the installer, or `run_app.bat` (uses `work/appenv`) |
| A section is left out of positivity | It has no background disc | Add one in the ROI curator and export |
| *Place the ROIs* and *Detect and measure nuclei* say Out of date | They ran before the region fixes of 14 October 2026, or the export changed since | Press Run all remaining on Measure, then rebuild Results. See [Measure](Measure#out-of-date). |
| The overview stage fails on Python 3.14 | The CZI reader has no 3.14 version | Use Python 3.13 (`run_app.bat`) |
| Scene pictures: "N scenes not drawn" | Not enough free memory at a fine resolution | Use 1.3 or 2.6 µm per pixel on Set up's Pictures card (under Show advanced settings) |
| A curator pane is blank | The install is incomplete | Run the self-test (see [Install and first run](Install-and-First-Run)); reinstall if it fails |
| The app will not start | A missing library or a broken environment | Read `lsapp-crash.log`. To keep curating meanwhile, run `serve_curators.bat` and use a browser. |
| Unblinding says the workbook header does not match | The sampling workbook's columns differ | Fix the column names to the ones it lists |
| A stage's row is not ticked though its files are there, and says *its last run failed* | Its last run failed | Read **Show log**, fix the cause, and run it again |
| *no … sections to check: the exclusion step left none*, or *no … sections to composite* | Every section of that marker was set aside or excluded | Look at `reformatted/exclusion_candidates.csv` and the overviews; reinstate sections with tissue in the ROI curator's Review |
| A stage says *Not used in this study* | The study has one marker, so the second marker's stages are left out | Nothing to do |
| Adding a Fiji atlas is refused | An ROI is unnamed, not an area, or a plate lacks its `.zip` | The message names the file and ROI. See [Adding your own atlas](Adding-Your-Own-Atlas). |

## Repairing older results files

Three tools under *Repairs for older results* on the **Measure** screen fix results files written by older versions. They are not part of a normal run. Each backs the file up first, and **Report only** says what would change without writing.

| Tool | Use it when |
| --- | --- |
| 06f Recompute the censored flag | A results file was written before detection flagged clipped nuclei itself |
| 06g Flag nuclei off the section | A results file was written before detection flagged nuclei on bare glass |
| 06h Backfill segmentation provenance | A results file lacks the columns saying which channel and method found each object |

## Still stuck

- Press **(i)** on the stage or setting.
- **Show log** on the status bar has the full log of the last run.
- `LOGS.md` in the repository records past problems and how they were fixed.
- Open an issue on the repository with the log attached.
