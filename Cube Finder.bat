@echo off
rem Starts the cube radar without a console window; it asks for administrator rights (WinDivert needs them).
rem The "Cube Finder" shortcut (python cube_finder.py --shortcut) does the same with the icon.
start "" pythonw "%~dp0cube_finder.py" %*
