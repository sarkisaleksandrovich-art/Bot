@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo Не найден запускатель Python "py".
    echo Установите Python и включите его в PATH.
    pause
    exit /b 1
)

py gui.py
if errorlevel 1 (
    echo.
    echo GUI завершился с ошибкой. Проверьте сообщения выше.
    pause
)

endlocal
