# 🍎 macOS Setup - Полное руководство

## 📖 Что было сделано

Приложение "Секретный помощник" адаптировано для работы на macOS с полной поддержкой горячих клавиш, глобального слушания и анализа скриншотов через GPT-4 Vision.

### ✅ Созданные файлы

| Файл | Описание |
|------|---------|
| `client_fixed.py` | ✨ Новый исправленный клиент (кроссплатформный) |
| `systemapp_macos.spec` | 🍎 PyInstaller конфиг для macOS |
| `build_macos.sh` | 🔨 Скрипт сборки для macOS |
| `README_MACOS.md` | 📚 Подробное руководство |
| `BUGFIXES.md` | 🐛 Список исправленных багов |

### ❌ Старые файлы (оставлены для совместимости)

- `client.py` - Windows-only версия
- `systemapp.spec` - Windows конфиг
- `build.bat` - Windows скрипт

## 🎯 Быстрый старт (3 шага)

### Шаг 1: Зависимости

```bash
cd "/Telegram Desktop/system app"
pip3 install -r requirements.txt
```

### Шаг 2: API ключ

Создайте `.env` файл:
```
OPENAI_API_KEY=sk-your-api-key
# или
GEMINI_API_KEY=your-gemini-key
```

### Шаг 3: Запуск

Два терминала:

**Терминал 1 (Сервер):**
```bash
python3 server.py
```

**Терминал 2 (Клиент):**
```bash
python3 client_fixed.py
```

## 🔧 Основные исправления в client_fixed.py

### 1. Шрифты
```python
# ❌ Было (Windows only)
self.text_edit.setFont(QFont("Segoe UI", 10))

# ✅ Теперь (кроссплатформно)
if sys.platform == 'darwin':
    SYSTEM_FONT = "San Francisco"
elif sys.platform == 'win32':
    SYSTEM_FONT = "Segoe UI"
else:
    SYSTEM_FONT = "Ubuntu"
```

### 2. Windows-specific код
```python
# ❌ Было (падал на macOS)
WDA_EXCLUDEFROMCAPTURE = 0x11
ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

# ✅ Теперь (с проверкой платформы)
if sys.platform == 'win32':
    # Только Windows код
```

### 3. BytesIO buffer
```python
# ❌ Было (может вернуть пусто)
screenshot.save(buffer, format='PNG')
image_bytes = buffer.getvalue()

# ✅ Теперь (правильно)
screenshot.save(buffer, format='PNG')
buffer.seek(0)  # Важно!
image_bytes = buffer.getvalue()
```

### 4. HTTP запросы
```python
# ❌ Было (может крашнуться)
response = requests.post(url, files=files, timeout=60)
result = response.json()

# ✅ Теперь (безопасно)
response = requests.post(url, files=files, timeout=60)
response.raise_for_status()  # Проверяем статус
result = response.json()
```

### 5. Race conditions
```python
# ❌ Было (может быть утечка потоков)
self.worker = ScreenshotWorker(image_bytes)
self.worker.start()

# ✅ Теперь (безопасно)
if self.worker and self.worker.isRunning():
    logger.warning("Предыдущий запрос ещё обрабатывается")
    return
self.worker = ScreenshotWorker(image_bytes)
self.worker.start()
```

## 🎮 Горячие клавиши

| Система | Клавиша | Действие |
|---------|---------|----------|
| Все | Shift+A | Скриншот + анализ |
| Все | Shift+D | Показать/скрыть |
| Все | F+\ | Выход |
| macOS | Cmd+A | ❌ Не работает (используйте Shift+A) |

## 📱 Запуск как приложение (.app)

```bash
# Сборка
bash build_macos.sh

# Запуск
open dist/SystemApp.app
```

## 🔐 Разрешение Input Monitoring на macOS

1. System Preferences → Security & Privacy
2. Input Monitoring
3. Нажмите замок 🔒
4. Добавьте Python в список
5. Перезагрузитесь

## 🌐 Переменные окружения (.env)

```bash
# OpenAI API ключ (для server.py)
OPENAI_API_KEY=sk-xxx

# Google Gemini API ключ (для server_gemini.py)
GEMINI_API_KEY=xxx

# Адрес сервера (опционально)
SERVER_URL=http://localhost:8000

# Для удалённого сервера
SERVER_URL=https://your-server.com
```

## 📊 Сравнение версий

| Параметр | client.py ❌ | client_fixed.py ✅ |
|----------|------------|-----------------|
| Windows | ✅ | ✅ |
| macOS | ❌ | ✅ |
| Linux | ⚠️ | ✅ |
| Шрифты | Segoe UI only | Системные |
| Скрытие окна | Windows API | Кроссплатформно |
| Buffer bug | ❌ | ✅ |
| HTTP errors | ⚠️ | ✅ |
| Race conditions | ❌ | ✅ |
| .env support | ❌ | ✅ |

## 🚀 Следующие шаги

### Опция 1: Локальный сервер (рекомендуется для разработки)
```bash
# Запустите оба скрипта локально
python3 server.py          # Терминал 1
python3 client_fixed.py    # Терминал 2
```

### Опция 2: Готовое приложение
```bash
bash build_macos.sh
open dist/SystemApp.app
```

### Опция 3: Удалённый сервер
```bash
# Измените SERVER_URL в .env
SERVER_URL=https://backend-helper-6nmh.onrender.com
python3 client_fixed.py
```

## ✨ Что теперь работает на macOS

- ✅ Глобальные горячие клавиши (Shift+A, Shift+D)
- ✅ Скриншоты (через Pillow)
- ✅ Анализ GPT-4 Vision
- ✅ Прозрачное окно с ответом
- ✅ Правильные системные шрифты
- ✅ Обработка ошибок
- ✅ Input Monitoring разрешения

## 🐛 Если что-то не работает

1. **Проверьте логи** - смотрите вывод в терминале
2. **Input Monitoring** - убедитесь в разрешениях
3. **Сервер запущен** - проверьте второй терминал
4. **API ключ** - проверьте `.env`
5. **Python версия** - нужно 3.8+

```bash
# Диагностика
python3 --version
python3 -m pip show PyQt6
python3 -m pip show pynput
```

## 📝 Структура проекта после адаптации

```
system app/
├── 📄 README.txt              (старый)
├── 📄 README_MACOS.md         (новый - подробный гайд)
├── 📄 MACOS_SETUP.md          (этот файл)
├── 📄 BUGFIXES.md             (список исправлений)
│
├── 🐍 client.py               (старый - Windows)
├── 🐍 client_fixed.py         (новый - кроссплатформный)
├── 🐍 server.py               (OpenAI)
├── 🐍 server_gemini.py        (Google Gemini)
│
├── 🔧 systemapp.spec          (Windows конфиг)
├── 🔧 systemapp_macos.spec    (macOS конфиг)
│
├── 🔨 build.bat               (Windows скрипт)
├── 🔨 build_macos.sh          (macOS скрипт)
│
├── 📋 requirements.txt
└── 🔐 .env                    (создать самостоятельно)
```

---

🎉 **Приложение готово к работе на macOS!**

Выбери способ запуска выше и начинай анализировать скриншоты с помощью GPT-4! 🚀
