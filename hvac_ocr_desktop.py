#!/usr/bin/env python3
"""
HVAC Plan & Spec OCR — Windows Desktop GUI (PyInstaller Portable Ready)
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
        self.title("HVAC Plan & Spec OCR Tool")
        self.geometry("760x560")
        self.minsize(660, 480)
        self.tess_path = configure_bundled_tesseract()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._build_ui()
        self.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(hdr, text="HVAC Plan & Spec OCR Tool", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        status_txt = f"Tesseract: {self.tess_path}" if self.tess_path else "Tesseract not found (bundle via build_exe.bat)"
        ttk.Label(hdr, text=status_txt, foreground="#475569").pack(anchor="w")

        io_frame = ttk.LabelFrame(self, text="Files & Folders", padding=10)
        io_frame.pack(fill="x", **pad)
        io_frame.columnconfigure(1, weight=1)
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()

        ttk.Label(io_frame, text="Input PDF / Folder:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(io_frame, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        btn_box = ttk.Frame(io_frame)
        btn_box.grid(row=0, column=2, sticky="e")
        ttk.Button(btn_box, text="PDF...", width=8, command=self._pick_pdf).pack(side="left", padx=(0, 4))
        ttk.Button(btn_box, text="Folder...", width=8, command=self._pick_folder).pack(side="left")

        ttk.Label(io_frame, text="Output Folder:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(io_frame, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(io_frame, text="Browse...", width=17, command=self._pick_output).grid(row=1, column=2, sticky="e")

        opt_frame = ttk.LabelFrame(self, text="OCR, Zones & Extraction Settings", padding=10)
        opt_frame.pack(fill="x", **pad)

        self.mode_var = tk.StringVar(value="auto")
        self.dpi_var = tk.IntVar(value=300)
        self.force_ocr_var = tk.BooleanVar(value=True)
        self.export_txt_var = tk.BooleanVar(value=True)
        self.export_csv_var = tk.BooleanVar(value=True)
        self.export_json_var = tk.BooleanVar(value=True)

        row1 = ttk.Frame(opt_frame)
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="Mode:").pack(side="left")
        ttk.Combobox(row1, textvariable=self.mode_var, values=("auto", "plan", "spec"), state="readonly", width=10).pack(side="left", padx=(4, 16))
        ttk.Label(row1, text="DPI:").pack(side="left")
        ttk.Combobox(row1, textvariable=self.dpi_var, values=(150, 200, 250, 300), state="readonly", width=8).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(row1, text="Force OCR all pages (AutoCAD SHX)", variable=self.force_ocr_var).pack(side="left")

        row2 = ttk.Frame(opt_frame)
        row2.pack(fill="x")
        ttk.Checkbutton(row2, text="Export TXT", variable=self.export_txt_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(row2, text="Export CSV (Zones & Tags)", variable=self.export_csv_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(row2, text="Export JSON (Diagram & SOO Links)", variable=self.export_json_var).pack(side="left")

        act = ttk.Frame(self)
        act.pack(fill="x", **pad)
        self.btn_run = ttk.Button(act, text="Run OCR & Extract Links", command=self._start_run)
        self.btn_run.pack(side="left")
        self.progress = ttk.Progressbar(act, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        log_frame = ttk.LabelFrame(self, text="Activity Log", padding=8)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=12, state="disabled", font=("Consolas", 9), wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)

    def _pick_pdf(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent))

    def _pick_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(path)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

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
            messagebox.showwarning("Select Input", "Please select a valid PDF file or folder.")
            return
        out_dir = Path(self.output_var.get().strip()) if self.output_var.get().strip() else (in_path.parent if in_path.is_file() else in_path)
        self.btn_run.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(target=self._run_worker, args=(in_path, out_dir), daemon=True).start()

    def _run_worker(self, in_path: Path, out_dir: Path) -> None:
        try:
            pdfs = core.collect_pdf_inputs(in_path, recursive=False)
            for idx, pdf_path in enumerate(pdfs, start=1):
                self._log(f"[{idx}/{len(pdfs)}] Processing {pdf_path.name}...")
                rep = core.process_pdf(
                    input_pdf=pdf_path,
                    output_dir=out_dir,
                    dpi=int(self.dpi_var.get()),
                    mode=self.mode_var.get(),
                    force_ocr=bool(self.force_ocr_var.get()),
                    workers=1,
                    export_text=bool(self.export_txt_var.get()),
                    export_json=bool(self.export_json_var.get()),
                    export_csv=bool(self.export_csv_var.get()),
                    verbose=False,
                )
                self._log(
                    f"  Done -> {Path(rep.output_pdf).name} | "
                    f"Floorplans: {len(rep.links['floorplans'])}, "
                    f"Control Diagrams: {len(rep.links['control_diagrams'])}, "
                    f"SOO: {len(rep.links['sequence_of_operations'])}, "
                    f"Schedules: {len(rep.links['schedules'])}"
                )
            self._log("All files completed!")
        except Exception as exc:
            self._log(f"ERROR: {exc}")
        finally:
            self.after(0, lambda: (self.progress.stop(), self.btn_run.configure(state="normal")))


if __name__ == "__main__":
    HvacOcrApp().mainloop()
