@echo off
chcp 65001 >nul
title my-mail - 설치
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
  echo.
  echo   [오류] Python을 찾을 수 없습니다.
  echo   https://www.python.org 에서 설치할 때
  echo   "Add Python to PATH" 를 반드시 체크하세요.
  echo.
  pause
  exit /b 1
)

echo.
echo [1/2] 필요한 패키지를 설치합니다...
echo.
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo [2/2] 브라우저(Chromium)를 설치합니다... 용량이 커서 몇 분 걸립니다.
echo.
%PY% -m playwright install chromium
if errorlevel 1 goto fail

echo.
echo ==================================================
echo   설치가 완료되었습니다.
echo   run.bat 을 실행하면 웹 콘솔이 브라우저에서 열립니다.
echo ==================================================
echo.
pause
exit /b 0

:fail
echo.
echo   [오류] 설치에 실패했습니다. 위 메시지를 확인하세요.
echo.
pause
exit /b 1
