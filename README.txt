HVAC Plan & Spec OCR Kit — IT-Compliant GitHub Pages + Windows Portable .exe
============================================================================

RECOMMENDED FOR IT COMPLIANCE (Zero Red Flags):
1. GITHUB PAGES WEB APP (100% Browser-Based, No .exe Required)
   - Push this folder to a GitHub repository (public or private/Enterprise).
   - In GitHub: Settings -> Pages -> Source: "GitHub Actions".
   - The included `.github/workflows/deploy-pages.yml` automatically publishes
     `site/index.html` to a clean HTTPS URL (`https://<user>.github.io/<repo>/`).
   - Why IT teams prefer this:
     * Zero executables downloaded or run on corporate endpoints.
     * Runs 100% locally in Chrome/Edge via WebAssembly — proprietary bid plans
       never leave the user's computer or touch any external server.
     * Includes font-size classification, isolated schedules & title blocks,
       equipment cross-reference links (Floor Plan, Control Diagram, Sequence of
       Operations, All Diagrams), and clickable links embedded inside the
       downloaded searchable PDF.

2. PORTABLE WINDOWS .EXE (Optional — Best for 100+ Page 24"x36" Drawing Sets)
   - Note on IT/EDR compliance: Unsigned PyInstaller `--onefile` .exe files
     self-extract into %TEMP%, which often triggers Defender/CrowdStrike alerts.
     This kit uses `--onedir` (a clean portable folder with `HVAC_Plan_OCR.exe`)
     to minimize false positives. For company-wide rollout, ask IT to code-sign
     the folder or allowlist the GitHub Pages URL instead.
   - Cloud build (no local Python needed): Push to GitHub -> Actions tab ->
     run "Build Windows HVAC OCR Portable App" -> download artifact.
   - Local build on Windows: Run `build_exe.bat`.
