#!/bin/bash

# Скрипт для сборки macOS приложения (.app бандл)
# Использование: bash build_macos.sh

set -e  # Выход при первой ошибке

echo "=========================================="
echo "🍎 SystemApp - macOS Build Script"
echo "=========================================="
echo ""

# Проверяем, что мы в правильной директории
if [ ! -f "client.py" ]; then
    echo "❌ Ошибка: client.py не найден"
    echo "Запустите скрипт из папки с проектом"
    exit 1
fi

# Проверяем наличие Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Ошибка: Python3 не установлен"
    exit 1
fi

echo "✅ Python3 найден: $(python3 --version)"
echo ""

# Проверяем наличие PyInstaller
echo "📦 Проверяю PyInstaller..."
if ! python3 -m pip show pyinstaller &> /dev/null; then
    echo "⚠️  PyInstaller не установлен"
    echo "📥 Устанавливаю зависимости..."
    python3 -m pip install -r requirements.txt --upgrade
else
    echo "✅ PyInstaller найден"
fi

echo ""
echo "🔨 Очищаю старые сборки..."
rm -rf build/
rm -rf dist/
rm -rf SystemApp.app

echo ""
echo "🏗️  Собираю приложение (может занять 1-2 минуты)..."
python3 -m PyInstaller systemapp_macos.spec --noconfirm

if [ -d "dist/SystemApp.app" ]; then
    echo ""
    echo "✅ УСПЕХ! Приложение собрано"
    echo ""
    echo "📍 Расположение: dist/SystemApp.app"
    echo ""
    echo "▶️  Для запуска:"
    echo "   open dist/SystemApp.app"
    echo ""
    echo "⚠️  Перед запуском:"
    echo "   1. Установите SERVER_URL в .env (или используйте localhost)"
    echo "   2. Убедитесь, что сервер запущен: python3 server.py"
    echo "   3. Разрешите Input Monitoring в System Preferences"
    echo ""
else
    echo ""
    echo "❌ Ошибка при сборке приложения"
    echo "Проверьте логи выше"
    exit 1
fi
