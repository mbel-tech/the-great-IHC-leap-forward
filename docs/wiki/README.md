# Wiki source

These files are the repository's GitHub wiki. Edit them here, not on GitHub: the
*Publish wiki* workflow copies this folder to the wiki whenever a change to it reaches
`main`, and anything edited on the wiki directly is overwritten.

- One file per page. The file name is the page name (`Quick-Start.md` is the page
  *Quick Start*); `Home.md` is the front page, `_Sidebar.md` and `_Footer.md` appear on
  every page.
- Link to another page by its name without `.md`: `[Measure](Measure)`.
- Put pictures in `images/` here and reference them as `images/<name>.png`.
- `tests/test_wiki.py` checks every page link and picture resolves.
- Keep the pages in step with `docs/user-guide.md`, which is the complete reference.
