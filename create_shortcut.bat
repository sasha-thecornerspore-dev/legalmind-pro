@echo off
:: Creates a "LegalMind Pro" desktop shortcut pointing to gui.py
:: with the legalmind.ico icon.

set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=%DESKTOP%\LegalMind Pro.lnk"

echo Creating desktop shortcut...

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$sc = $ws.CreateShortcut('%SHORTCUT%');" ^
  "$sc.TargetPath = 'wscript.exe';" ^
  "$sc.Arguments = '\"%SCRIPT_DIR%legalmind.vbs\"';" ^
  "$sc.WorkingDirectory = '%SCRIPT_DIR%';" ^
  "$sc.IconLocation = '%SCRIPT_DIR%legalmind.ico';" ^
  "$sc.Description = 'LegalMind Pro — Legal Document Intelligence';" ^
  "$sc.Save()"

if exist "%SHORTCUT%" (
    echo.
    echo SUCCESS: Shortcut created on Desktop!
    echo   %SHORTCUT%
) else (
    echo.
    echo ERROR: Failed to create shortcut.
)

echo.
pause
