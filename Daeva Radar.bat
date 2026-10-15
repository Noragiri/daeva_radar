@echo off
rem Starts Daeva Radar without a console window; it asks for administrator rights (WinDivert needs them).
rem The "Daeva Radar" shortcut (python daeva_radar.py --shortcut) does the same with the icon.
start "" pythonw "%~dp0daeva_radar.py" %*
