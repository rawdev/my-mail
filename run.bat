@echo off
chcp 65001 >nul
title my-mail 웹 콘솔
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
  echo.
  echo   [오류] Python을 찾을 수 없습니다.
  echo   https://www.python.org 에서 설치한 뒤 다시 실행하세요.
  echo.
  pause
  exit /b 1
)

%PY% -c "import playwright, rich, flask" >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [알림] 필요한 패키지가 설치되어 있지 않습니다.
  echo   setup.bat 을 먼저 실행하세요.
  echo.
  pause
  exit /b 1
)

echo.
echo ==================================================
echo    my-mail 웹 콘솔을 시작합니다.
echo    잠시 후 브라우저가 자동으로 열립니다.
echo.
echo    주소   : http://127.0.0.1:8765
echo    종료   : 이 창에서 Ctrl+C  (또는 창 닫기)
echo.
echo    터미널 메뉴를 쓰려면 console.bat 을 실행하세요.
echo ==================================================
echo.

%PY% webapp.py
echo.
echo   서버가 종료되었습니다.
pause
