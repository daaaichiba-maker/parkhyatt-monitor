@echo off
setlocal
echo ============================================================
echo  Park Hyatt Tokyo 予約監視システム セットアップ
echo ============================================================
echo.

:: Pythonの確認
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo https://www.python.org/ からインストールしてください。
    pause
    exit /b 1
)

:: 依存ライブラリのインストール
echo [1/3] Python ライブラリをインストール中...
pip install playwright --quiet
if errorlevel 1 ( echo [ERROR] playwright のインストールに失敗しました。 & pause & exit /b 1 )

echo [2/3] Playwright ブラウザをインストール中...
playwright install chromium
if errorlevel 1 ( echo [ERROR] Playwright ブラウザのインストールに失敗しました。 & pause & exit /b 1 )

:: .env の確認
echo.
echo [3/3] .env ファイルの設定を確認してください。
echo.
echo  場所: %~dp0.env
echo.
type "%~dp0.env"
echo.
echo  上記の GMAIL_ADDRESS, GMAIL_APP_PASSWORD, NOTIFY_EMAIL を
echo  実際の値に書き換えてから続けてください。
echo.
pause

:: タスクスケジューラへの登録
echo.
echo ============================================================
echo  Windowsタスクスケジューラに5分おきの監視を登録します
echo ============================================================
echo.

set TASK_NAME=ParkHyatt_HarryWinston_Monitor
set SCRIPT_PATH=%~dp0checker.py
set PYTHON_PATH=

:: pythonのフルパスを取得
for /f "delims=" %%i in ('where python') do (
    set PYTHON_PATH=%%i
    goto :found_python
)
:found_python

echo Python パス: %PYTHON_PATH%
echo スクリプト: %SCRIPT_PATH%
echo.

:: 既存タスクを削除してから再登録
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_PATH%\" \"%SCRIPT_PATH%\"" ^
  /sc minute ^
  /mo 5 ^
  /ru "%USERNAME%" ^
  /rl highest ^
  /f

if errorlevel 1 (
    echo [ERROR] タスクスケジューラへの登録に失敗しました。
    echo 管理者として実行してみてください。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  セットアップ完了！
echo ============================================================
echo.
echo  監視タスク名 : %TASK_NAME%
echo  実行間隔    : 5分おき
echo  ログファイル : %~dp0checker.log
echo  スクリーンショット: %~dp0last_check.png
echo.
echo  タスクを今すぐテスト実行しますか？ (Y/N)
set /p RUN_NOW="> "
if /i "%RUN_NOW%"=="Y" (
    echo.
    echo テスト実行中...
    "%PYTHON_PATH%" "%SCRIPT_PATH%"
    echo.
    echo 完了。checker.log を確認してください。
)

echo.
echo  監視を停止するには stop.bat を実行してください。
pause
