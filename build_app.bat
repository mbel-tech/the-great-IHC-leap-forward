@echo off
REM Set up work\appenv, the Python 3.13 environment run_app.bat runs the app in.
REM
REM This used to go on to freeze the app with PyInstaller (app\lsapp.spec, now
REM deleted). That build left out StarDist, so it could not run Measure, and it
REM could miss libraries the stages load at run time; it also closed its window
REM on failure, so nobody saw why. The app now ships as an installer instead, built by
REM packaging\windows\build.ps1 on GitHub Actions - see the README's Download.
REM
REM Python 3.13 rather than 3.14: pylibCZIrw publishes no cp314 wheel.
REM The venv is created once and reused; delete work\appenv to start over.
setlocal
cd /d "%~dp0"

echo Most people want the installer instead of this:
echo   https://github.com/mbel-tech/the-great-IHC-leap-forward/releases/latest
echo   (under Assets: The-Great-IHC-Leap-Forward-Setup-^<version^>.exe)
echo.

if exist work\appenv\Scripts\python.exe (
  echo work\appenv already exists. Delete it to rebuild it.
  goto :done
)

echo Creating work\appenv with Python 3.13...
py -3.13 -m venv work\appenv || goto :fail
work\appenv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
work\appenv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-czi.txt -r packaging\windows\requirements-bundle.txt || goto :fail

:done
echo.
echo Ready. Start the app with run_app.bat.
pause
goto :eof

:fail
echo.
echo SETUP FAILED - see the output above.
echo If "py -3.13" was not found, install Python 3.13 from python.org first.
pause
exit /b 1
