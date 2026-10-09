#!/usr/bin/env python3
"""Markdown -> PDF impaginato, via Chrome headless (non richiede pandoc).

Uso: python scripts/md2pdf.py guida.md guida.pdf
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRATCH = Path(__file__).resolve().parent
if (SCRATCH / "pylibs").is_dir():  # copia locale opzionale di `markdown`
    sys.path.insert(0, str(SCRATCH / "pylibs"))
import markdown  # noqa: E402


def find_chrome() -> str:
    """Chrome/Chromium/Edge: variabile CHROME_PATH, poi i percorsi comuni, poi il PATH."""
    candidates = [
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Edge is Chromium-based and ships with every Windows 10/11.
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    sys.exit("Chrome, Chromium o Edge non trovato: installane uno oppure imposta CHROME_PATH.")

CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 15pt; margin-top: 22px; border-bottom: 1px solid #ccc;
     padding-bottom: 4px; break-after: avoid; }
h3 { font-size: 12pt; margin-top: 16px; break-after: avoid; }
h4 { font-size: 11pt; margin-top: 14px; break-after: avoid; }
h1, h2, h3, h4 { break-inside: avoid; }
code { font-family: "SF Mono", Menlo, monospace; font-size: 9pt;
       background: #f2f2f2; padding: 1px 4px; border-radius: 3px;
       white-space: normal; overflow-wrap: anywhere; }
pre { background: #f6f6f6; border: 1px solid #e0e0e0; border-radius: 4px;
      padding: 10px; break-inside: avoid; overflow-wrap: anywhere;
      white-space: pre-wrap; }
pre code { background: none; padding: 0; white-space: pre-wrap; }
blockquote { border-left: 3px solid #999; margin: 12px 0; padding: 4px 14px;
             color: #444; background: #fafafa; break-inside: avoid; }
blockquote code { white-space: normal; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 9.5pt; }
thead { display: table-header-group; }
th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #f0f0f0; }
td:first-child { white-space: nowrap; width: 1%; }
td:first-child code { white-space: nowrap; overflow-wrap: normal; }
tr { break-inside: avoid; }
li { margin: 3px 0; }
hr { border: none; border-top: 1px solid #ddd; margin: 20px 0; }
a { color: #1a4f8a; text-decoration: none; }
img { max-width: 100%; max-height: 95mm; display: block; margin: 8px auto;
      border: 1px solid #ddd; border-radius: 4px; break-inside: avoid; }
"""


def convert(md_path: Path, pdf_path: Path) -> None:
    html_body = markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists", "nl2br", "toc"],
    )
    # The HTML lives in a temp dir: <base> keeps relative image paths resolving
    # against the Markdown file's folder.
    base = md_path.resolve().parent.as_uri() + "/"
    html = (f"<!doctype html><html lang='it'><head><meta charset='utf-8'>"
            f"<base href='{base}'>"
            f"<title>{pdf_path.stem}</title><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>")
    # L'HTML e' solo un passaggio intermedio: non lo lasciamo nel progetto.
    tmp = Path(tempfile.mkdtemp(prefix="md2pdf-"))
    html_path = tmp / (pdf_path.stem + ".html")
    html_path.write_text(html, encoding="utf-8")

    # Chrome pretende un percorso assoluto: senza resolve() as_uri() solleva ValueError.
    subprocess.run(
        [find_chrome(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()],
        check=True, capture_output=True,
    )


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"OK: {sys.argv[2]}")
