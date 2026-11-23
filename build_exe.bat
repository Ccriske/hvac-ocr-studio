@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo   Building HVAC OCR Studio (Unified Windows Desktop App)
echo ============================================================

python -m pip install --upgrade pip
python -m pip install pyinstaller pypdf pypdfium2 pytesseract pillow reportlab pywebview

set "TESS_DIR=C:\Program Files\Tesseract-OCR"
if not exist "%TESS_DIR%\tesseract.exe" (
    set "TESS_DIR=C:\Program Files (x86)\Tesseract-OCR"
)

if exist "%TESS_DIR%\tesseract.exe" (
    echo Bundling Tesseract OCR from: %TESS_DIR%
    pyinstaller --noconfirm --onedir --windowed ^
        --name "HVAC_OCR_Studio" ^
        --collect-all webview ^
        --collect-all pypdfium2 ^
        --hidden-import webview.platforms.edgechromium ^
        --add-data "site;site" ^
        --add-data "%TESS_DIR%;tesseract" ^
        hvac_ocr_desktop.py
) else (
    echo [WARNING] Tesseract-OCR not found in Program Files.
    pyinstaller --noconfirm --onedir --windowed ^
        --name "HVAC_OCR_Studio" ^
        --collect-all webview ^
        --collect-all pypdfium2 ^
        --hidden-import webview.platforms.edgechromium ^
        --add-data "site;site" ^
        hvac_ocr_desktop.py
)

echo.
echo Build complete! Portable app ready at:
echo   dist\HVAC_OCR_Studio\HVAC_OCR_Studio.exe
pause
