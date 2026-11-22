"""Render docs/guides/command-line-guide.md to docs/guides/command-line-guide.pdf.

    python docs/make_command_line_pdf.py

pandoc turns the Markdown into one standalone HTML page with the styles below,
and a headless Chrome, Chromium or Edge prints it to A4 (set CHROME to its path
if it is not found). Run it again whenever
the guide changes, so the PDF never describes commands the Markdown no longer
does.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "guides", "command-line-guide.md")
OUT = os.path.join(HERE, "guides", "command-line-guide.pdf")

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.45; color: #1f2328; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 6pt; color: #0b3d5c; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #d0d7de;
     color: #0b3d5c; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; page-break-after: avoid; }
p, li { margin: 4pt 0; }
code { font-family: Consolas, "SF Mono", Menlo, monospace; font-size: 9pt;
       background: #f3f4f6; padding: 0 2pt; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 4px; padding: 6pt 8pt;
      white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 9.5pt;
        page-break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #eef2f6; }
hr { border: 0; border-top: 1px solid #d0d7de; margin: 12pt 0; }
dt { font-weight: 600; margin-top: 8pt; }
dd { margin: 2pt 0 6pt 14pt; }
"""


def find_chrome():
    """A Chrome-family browser to print with: CHROME, else the usual names and places."""
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        if shutil.which(name):
            return shutil.which(name)
    for path in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/opt/pw-browsers/chromium"):
        if os.path.exists(path):
            return path
    raise SystemExit("no Chrome, Chromium or Edge found; set CHROME to one")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        css = os.path.join(tmp, "style.css")
        html = os.path.join(tmp, "guide.html")
        with open(css, "w", encoding="utf-8") as fh:
            fh.write(CSS)
        subprocess.run(["pandoc", SRC, "-f", "gfm+definition_lists", "-t", "html5",
                        "--standalone", "--embed-resources", "--no-highlight", "--css", css,
                        "--metadata", "title=Running the steps from the command prompt",
                        "--variable", "title=", "-o", html], check=True)
        chrome = find_chrome()
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                        "--no-pdf-header-footer", "--print-to-pdf=" + OUT,
                        "file://" + html], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("wrote", os.path.relpath(OUT, os.path.dirname(HERE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
