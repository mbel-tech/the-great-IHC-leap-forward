# Wiki source

These files are the repository's GitHub wiki. Edit them here, not on GitHub: the
*Publish wiki* workflow copies this folder to the wiki whenever a change to it reaches
`main`, and anything edited on the wiki directly is overwritten.

- One file per page. The file name is the page name (`Quick-Start.md` is the page
  *Quick Start*); `Home.md` is the front page, `_Sidebar.md` and `_Footer.md` appear on
  every page.
- Link to another page by its name without `.md`: `[Measure](Measure)`.
- Reference pictures as `images/<name>.png`. The workflow copies `docs/guides/img/user-guide/*.png`
  (the user guide's screenshots) into the wiki's `images/` folder; a picture only for the wiki
  can go in `docs/wiki/images/`.
- `tests/test_wiki.py` checks every page link and picture resolves.
- Keep the pages in step with `docs/guides/user-guide.md`, which is the complete reference.
