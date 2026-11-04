@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo Не найден Python Launcher "py".
    echo Установите Python для Windows и включите его в PATH.
    pause
    exit /b 1
)

rem pyw.exe — оконный интерпретатор: GUI запускается без окна консоли.
where pyw >nul 2>&1
if errorlevel 1 (
    py gui.py
    if errorlevel 1 (
        echo.
        echo GUI завершился с ошибкой. Проверьте сообщение выше.
        pause
    )
) else (
    start "" pyw gui.py
)

endlocal
