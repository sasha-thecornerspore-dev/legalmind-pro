@echo off
title LegalMind Pro — Installer
color 0B

echo.
echo  ========================================
echo   LegalMind Pro — Installer
echo   Legal Document Intelligence (RAG)
echo  ========================================
echo.

:: Check Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Python is not installed or not on PATH.
    echo  Download from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo  [1/4] Python found:
python --version
echo.

:: Install dependencies
echo  [2/4] Installing Python packages...
echo        This may take a few minutes on first run.
echo.
pip install -r "%~dp0requirements.txt" --quiet --disable-pip-version-check
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [WARNING] Some packages may have failed. Trying with --break-system-packages...
    pip install -r "%~dp0requirements.txt" --quiet --break-system-packages --disable-pip-version-check
)
echo.
echo  [OK] Python packages installed.
echo.

:: Create desktop shortcut
echo  [3/4] Creating desktop shortcut...
call "%~dp0create_shortcut.bat" >nul 2>&1
if exist "%USERPROFILE%\Desktop\LegalMind Pro.lnk" (
    echo  [OK] Desktop shortcut created.
) else (
    echo  [WARNING] Could not create desktop shortcut automatically.
    echo            You can run create_shortcut.bat manually.
)
echo.

:: OCR setup info
echo  [4/4] Optional: OCR for scanned documents
echo.
echo  To enable OCR support, install these (both are free):
echo    Tesseract:  https://github.com/UB-Mannheim/tesseract/wiki
echo    Poppler:    https://github.com/oschwartz10612/poppler-windows/releases
echo.
echo  After installing, set the paths in LegalMind Settings tab.
echo.

:: Ollama info
echo  ----------------------------------------
echo  Optional: AI Analysis with Ollama
echo.
echo  For AI-powered document analysis, install Ollama:
echo    https://ollama.com
echo  Then run:  ollama pull gemma
echo  ----------------------------------------
echo.

echo  ========================================
echo   Installation complete!
echo.
echo   Launch LegalMind Pro:
echo     - Double-click the desktop shortcut
echo     - Or run: legalmind.bat
echo     - Or run: python gui.py
echo  ========================================
echo.
pause
