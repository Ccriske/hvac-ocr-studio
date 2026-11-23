#!/usr/bin/env python3
"""
HVAC OCR Studio — Minimal Desktop App (Zero Settings Clutter, Auto-Optimized)
"""

from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pytesseract
import hvac_pdf_ocr as core


def configure_bundled_tesseract() -> str | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend([
            base / "tesseract" / "tesseract.exe",
            Path(sys.executable).parent / "_internal" / "tesseract" / "tesseract.exe",
            base / "tesseract.exe",
        ])
    candidates.extend([
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ])
    for cand in candidates:
        if cand.exists():
            pytesseract.pytesseract.tesseract_cmd = str(cand)
            tessdata_dir = cand.parent / "tessdata"
            if tessdata_dir.exists():
                os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
            return str(cand)
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        return found
    return None


class HvacOcrApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("HVAC OCR Studio")
        self.geometry("620x420")
        self.minsize(540, 360)
        self.tess_path = configure_bundled_tesseract()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._build_ui()
        self.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        pad = {"padx": 16, "pady": 8}

        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=16, pady=(16, 4))
        ttk.Label(hdr, text="HVAC OCR Studio", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(
            hdr,
            text="Select a PDF or folder. Automatically optimizes OCR, rotates vertical title blocks, and exports searchable PDF + CSV + JSON.",
            foreground="#64748b",
            wraplength=560,
        ).pack(anchor="w", pady=(2, 0))

        io = ttk.Frame(self)
        io.pack(fill="x", **pad)
        io.columnconfigure(0, weight=1)
        self.input_var = tk.StringVar()
        ttk.Entry(io, textvariable=self.input_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(io, text="Choose PDF", command=self._pick_pdf).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(io, text="Choose Folder", command=self._pick_folder).grid(row=0, column=2)

        act = ttk.Frame(self)
        act.pack(fill="x", **pad)
        self.btn_run = ttk.Button(act, text="Make Searchable", command=self._start_run)
        self.btn_run.pack(side="left")
        self.progress = ttk.Progressbar(act, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        log_box = ttk.Frame(self)
        log_box.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        self.log_text = tk.Text(log_box, height=12, state="disabled", font=("Consolas", 9), wrap="word", relief="solid", borderwidth=1)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)

    def _pick_pdf(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path:
            self.input_var.set(path)

    def _pick_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.input_var.set(path)

    def _log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(100, self._drain_log_queue)

    def _start_run(self) -> None:
        if not self.tess_path:
            messagebox.showerror("Tesseract Missing", "Tesseract OCR was not found.")
            return
        in_path = Path(self.input_var.get().strip())
        if not in_path.exists():
            messagebox.showwarning("Select Input", "Please select a PDF file or folder.")
            return
        out_dir = in_path.parent if in_path.is_file() else in_path
        self.btn_run.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(target=self._run_worker, args=(in_path, out_dir), daemon=True).start()

    def _run_worker(self, in_path: Path, out_dir: Path) -> None:
        try:
            pdfs = core.collect_pdf_inputs(in_path, recursive=False)
            workers = max(1, (os.cpu_count() or 2) - 1)
            for idx, pdf_path in enumerate(pdfs, start=1):
                self._log(f"[{idx}/{len(pdfs)}] Processing {pdf_path.name}...")
                rep = core.process_pdf(
                    input_pdf=pdf_path,
                    output_dir=out_dir,
                    dpi=200,
                    mode="auto",
                    force_ocr=False,
                    workers=workers,
                    export_text=True,
                    export_json=True,
                    export_csv=True,
                    verbose=False,
                )
                self._log(f"  Done -> {Path(rep.output_pdf).name} ({rep.pages_ocr_processed}/{rep.total_pages} OCR'd)")
            self._log("Complete.")
        except Exception as exc:
            self._log(f"Error: {exc}")
        finally:
            self.after(0, lambda: (self.progress.stop(), self.btn_run.configure(state="normal")))


if __name__ == "__main__":
    HvacOcrApp().mainloop()
