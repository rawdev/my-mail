@echo off
chcp 65001 >nul
title my-mail - 웹메일 콘솔 뷰어
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

%PY% -c "import playwright, rich" >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [알림] 필요한 패키지가 설치되어 있지 않습니다.
  echo   setup.bat 을 먼저 실행하세요.
  echo.
  pause
  exit /b 1
)

:menu
cls
echo ==================================================
echo               my-mail  웹메일 뷰어
echo ==================================================
echo.
echo    1. 최신 메일 확인 (전체 계정)
echo    2. 메일 본문 읽기
echo    3. 새 메일 감시 (60초 간격)
echo.
echo    4. 계정 추가
echo    5. 계정 목록
echo    6. 계정 삭제
echo    7. 수동 로그인 (캡차 / 2단계 인증)
echo    8. 페이지 구조 확인 (inspect)
echo.
echo    0. 종료
echo.
choice /c 123456780 /n /m "번호를 선택하세요: "
set "sel=%errorlevel%"

if "%sel%"=="1" goto fetch
if "%sel%"=="2" goto read
if "%sel%"=="3" goto watch
if "%sel%"=="4" goto add
if "%sel%"=="5" goto list
if "%sel%"=="6" goto remove
if "%sel%"=="7" goto login
if "%sel%"=="8" goto inspect
if "%sel%"=="9" exit /b 0
REM 입력을 읽을 수 없는 환경(파이프 등)에서는 종료 (무한 반복 방지)
exit /b 1

:fetch
cls
echo [ 최신 메일을 가져오는 중... ]
echo.
%PY% mymail.py fetch
goto done

:read
cls
%PY% mymail.py list
echo.
set "acct="
set /p "acct=메일을 읽을 계정 ID (취소하려면 Enter): "
if "%acct%"=="" goto menu
cls
echo [ %acct% 의 메일 목록을 가져오는 중... ]
echo.
%PY% mymail.py fetch --account %acct%
echo.
set "num="
set /p "num=읽을 메일 번호 (취소하려면 Enter): "
if "%num%"=="" goto menu
cls
echo [ %acct% / %num% 번 메일을 여는 중... ]
echo.
%PY% mymail.py read %num% --account %acct%
goto done

:watch
cls
echo [ 새 메일 감시를 시작합니다. 중지하려면 Ctrl+C ]
echo.
%PY% mymail.py fetch --watch 60
goto done

:add
cls
%PY% mymail.py add
goto done

:list
cls
%PY% mymail.py list
goto done

:remove
cls
%PY% mymail.py list
echo.
set "acct="
set /p "acct=삭제할 계정 ID (취소하려면 Enter): "
if "%acct%"=="" goto menu
%PY% mymail.py remove %acct%
goto done

:login
cls
%PY% mymail.py list
echo.
set "acct="
set /p "acct=로그인할 계정 ID (전체는 Enter): "
if "%acct%"=="" (
  %PY% mymail.py login
) else (
  %PY% mymail.py login --account %acct%
)
goto done

:inspect
cls
%PY% mymail.py list
echo.
set "acct="
set /p "acct=확인할 계정 ID (전체는 Enter): "
if "%acct%"=="" (
  %PY% mymail.py inspect
) else (
  %PY% mymail.py inspect --account %acct%
)
goto done

:done
echo.
echo --------------------------------------------------
pause
goto menu
