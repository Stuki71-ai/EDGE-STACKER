' Silent launcher for run_local_nba.ps1 — no PowerShell popup window.
' WScript.Shell.Run with intWindowStyle=0 hides the console entirely.
Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\istva\.claude\CODE\EDGE STACKER\run_local_nba.ps1""", 0, False
