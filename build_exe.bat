@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo   Building Portable Windows App (IT-Friendly --onedir)
echo ============================================================

python -m pip install --upgrade pip
python -m pip install pyinstaller pypdf pypdfium2 pytesseract pillow

set "TESS_DIR=C:\Program Files\Tesseract-OCR"
if not exist "%TESS_DIR%\tesseract.exe" (
    set "TESS_DIR=C:\Program Files (x86)\Tesseract-OCR"
)

if exist "%TESS_DIR%\tesseract.exe" (
    echo Bundling Tesseract OCR from: %TESS_DIR%
    pyinstaller --noconfirm --onedir --windowed ^
        --name "HVAC_Plan_OCR" ^
        --add-data "%TESS_DIR%;tesseract" ^
        hvac_ocr_desktop.py
) else (
    echo [WARNING] Tesseract-OCR not found in Program Files.
    pyinstaller --noconfirm --onedir --windowed ^
        --name "HVAC_Plan_OCR" ^
        hvac_ocr_desktop.py
)

echo.
echo Build complete! Portable folder ready at:
echo   dist\HVAC_Plan_OCR\HVAC_Plan_OCR.exe
pause
