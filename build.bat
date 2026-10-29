@echo off
echo ========================================
echo     Сборка SecretHelper.exe
echo ========================================
echo.

:: Устанавливаем зависимости
echo [*] Устанавливаю зависимости...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [-] Ошибка установки зависимостей!
    pause
    exit /b 1
)
echo [+] Зависимости установлены
echo.

:: Чистим старую сборку
if exist build rmdir /s /q build
if exist dist\SecretHelper.exe del /q dist\SecretHelper.exe

:: Собираем .exe
echo [*] Собираю SecretHelper.exe...
echo.

pyinstaller --onefile ^
    --windowed ^
    --name "SecretHelper" ^
    --hidden-import=pynput.keyboard._win32 ^
    --hidden-import=pynput.mouse._win32 ^
    --hidden-import=pynput._util.win32 ^
    --hidden-import=PIL._tkinter_finder ^
    client.py

echo.
echo ========================================
if exist dist\SecretHelper.exe (
    echo [+] ГОТОВО! Файл: dist\SecretHelper.exe
    echo [+] Размер:
    for %%A in (dist\SecretHelper.exe) do echo     %%~zA bytes
    echo.
    echo [*] Запускаем...
    start dist\SecretHelper.exe
) else (
    echo [-] Ошибка сборки! Смотри логи выше.
)
echo ========================================
pause
