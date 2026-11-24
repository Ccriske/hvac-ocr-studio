
# HVAC OCR Studio

A zero-clutter, lossless OCR and PDF bookmarking tool built specifically for HVAC/mechanical engineering plans and Division 23 specification packages.

## Core Capabilities

* **Lossless Vector-Preserving PDF Output:** Keeps original CAD vector linework, hatches, and native fonts 100% crisp at any zoom level by overlaying only an invisible (`Tr 3`) searchable text layer—never re-rasterizing drawings to JPEG.
* **Font-Matched Selectable Text:** Scales each invisible OCR word horizontally and vertically to match the exact baseline, height, and width of the underlying printed or CAD text.
* **90° Vertical Title Block Handling:** Automatically crops right-edge vertical title blocks on landscape sheets (24"×36", 30"×42"), rotates them 90° clockwise for OCR, and maps the invisible searchable text back onto the vertical title block.
* **Native Hierarchical PDF Bookmarks (`/Outlines`):** Automatically embeds a structured bookmark tree that opens natively in Bluebeam Revu, Adobe Acrobat, Chrome, and Edge:
  * **Floor Plans**
  * **Schedules**
  * **Control Diagrams**
  * **Sequence of Operations**
  * **All Diagrams & Schematics**
  * **Specifications (Div 23)**
  * **Equipment Cross-Reference** (`AHU-*`, `RTU-*`, `VAV-*`, `EF-*`, etc. linked directly to each sheet where they appear)
* **Unified Web & Desktop Interface:** Both the browser app (GitHub Pages) and the Windows desktop app (`HVAC_OCR_Studio.exe` via `pywebview`) share the exact same clean two-pane UI with zero settings clutter.

---

## Project Structure

This Google Drive folder mirrors the GitHub repository 1-to-1 (superseded versions are moved to `_archive/`):

```text
HVAC OCR Studio/
├── .github/
│   └── workflows/
│       ├── build-windows-exe.yml   # Builds & publishes Windows Desktop .zip to GitHub Releases
│       └── deploy-pages.yml        # Auto-deploys site/index.html to GitHub Pages
├── site/
│   └── index.html                  # Unified UI (runs in-browser via WebAssembly OR inside pywebview)
├── hvac_pdf_ocr.py                 # Native lossless OCR, rotated title block & PDF bookmark engine
├── hvac_ocr_desktop.py             # Native Windows pywebview shell bridging index.html to Python
├── build_exe.bat                   # Local Windows PyInstaller build script (--onedir)
├── README.md                       # Project documentation & changelog
└── _archive/                       # Superseded versions and historical zip packages
```

---

## Usage

### 1. Browser App (GitHub Pages — Zero Installs)
* **Live URL:** `https://ccriske.github.io/hvac-ocr-studio/`
* Share directly in Microsoft Teams. No GitHub login or local install required.
* Drop any HVAC plan or spec PDF to start parallel WebAssembly OCR immediately, browse extracted sheets/schedules/title blocks, and download the bookmarked searchable PDF, CSV, JSON, or TXT.

### 2. Windows Desktop App (Fast Multi-Core Native Engine)
Best for large 50–200+ page mechanical drawing sets.
* **Download:** `https://github.com/Ccriske/hvac-ocr-studio/releases/latest` → download `HVAC_OCR_Studio_Windows.zip` → right-click **Extract All** → run `HVAC_OCR_Studio.exe`.
* **Local Build (Optional):** Install Python 3.11+ and Tesseract-OCR on Windows, then double-click `build_exe.bat`.

---

## IT & Data Privacy Notes

* **100% Local Processing:** In both the browser app and the desktop app, all PDFs are processed strictly in local memory on the user's machine. No plans, specs, or client files are ever uploaded to GitHub or any external server.
* **Public Repo Safety:** The GitHub repository contains only the generic application code—never project drawings or company data.
* **Packaging:** The Windows desktop release uses PyInstaller `--onedir` (portable folder) rather than `--onefile` to avoid `%TEMP%` self-extraction patterns that commonly trigger corporate EDR/antivirus false positives.

---

## Changelog

* **v4.0 (2026-11-23):** Lossless vector-preserving PDF text overlay (zero raster blur), native hierarchical PDF Bookmarks (`/Outlines`) for Bluebeam/Acrobat, unified `pywebview` desktop UI matching GitHub Pages, `multiprocessing.freeze_support()` Windows `.exe` fix, structured Drive folder mirroring GitHub + `_archive/`.
* **v3.0 (2026-11-23):** Parallel Tesseract worker pool + `4.0.0_fast` LSTM model, native-text auto-skip, clean minimal two-pane UI with zero checkboxes/dropdowns, GitHub Releases automated publishing.
* **v2.0 (2026-11-23):** Added font-size classification, schedule & title-block isolation, 90° clockwise vertical title block OCR, and sheet/diagram cross-reference links.
* **v1.0 (2026-11-19):** Initial Python CLI (`hvac_pdf_ocr.py`) and browser prototype.
