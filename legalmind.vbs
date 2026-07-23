' LegalMind Pro silent launcher.
' Runs pythonw gui.py with NO console window flash, even if pythonw spawns
' children that don't pass CREATE_NO_WINDOW themselves.
'
' Usage:
'   - Double-click this file to launch
'   - Point a desktop shortcut's Target at: wscript.exe "<path>\legalmind.vbs"
Option Explicit
Dim sh, fso, here, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
' 0 = hidden window, False = don't wait for completion
cmd = "pythonw.exe """ & here & "\gui.py"""
sh.Run cmd, 0, False
