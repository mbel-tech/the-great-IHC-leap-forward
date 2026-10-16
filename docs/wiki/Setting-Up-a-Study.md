# Setting up a study

Set up is where you describe the study once: where the slides are, how their files are named, how they were imaged, which atlas to use and how nuclei are counted. On a new study, do the tabs left to right. You can come back any time; stages read the new value the next time they run.

Only what the slide files cannot tell the app is shown at first: the folders, the file names, the section thickness and markers, and the atlas. The pixel size, the file-name rule and the channels are read from the slides and already filled in. Tick **Show advanced settings** at the top of Set up to see and change them; it also brings in the *Pictures*, *Curation* and *Nucleus detection* tabs. A required setting that is still empty is always shown.

Every field has a short hint, an **(i)** with the full reasoning and which stages read it, and a **↺** button that puts it back to its default. Changes are kept when you press **Apply** on that card, and saved automatically before any stage runs.

> **New or existing study?** Use **File > New project** for a new one. To continue one, use **File > Open project** or **Tools > Switch study**.

## Slides

1. Press **Choose folder…** and pick the folder with your `.czi` files, or **Choose files…** to pick some of them.
2. Read the table. It shows what the app read from each file name: animal, slide and replicate.
3. **Fix every red row before you go on.** A red row is a file whose name does not fit the naming pattern, and every stage would silently ignore it. Fix the pattern on the *Slide file names* card, or rename the file.
4. Press **Use these slides**.

![The Slides tab: one file whose name does not fit the pattern is shown in red](images/02-setup-slides.png)

**Sampling workbook** names the workbook Results will read to unblind; leave it empty to use the one in the slide folder. **Check workbook** tries the join now, without unblinding anything: it matches the workbook against the study's animals and slides, writes nothing, and reports how many it matched to a group, any animal with no row and any blank group. Use it early, so a mismatch is fixed before the measuring is done.

**Match the sheet…** opens the workbook's first rows with a menu above each column: **Animal ID** (the one column that names the animal), **Treatment** (the column, or several, holding what you want to compare; several make one group, such as `exercise · 1`), **Keep** (carried into the results at Unblind) or **Ignore**. As you choose, it compares the ID column with the animals in the slide file names: how many have a row, which slides have none (Unblind stops on these), and IDs written on two rows. IDs match by name, or by number when no name matches, so `22` in the sheet is `LS22`. An animal whose treatment cell is blank gets no group unless you add a rule, such as "when 'brackish Tank' is 303, 304, 'treatment' is exercise". **Save** keeps the choice in the study; Check workbook, Load the groups for Shotgun and Unblind all use it, Unblind only when it runs. A study that has not made a choice and uses the LS sampling sheet is read as before.

**Load the groups for Shotgun** reads the same workbook the same way and keeps each animal's treatment group in the study (`groups.order` and `groups.by_animal`), so the ROI curator's Shotgun deck can lay favourites out by treatment while you are still curating; press **Build page** on the ROI curator afterwards to turn Shotgun on. The deck shows the groups by design. Nothing else does: the curator's sections, plates and ROIs, every count and threshold, and Set up itself (which shows only how many animals each group has) stay keyed by animal, and 05c uses the groups only for the order it measures sections in. Results are still joined to the groups at Unblind. **Remove the groups** makes the study blind again, and a copy shared as blinded never carries them.

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

- **Filename pattern.** Usually filled in for you from the slides. To set it yourself press **Set up from an example…** and type or pick one of your file names. The app shows its reading, for example *animal LS53 · slide 2 · replicate b*; to change it, press **Animal ID**, **Slide number** or **Replicate letter** and click that piece of the name. It then checks the pattern against every file, tries the usual causes for any that do not fit (upper/lower case, a different ending, a missing replicate letter, other separators) and lists what it changed. On the card the rule is shown as the reading of the example; tick **Show advanced settings** to see the pattern itself.
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

Without **Show advanced settings** this card shows only the section thickness, **Channels and markers…** and, in a paired study, **Find nuclei**; the rest are read from the slides.

**Channels and markers…** opens a table of channels. **Detect from a CZI file…** reads each channel's dye and colour from a real slide. The file knows the dye, not the antibody, so you type the marker name yourself. A marker's name becomes part of folder and file names, so the app asks before you rename one.

**Find nuclei** (paired studies) has one row per marker. Leave it on *On the counterstain (DAPI)* unless a marker's scan has no usable DAPI. Then choose *On this marker's own channel*, and only for a marker whose signal is in the nucleus. In the LS study this is needed for PCNA, whose DAPI plane is noise at full resolution. For such a marker only the positive density is reported; see [Measure](Measure#pcna-counted-on-its-own-channel).

![Microscope and channels, with Find nuclei set to the marker's own channel for AF488 (PCNA)](images/03-setup-microscope.png)

## Pictures

An advanced tab: tick **Show advanced settings** to see it.

**Curation pictures** set how sharp the pictures in the ROI curator are. *Curation images from* **czi** (default) draws them from the original 16-bit scan at 2.6 µm per pixel (1.3 is the finest allowed). **overview** gives the older, softer pictures. Measurement always reads the scan's own pixels, whatever you set here. A change only affects pictures made afterwards.

**Scene pictures** are optional pictures of every whole scene, for looking at only. Set the folder, resolution (0.65 to 2.6 µm per pixel) and brightness (*Same for all*, so brightness compares, or *Per scene*). Nothing here changes a measurement.

![The Pictures card, with its Curation pictures and Scene pictures parts](images/04-setup-pictures.png)

## Atlas

Choose **salmon** (default), **wullimann1996** (zebrafish), or one you added. **Add atlas from Fiji…** adds your own. See [Atlases](Atlases).

## Curation

An advanced tab. *Target sections per animal and level* sets the target the Curate screen's coverage table counts towards: one section per animal and atlas plate unless you change it. It is a goal to curate towards; nothing is held back below it. See [Curate](Curate).

## Nucleus detection

An advanced tab.

| Setting | Default | Change it when |
| --- | --- | --- |
| Nucleus diameter (µm) | 7 | Your nuclei are clearly larger or smaller |
| Abercrombie correction | On | Leave on. It corrects for nuclei cut across two sections. |
| Threshold settings | 3 and 5 µm² | Only for markers counted on their own channel; see the (i) |

## You are done when

The Slides tab has no red rows and the step list says every card is applied. Then go to [Prepare](Prepare).
