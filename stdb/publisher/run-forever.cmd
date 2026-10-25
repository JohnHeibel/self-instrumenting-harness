@echo off
rem Keeps the publisher running: restarts it if the process ever exits (crash, Node error, reboot via Task Scheduler).
rem Usage: run-forever.cmd C:\path\to\runs\<run> [extra publisher options]
rem Server, database and token come from STDB_SERVER, STDB_MODULE, STDB_TOKEN (or the token file in the state dir).
setlocal
cd /d "%~dp0"
set RUNDIR=%~1
if "%RUNDIR%"=="" (echo usage: run-forever.cmd ^<run-dir^> [options] & exit /b 2)
shift
set EXTRA=
:collect
if "%~1"=="" goto go
set EXTRA=%EXTRA% %1
shift
goto collect
:go
call npx tsx src\cli.ts --run-dir "%RUNDIR%" --forever %EXTRA%
echo [run-forever] publisher exited with %ERRORLEVEL%; restarting in 10 s
timeout /t 10 /nobreak >nul
goto go
