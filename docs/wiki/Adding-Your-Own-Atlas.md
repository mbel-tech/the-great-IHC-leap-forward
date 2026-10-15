# Adding your own atlas

To use another atlas, such as another species or your own drawings, you need a picture of each atlas plate and the regions you want measured, drawn on each plate in Fiji (ImageJ). The app then uses it like the built-in ones.

## 1. Put the plates in one folder

One image per plate, as PNG, JPG or TIF. Plates are taken in **file-name order**, so name them from front (rostral) to back (caudal):

```
my_atlas/
    mouse_01.png
    mouse_02.png
    mouse_03.png
```

Use the final images. Regions are stored relative to the picture you drew them on, so do not resize, crop or re-save a plate after drawing on it.

## 2. Draw the regions in Fiji

For each plate:

1. **File > Open** the plate.
2. Open the ROI Manager (**Analyze > Tools > ROI Manager**).
3. Draw a region with the **polygon** or **freehand** tool (rectangles and ovals work too), then press **t** to add it.
4. Select it in the ROI Manager, click **Rename...**, and type the region's name, for example `Dm`. **The name is the region**: it becomes the column name in your results, so spell it the same on every plate.
5. Repeat for every region on the plate.
6. Click **More > Save...** and save the ROIs in the same folder under the plate's name with `.zip`: `mouse_01.png` gets `mouse_01.zip`.
7. Delete all ROIs in the ROI Manager before the next plate.

```
my_atlas/
    mouse_01.png   mouse_01.zip
    mouse_02.png   mouse_02.zip
    mouse_03.png   mouse_03.zip
```

The import checks that:

- every plate has its `.zip`;
- every ROI was renamed (an automatic name such as `0123-0456` is refused);
- every region is an area. Lines, points and combined shapes are refused; draw each region as one outline.

If your atlas draws only one half of the brain, draw that half and turn on mirroring when you import.

## 3. Add it to your study

**In the app:** on **Set up > Atlas**, press **Add atlas from Fiji…**, choose the folder and give the atlas a short name. The atlas is checked, added to the study and selected. Anything to fix is listed with the file and ROI it is in.

**From a terminal**, in the app folder:

```bash
python scripts/ls_atlas_imagej.py my_atlas --name mouse
```

`--name` is lower-case letters, digits and `_`. Add `--label "Mouse brain (Smith 2020)"` for a readable name in menus, and `--mirror` for a one-hemisphere atlas. Leave out `--name` to check the folder without importing.

After importing, the ROI curator shows your regions as numbered outlines on each plate.

## If you already have tables

An atlas that exists as tables (`plates.csv` plus `polygons.csv` or `seeds.csv`) is imported with **Import atlas...** on **Set up > Atlas**, or `scripts/ls_atlas_import.py`. The table format is described in the repository README, under *Using an atlas of your own*.

## Cite your atlas

If your atlas comes from a publication, cite it in your methods. See [Citing](Citing).
