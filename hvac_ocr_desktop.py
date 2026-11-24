#!/usr/bin/env python3
"""
HVAC OCR Studio — Unified Native Desktop App (Async Live-Progress Server)
=========================================================================
Starts a local HTTP server on 127.0.0.1 serving `site/index.html` inside a
borderless Edge/Chrome app window (`--app=...`), with async background OCR
jobs, live page-by-page progress polling, and real Cancel support.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
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


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start_job(self, filename: str, raw_pdf: bytes) -> str:
        job_id = uuid.uuid4().hex[:12]
        cancel_event = threading.Event()
        with self._lock:
            self._jobs[job_id] = {
                "status": "running",
                "pct": 2,
                "message": "Starting native OCR...",
                "started_at": time.time(),
                "error": None,
                "result": None,
                "cancel_event": cancel_event,
            }

        def _worker() -> None:
            try:
                safe_name = Path(filename).name or "document.pdf"
                with tempfile.TemporaryDirectory(prefix="hvac_ocr_") as td:
                    in_path = Path(td) / safe_name
                    out_path = Path(td) / f"{in_path.stem}_searchable.pdf"
                    in_path.write_bytes(raw_pdf)

                    def _on_prog(pct: int, msg: str) -> None:
                        with self._lock:
                            if job_id in self._jobs:
                                self._jobs[job_id]["pct"] = pct
                                self._jobs[job_id]["message"] = msg

                    report = core.process_pdf_lossless(
                        input_pdf=in_path,
                        output_pdf=out_path,
                        progress_cb=_on_prog,
                        cancel_event=cancel_event,
                    )
                    report["file_name"] = safe_name
                    report["pdf_base64"] = base64.b64encode(out_path.read_bytes()).decode("ascii")

                with self._lock:
                    self._jobs[job_id]["status"] = "done"
                    self._jobs[job_id]["pct"] = 100
                    self._jobs[job_id]["message"] = "Complete"
                    self._jobs[job_id]["result"] = report
            except Exception as exc:
                with self._lock:
                    self._jobs[job_id]["status"] = "error"
                    self._jobs[job_id]["error"] = str(exc)

        threading.Thread(target=_worker, daemon=True).start()
        return job_id

    def get_status(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {
                "status": job["status"],
                "pct": job["pct"],
                "message": job["message"],
                "elapsed_sec": round(time.time() - job["started_at"], 1),
                "error": job["error"],
            }

    def pop_result(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if not job:
                return None
            return job.get("result")

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job["cancel_event"].set()
            return True


def make_handler(html_path: Path, jobs: JobStore):
    class HvacRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

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
            if self.path.startswith("/api/status/"):
                job_id = self.path.split("/api/status/", 1)[1]
                st = jobs.get_status(job_id)
                if not st:
                    self._send_json({"error": "Job not found"}, status=404)
                else:
                    self._send_json(st)
                return
            if self.path.startswith("/api/result/"):
                job_id = self.path.split("/api/result/", 1)[1]
                res = jobs.pop_result(job_id)
                if not res:
                    self._send_json({"error": "Result not ready"}, status=404)
                else:
                    self._send_json(res)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path == "/api/start":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw_pdf = self.rfile.read(length)
                    raw_name = unquote(self.headers.get("X-Filename", "document.pdf"))
                    job_id = jobs.start_job(raw_name, raw_pdf)
                    self._send_json({"job_id": job_id}, status=202)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
                return
            if self.path.startswith("/api/cancel/"):
                job_id = self.path.split("/api/cancel/", 1)[1]
                jobs.cancel(job_id)
                self._send_json({"cancelled": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    return HvacRequestHandler


def main() -> None:
    configure_tesseract()
    base = get_bundle_dir()
    html_file = base / "site" / "index.html"
    if not html_file.exists():
        html_file = base / "index.html"

    jobs = JobStore()
    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(html_file, jobs))
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
    main()
