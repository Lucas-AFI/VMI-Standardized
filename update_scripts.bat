@echo off
REM VMI Update Process - Pull latest scripts from GitHub
REM Schedule this via Task Scheduler to run monthly

echo Pulling latest VMI scripts from GitHub...
echo.

cd /d C:\update_process

git pull origin main

echo.
if %ERRORLEVEL% EQU 0 (
    echo Scripts updated successfully!
) else (
    echo Update failed with error code %ERRORLEVEL%
    echo Please check your internet connection or contact AFI support.
)
