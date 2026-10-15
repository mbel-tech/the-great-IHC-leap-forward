# Setting up a study

Set up is where you describe the study once: where the slides are, how their files are named, how they were imaged, which atlas to use and how nuclei are counted. On a new study, do the tabs left to right. You can come back any time; stages read the new value the next time they run.

Every field has a short hint, an **(i)** with the full reasoning and which stages read it, and a **↺** button that puts it back to its default. Changes are kept when you press **Apply** on that card, and saved automatically before any stage runs.

> **New or existing study?** Use **File > New project** for a new one. To continue one, use **File > Open project** or **Tools > Switch study**.

## Slides

1. Press **Choose folder…** and pick the folder with your `.czi` files, or **Choose files…** to pick some of them.
2. Read the table. It shows what the app read from each file name: animal, slide and replicate.
3. **Fix every red row before you go on.** A red row is a file whose name does not fit the naming pattern, and every stage would silently ignore it. Fix the pattern on the *Slide file names* card, or rename the file.
4. Press **Use these slides**.

![The Slides tab: one file whose name does not fit the pattern is shown in red](images/02-setup-slides.png)

Choosing a folder only records where the slides are; nothing is moved or copied. Picking files from one folder records a list, which costs no space. If the files are spread over several folders, the app offers to link or copy them and shows the size first.

## Study and folders

| Setting | What to put |
| --- | --- |
| Study name | A short name, shown in the study picker |
| Slides folder | The folder with the `.czi` files. Usually set for you by the Slides tab. |
| Analysis output | Where every result is written. Needs plenty of space. |
| Curator exports | Where the ROI curator saves its exports. Leave empty to use `<output>/exports`. |

## Slide file names

The app reads which animal and slide each file is from its name, and builds the section ID (`scene_uid`, for example `LS105_s10a_sc03`) that every later table joins on.

- **Filename pattern.** Press **Build…** rather than typing it. The dialog shows a real file name: select a run of characters and press **Mark as…** to say what it is (animal, slide, replicate). Everything you do not mark must match exactly. Every file found is then checked against the pattern, misses first.
- **Case-insensitive names.** On by default; leave it on.

Animal is required; slide and replicate are optional. An animal ID cannot contain an underscore.

## Microscope and channels

| Setting | What it means | LS study value |
| --- | --- | --- |
| Pixel size (µm) | Camera pixel size at the objective used | 0.65 |
| Section thickness (µm) | Used for the Abercrombie correction | 14 |
| Overview resolution (µm per px) | Size of the small per-section pictures | 5.2 (default) |
| Acquisition layout | **multiplex**: one scan holds every marker. **paired**: one scan per marker, each with its own counterstain. | paired |
| DAPI index, Marker index | Which channel in the CZI file is which | 0 and 1 |

**Channels and markers…** opens a table of channels. **Detect from a CZI file…** reads each channel's dye and colour from a real slide. The file knows the dye, not the antibody, so you type the marker name yourself. A marker's name becomes part of folder and file names, so the app asks before you rename one.

**Find nuclei** (paired studies) has one row per marker. Leave it on *On the counterstain (DAPI)* unless a marker's scan has no usable DAPI. Then choose *On this marker's own channel*, and only for a marker whose signal is in the nucleus. In the LS study this is needed for PCNA, whose DAPI plane is noise at full resolution. For such a marker only the positive density is reported; see [Measure](Measure#pcna-counted-on-its-own-channel).

![Microscope and channels, with Find nuclei set to the marker's own channel for PCNA](images/03-setup-microscope.png)

## Pictures

**Curation pictures** set how sharp the pictures in the ROI curator are. *Curation images from* **czi** (default) draws them from the original 16-bit scan at 2.6 µm per pixel (1.3 is the finest allowed). **overview** gives the older, softer pictures. Measurement always reads the scan's own pixels, whatever you set here. A change only affects pictures made afterwards.

**Scene pictures** are optional pictures of every whole scene, for looking at only. Set the folder, resolution (0.65 to 2.6 µm per pixel) and brightness (*Same for all*, so brightness compares, or *Per scene*). Nothing here changes a measurement.

![The Pictures card](images/04-setup-pictures.png)

## Atlas

Choose **salmon** (default), **wullimann1996** (zebrafish), or one you added. **Add atlas from Fiji…** adds your own. See [Atlases](Atlases).

## Nucleus detection

| Setting | Default | Change it when |
| --- | --- | --- |
| Nucleus diameter (µm) | 7 | Your nuclei are clearly larger or smaller |
| Abercrombie correction | On | Leave on. It corrects for nuclei cut across two sections. |
| Threshold settings | 3 and 5 µm² | Only for markers counted on their own channel; see the (i) |

## You are done when

The Slides tab has no red rows and the step list says every card is applied. Then go to [Prepare](Prepare).
