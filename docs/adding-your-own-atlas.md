# Adding your own atlas, with regions drawn in Fiji

The app comes with two atlases: the salmon atlas and the Wullimann 1996 zebrafish atlas.
To use another one, such as another species or your own drawings, you need two things:
a picture of each atlas plate, and the regions (ROIs) you want measured, drawn on each
plate in Fiji (ImageJ). One command then turns that folder into an atlas the app uses
like the built-in ones.

## 1. Put the plates in one folder

One image per plate, as PNG, JPG or TIF, in a new folder. The plates are taken in
**file-name order**, so name them in order from front (rostral) to back (caudal):

```
my_atlas/
    mouse_01.png
    mouse_02.png
    mouse_03.png
```

Use the final images. The regions are stored relative to the picture you drew them on,
so do not resize, crop or re-save a plate after drawing on it.

## 2. Draw the regions in Fiji

For each plate:

1. **File > Open** the plate.
2. Open the ROI Manager (**Analyze > Tools > ROI Manager**).
3. Draw a region with the **polygon** or **freehand** tool. Rectangles and ovals work too.
   Then press **t** (or click **Add**) to add it to the ROI Manager.
4. Select it in the ROI Manager, click **Rename...**, and type the region's name
   (for example `Dm`). **The name is the region**: it becomes the column name in your
   results, so use the same spelling on every plate.
5. Repeat for every region on the plate.
6. In the ROI Manager, click **More > Save...** and save the ROIs **in the same folder,
   under the plate's name** with `.zip`: `mouse_01.png` gets `mouse_01.zip`.
7. Close the ROI Manager (or **Delete** all its ROIs) before the next plate.

The folder then looks like this:

```
my_atlas/
    mouse_01.png   mouse_01.zip
    mouse_02.png   mouse_02.zip
    mouse_03.png   mouse_03.zip
```

Rules the import checks:

- Every plate needs its `.zip`.
- Every ROI must be renamed. A ROI still called something like `0123-0456`
  (ImageJ's automatic name) is refused.
- Regions must be areas. Lines, points and combined shapes (made with AND or OR)
  are refused. Draw each region as one outline instead.
- If your atlas draws only one half of the brain, draw that half. Add `--mirror`
  in the next step, and the app reflects the regions onto the other side.

## 3. Import it

From the app folder:

```bash
python scripts/ls_atlas_imagej.py my_atlas --name mouse
```

`--name` is a short name for the atlas: lower-case letters, digits and `_`. The command
reads every plate and its ROIs and lists anything to fix, naming the file and the ROI.
Once nothing is left to fix, it copies the atlas into the open study and selects it as
the study's atlas. Add `--label "Mouse brain (Smith 2020)"` for a readable name in menus,
and `--mirror` for a one-hemisphere atlas.

To check the folder without importing anything, leave out `--name`. It then only builds
the plate set, in `my_atlas/plate_set`.

After importing, the ROI curator shows your regions as the numbered outlines on each
plate, exactly as it does for the built-in atlases.

## If you already have tables instead of Fiji files

An atlas that already exists as tables (`plates.csv` plus `polygons.csv` or `seeds.csv`)
is imported with `scripts/ls_atlas_import.py` instead. The README section
"Using an atlas of your own" describes that format.

## Cite your atlas

If your atlas comes from a publication, add its citation to your study's methods, as
`atlas/README.md` does for the two built-in atlases.
