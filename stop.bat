@echo off
set TASK_NAME=ParkHyatt_HarryWinston_Monitor
schtasks /delete /tn "%TASK_NAME%" /f
if errorlevel 1 (
    echo タスクが見つかりませんでした（すでに停止済みかもしれません）。
) else (
    echo 監視タスクを停止しました。
)
pause
