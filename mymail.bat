@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
  echo [오류] Python을 찾을 수 없습니다.
  exit /b 1
)

if "%~1"=="" (
  echo.
  echo   사용법: mymail ^<명령^> [옵션]
  echo.
  echo     mymail add                       계정 추가
  echo     mymail list                      계정 목록
  echo     mymail remove ^<계정ID^>           계정 삭제
  echo     mymail fetch                     최신 메일 확인
  echo     mymail fetch --account ^<계정ID^>  특정 계정만
  echo     mymail fetch --watch 60          60초마다 새 메일 감시
  echo     mymail login --account ^<계정ID^>  수동 로그인
  echo.
  echo   메뉴로 실행하려면 run.bat 을 더블클릭하세요.
  echo.
  exit /b 0
)

%PY% mymail.py %*
