#!/usr/bin/env python3
"""
HVAC OCR Studio — Unified Native Desktop App (Zero .NET / Zero pythonnet)
=========================================================================
Starts a local-only HTTP server on 127.0.0.1 serving `site/index.html` and
opens it in a borderless native Edge/Chrome app window (`--app=...`).
All OCR and lossless PDF bookmarking run on the native multi-core C++
Tesseract + pypdf engine via `/api/process`.
"""

from __future__ import annotations

import base64
import json
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytesseract
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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_chromium_browser() -> str | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def make_handler(html_path: Path):
    class HvacRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass  # Quiet console logs

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                body = html_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/health":
                self._send_json({"ok": True, "engine": "native"})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path == "/api/process":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw_pdf = self.rfile.read(length)
                    raw_name = unquote(self.headers.get("X-Filename", "document.pdf"))
                    safe_name = Path(raw_name).name or "document.pdf"

                    with tempfile.TemporaryDirectory(prefix="hvac_ocr_") as td:
                        in_path = Path(td) / safe_name
                        out_path = Path(td) / f"{in_path.stem}_searchable.pdf"
                        in_path.write_bytes(raw_pdf)

                        report = core.process_pdf_lossless(
                            input_pdf=in_path,
                            output_pdf=out_path,
                        )
                        report["file_name"] = safe_name
                        report["pdf_base64"] = base64.b64encode(out_path.read_bytes()).decode("ascii")
                    self._send_json(report, status=200)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    return HvacRequestHandler


def main() -> None:
    configure_tesseract()
    base = get_bundle_dir()
    html_file = base / "site" / "index.html"
    if not html_file.exists():
        html_file = base / "index.html"

    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(html_file))
    url = f"http://127.0.0.1:{port}/"

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    browser = find_chromium_browser()
    if browser:
        profile_dir = tempfile.mkdtemp(prefix="hvac_ocr_win_")
        try:
            proc = subprocess.Popen([
                browser,
                f"--app={url}",
                "--window-size=1280,840",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ])
            proc.wait()
        finally:
            server.shutdown()
            shutil.rmtree(profile_dir, ignore_errors=True)
    else:
        webbrowser.open(url)
        try:
            server_thread.join()
        except KeyboardInterrupt:
            server.shutdown()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
