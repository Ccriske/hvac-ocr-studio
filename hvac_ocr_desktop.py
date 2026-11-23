#!/usr/bin/env python3
"""
HVAC OCR Studio — Unified Native Desktop App (pywebview + Lossless Native OCR)
==============================================================================
Renders the EXACT SAME sleek HTML UI (`site/index.html`) inside a native
Windows WebView2 window while running native multi-core C++ Tesseract +
lossless vector PDF overlay + native PDF bookmarks under the hood.
"""

from __future__ import annotations

import base64
import multiprocessing
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytesseract
import webview

import hvac_pdf_ocr as core


def get_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def configure_tesseract() -> str | None:
    base = get_bundle_dir()
    candidates = [
        base / "tesseract" / "tesseract.exe",
        Path(sys.executable).parent / "_internal" / "tesseract" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for cand in candidates:
        if cand.exists():
            pytesseract.pytesseract.tesseract_cmd = str(cand)
            td = cand.parent / "tessdata"
            if td.exists():
                os.environ["TESSDATA_PREFIX"] = str(td)
            return str(cand)
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        return found
    return None


class DesktopApi:
    """Bridge exposed to window.pywebview.api inside site/index.html."""

    def __init__(self) -> None:
        self.last_output_pdf: Path | None = None
        self.window: webview.Window | None = None

    def _emit_progress(self, pct: int, msg: str) -> None:
        if self.window:
            safe_msg = msg.replace("\\", "\\\\").replace("'", "\\'")
            self.window.evaluate_js(
                f"window.__onNativeProgress && window.__onNativeProgress({int(pct)}, '{safe_msg}');"
            )

    def pick_and_process_pdf(self) -> dict | None:
        if not self.window:
            return None
        chosen = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("PDF Files (*.pdf)",),
        )
        if not chosen:
            return None
        return self.process_local_path(chosen[0])

    def process_pdf_base64(self, filename: str, b64_data: str) -> dict:
        tmp_dir = Path(tempfile.mkdtemp(prefix="hvac_ocr_"))
        in_path = tmp_dir / Path(filename).name
        in_path.write_bytes(base64.b64decode(b64_data))
        return self.process_local_path(str(in_path))

    def process_local_path(self, path_str: str) -> dict:
        in_path = Path(path_str)
        out_path = in_path.with_name(f"{in_path.stem}_searchable.pdf")
        report = core.process_pdf_lossless(
            input_pdf=in_path,
            output_pdf=out_path,
            progress_cb=self._emit_progress,
        )
        self.last_output_pdf = out_path
        pdf_b64 = base64.b64encode(out_path.read_bytes()).decode("ascii")
        report["file_name"] = in_path.name
        report["pdf_base64"] = pdf_b64
        return report

    def save_searchable_pdf(self) -> dict:
        if not self.window or not self.last_output_pdf or not self.last_output_pdf.exists():
            return {"saved": False}
        dest = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=self.last_output_pdf.name,
            file_types=("PDF Files (*.pdf)",),
        )
        if not dest:
            return {"saved": False}
        target = Path(dest if isinstance(dest, str) else dest[0])
        shutil.copy2(self.last_output_pdf, target)
        return {"saved": True, "path": str(target)}


def main() -> None:
    configure_tesseract()
    base = get_bundle_dir()
    html_file = base / "site" / "index.html"
    if not html_file.exists():
        html_file = base / "index.html"

    api = DesktopApi()
    window = webview.create_window(
        title="HVAC OCR Studio",
        url=html_file.as_uri(),
        js_api=api,
        width=1240,
        height=820,
        min_size=(920, 620),
    )
    api.window = window
    webview.start()


if __name__ == "__main__":
    # Critical for PyInstaller on Windows so ProcessPoolExecutor never spawns duplicate windows!
    multiprocessing.freeze_support()
    main()
