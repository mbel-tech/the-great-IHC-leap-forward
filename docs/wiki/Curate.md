# Curate: the ROI curator

The Curate screen shows one curator page at a time. For most studies the only page you need is the **ROI curator**: for each section you check it should be used, match it to an atlas plate, and draw the brain regions to measure, plus background discs for comparison.

Your work is saved into the project as you go, so closing the app loses nothing, but **only Export hands your decisions to Measure**. **Done: export and go to Measure**, in the bar above the page, exports for you and opens Measure.

The bar above the page also has **Reload** (after Prepare rebuilt its pictures), **Open in browser**, and **Own window** (a window of its own you can make full screen).

![The Curate screen with the ROI curator inside the app](images/06-curate.png)

In every curator, **a dashed amber border is a proposal the program made; a solid red border is a decision a person made.**

## The page

- **Top bar.** *Animal* and *Marker* menus, *favourites only* and *hide excluded*, counts, **Review**, **?** (every keyboard shortcut) and the export buttons.
- **Tool rail (left).** Every tool, grouped, with its key in brackets.
- **Coach line.** Names the tool you are in and says what a click will do.
- **Two panes.** The section on the left, the atlas plate on the right.
- **Section strip (bottom).** Every section, grouped by animal. Purple border: registered. Blue: plate assigned only. Gold edge: favourite. Red and greyed: excluded. Faded: no ROI.

![The ROI curator: top bar, tool rail, coach line, section and plate panes, info cards and the section strip](images/11-roi-curator.png)

**The first time you open it**, a pop-up asks which regions you will draw. The choice applies to the whole study; press **o** to change it. Chosen regions are numbered on the plate in the order you picked them.

## For each section

1. **Find the plate.** Use ← and → until the plate matches the section, then press **a** (*Assign plate*). The plate stays put when you move to the next section with ↓.
2. **Draw each region.** With **Guided** on (**g**), the cursor walks through your regions in order. Click the region's corners on the section and close the outline with **Enter**, a double-click, or a click on the first corner. Press **s** to skip a region that is not on this section.
3. **Place background discs (d).** Put at least one disc on tissue with no real signal. It sets the positivity cut for this section. **A section without a background disc cannot be used for positivity.**
4. **Optional: landmarks.** With Guided off, click a point on the section, then the matching point on the plate. They record how well the plate fits.
5. **Mark the section if needed:** **f** favourite, **No ROI here** (fine, but nothing to measure), or **x** exclude.

**Editing.** Drag a corner to move it, click an edge to add one, press Delete over a corner to remove it. **z** undoes the last corner or region, **u** is the general undo.

**Zoom and pan.** Scroll to zoom, drag with the middle or right button to pan, **0** fits the section. Zoom shows the scan's real pixels and never changes what is saved.

**Rotate** turns the view of a section that is not upright: press it and drag. It changes the view and how your clicks are read, not the saved coordinates. **r** restores the tilt. Mirroring is not possible.

**ADD ROI** draws a region the plate has no outline for.

## Review: bringing sections back

**Review** shows every scanned section and what happened to it, including those set aside automatically. **Reinstate** puts an excluded section back; **Reject mask** discards an artifact mask that covered real tissue. Keys: ↑ ↓ move, **d** DAPI, **k** marker, **v** artifact mask, **c** censored pixels, **f** full screen.

## Export

Export writes three files to your exports folder, which Measure reads: `roi_plates.csv` (plate and status per section), `roi_landmarks.csv` and `roi_regions.csv` (every region and background disc).

**Shotgun** and **By region** build a PowerPoint deck of your favourites, split by treatment group. They are the only curator buttons that read the groups, and are off unless the study lists them.

## Things worth knowing

- Vd, Vv and POA cannot be told apart without a section's exact level, so the curator shows them as one gold group and the export marks them `region_ambiguous`.
- In the salmon atlas, 31 of 64 plates carry regions. A section matched to any other plate has nothing to draw.
- **Atlas plate boxes** (salmon only, when rebuilding the plates): draw one box around each brain section on each atlas figure, Export, then press **Continue Prepare**.

## Curating in a web browser

The curator pages also work in an ordinary browser (run `serve_curators.bat`). Work done there lives in that browser only. Bring it in with **Bring in browser exports** on the Curate screen, or **Tools > Migrate browser curation**.

Next: [Measure](Measure).
