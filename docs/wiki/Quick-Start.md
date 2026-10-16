# Quick start

Do the six screens top to bottom. Each has a blue **Your part** line saying what to do there.

| # | Screen | What you do there | You are done when |
| --- | --- | --- | --- |
| ⌂ | **Project** | Open or create your study's project. Read the **Next up** card and press **Go**. | You know which screen is next. |
| 1 | **Set up** | Check the few cards it shows: the folders, the file names, the section thickness and markers, and the atlas. Everything the slide files can tell the app is already filled in; the rest is under **Show advanced settings**. Press **Apply** on any card you change. | No red rows on the Slides tab, and every card is applied. |
| 2 | **Prepare** | Press **Run all remaining**. It runs straight through to the ROI curator. | Prepare shows done and Next up sends you to Curate. |
| 3 | **Curate** | In the ROI curator, bring back any section set aside by mistake, match each section to its atlas plate and draw the brain regions. Press **Done: export and go to Measure**. | Every section is curated or excluded, for each marker (the step list counts them, for example *23 of 48 sections curated*). |
| 4 | **Measure** | Press **Run all remaining**. It first checks signal quality and tunes detection; nucleus detection then takes hours and can be stopped and resumed. | Every section is measured. |
| 5 | **Results** | Press **Run…** on *Join the experiment*, check the sampling workbook and press **Unblind and run**. Then press **Run all remaining** for the spreadsheets (the R figures are switched off for now). | `roi_dataset.xlsx` is in your results folder. |

## Trying a new study on a few sections first

Before the full run of a new study, check your settings on a handful of sections:

1. On Prepare, open **Options** on the slow stages and set **Only the first N sections** to a small number, such as 5.
2. Run, then look at the contact sheets and the curator pictures.
3. On Measure, set **Only the first N sections** to 3 and turn on **Save check pictures** for *Detect and measure nuclei*. Look at the pictures: nuclei outlined in green are counted, red ones are outside the region.
4. Set the limits back to 0 (all) and run everything.

## Where to go next

- [Setting up a study](Setting-Up-a-Study) for each Set up card.
- [Curate](Curate) for the ROI curator, the one place you spend real time.
- [Troubleshooting](Troubleshooting) when a stage stops.
