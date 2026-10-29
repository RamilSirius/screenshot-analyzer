"""
Desktop клиент - скрытое приложение с хоткеями
Нажатие Shift+A делает скриншот и отправляет на сервер
"""
import sys
import os
import signal
import ctypes
import requests
import io
import logging
from PIL import ImageGrab
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QScrollArea, QTextEdit
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from pynput import keyboard
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    # Собранное приложение: cwd = "/", поэтому берём конфиг из бандла
    load_dotenv(os.path.join(sys._MEIPASS, 'client.env'))
else:
    load_dotenv()

# Конфигурация
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Системный шрифт в зависимости от ОС
if sys.platform == 'darwin':
    SYSTEM_FONT = "San Francisco"
elif sys.platform == 'win32':
    SYSTEM_FONT = "Segoe UI"
else:  # Linux
    SYSTEM_FONT = "Ubuntu"


class ScreenshotWorker(QThread):
    """Воркер для отправки скриншота на сервер в фоне"""
    finished = pyqtSignal(dict)

    def __init__(self, image_bytes):
        super().__init__()
        self.image_bytes = image_bytes

    def run(self):
        try:
            logger.info("📤 Отправляю скриншот на сервер...")
            files = {"file": ("screenshot.png", self.image_bytes, "image/png")}

            try:
                response = requests.post(f"{SERVER_URL}/analyze", files=files, timeout=60)
                response.raise_for_status()  # Проверяем статус код
            except requests.exceptions.HTTPError as e:
                logger.error(f"❌ HTTP ошибка {response.status_code}: {response.text}")
                self.finished.emit({"success": False, "error": f"Ошибка сервера: {response.status_code}"})
                return

            result = response.json()

            if result.get("success"):
                logger.info("✅ Ответ от сервера получен успешно")
            else:
                logger.warning(f"⚠️ Сервер вернул ошибку: {result.get('error')}")

            self.finished.emit(result)
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ Не удалось подключиться к серверу {SERVER_URL}")
            self.finished.emit({
                "success": False,
                "error": f"Сервер недоступен ({SERVER_URL}). Проверьте подключение или запустите сервер."
            })
        except requests.exceptions.Timeout:
            logger.error("❌ Таймаут запроса (60 сек)")
            self.finished.emit({"success": False, "error": "Таймаут запроса"})
        except Exception as e:
            logger.error(f"❌ Ошибка: {str(e)}")
            self.finished.emit({"success": False, "error": str(e)})


class OverlayWindow(QMainWindow):
    """Прозрачное окно для показа ответов, скрытое от записи экрана"""

    show_message = pyqtSignal(str)
    hide_signal = pyqtSignal()
    toggle_signal = pyqtSignal()
    quit_signal = pyqtSignal()
    screenshot_signal = pyqtSignal()  # Сигнал для скриншота из главного потока

    def __init__(self):
        super().__init__()
        self.setup_ui()
        self.worker = None
        self.pressed_keys = set()
        self.pressed_vks = set()  # vk-коды, не зависят от раскладки
        self.listener = None
        self._taking_screenshot = False  # Флаг: идёт ли сейчас обработка скриншота

        # Сигналы для потокобезопасности (QueuedConnection гарантирует вызов в главном потоке)
        self.show_message.connect(self._show_message, Qt.ConnectionType.QueuedConnection)
        self.hide_signal.connect(self._hide_window, Qt.ConnectionType.QueuedConnection)
        self.toggle_signal.connect(self._toggle_window, Qt.ConnectionType.QueuedConnection)
        self.quit_signal.connect(self._quit_app, Qt.ConnectionType.QueuedConnection)
        self.screenshot_signal.connect(self._take_screenshot, Qt.ConnectionType.QueuedConnection)

        # Запускаем хоткеи
        self.setup_hotkeys()

        # Watchdog — каждые 3 сек проверяет жив ли listener
        self._watchdog = QTimer()
        self._watchdog.timeout.connect(self._check_listener)
        self._watchdog.start(3000)

        # Автоочистка залипших клавиш каждые 10 сек
        self._key_cleaner = QTimer()
        self._key_cleaner.timeout.connect(self._clear_stuck_keys)
        self._key_cleaner.start(10000)

    def setup_ui(self):
        # Прозрачное окно без рамки, поверх всех
        # На macOS Tool-окно скрывается когда app не активно → используем обычный Window
        if sys.platform == 'darwin':
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint
            )
        else:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.Tool
            )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Главный виджет
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Область прокрутки для длинных ответов
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                background: rgba(10, 10, 15, 80);
                border: none;
                border-radius: 12px;
            }
            QScrollBar:vertical {
                background: rgba(0, 0, 0, 20);
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(150, 150, 150, 60);
                border-radius: 3px;
            }
        """)

        # Текстовое поле с поддержкой markdown
        self.text_edit = QTextEdit("⏳ Загрузка...")
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.text_edit.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: #ffffff;
                padding: 20px;
                font-size: 14px;
                line-height: 1.5;
                border: none;
            }
        """)
        # Используем системный шрифт с fallback
        font = QFont()
        font.setFamilies([SYSTEM_FONT, "Arial", "Helvetica", "System"])
        font.setPointSize(10)
        self.text_edit.setFont(font)

        scroll.setWidget(self.text_edit)
        layout.addWidget(scroll)

        self.setCentralWidget(central)

        # Позиция - правый нижний угол
        screen = QApplication.primaryScreen().geometry()
        self.resize(500, 350)
        self.move(screen.width() - 520, screen.height() - 420)

        if sys.platform == 'win32':
            QTimer.singleShot(100, self.setup_window_properties)
        elif sys.platform == 'darwin':
            QTimer.singleShot(100, self.setup_macos_overlay)

    def setup_window_properties(self):
        """Настройка свойств окна: скрытие от записи (только Windows)"""
        if sys.platform != 'win32':
            return

        try:
            hwnd = int(self.winId())
            # Скрываем от захвата экрана
            WDA_EXCLUDEFROMCAPTURE = 0x11
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            logger.info("✅ Окно настроено: скрыто от записи")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось скрыть окно от записи: {e}")

    def hide_from_capture(self):
        """Обновить свойства окна (скрытие от записи)"""
        if sys.platform == 'win32':
            self.setup_window_properties()
        elif sys.platform == 'darwin':
            self.setup_macos_overlay()

    def setup_macos_overlay(self):
        """macOS: поднять окно выше всех + скрыть от записи экрана"""
        try:
            from AppKit import NSFloatingWindowLevel
            import objc
            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = ns_view.window()
            if ns_window:
                ns_window.setLevel_(NSFloatingWindowLevel)
                # NSWindowSharingNone = 0 — окно не попадает в скриншоты и запись экрана
                ns_window.setSharingType_(0)
                logger.info("✅ macOS: окно поверх всех + скрыто от записи экрана")
        except Exception as e:
            logger.warning(f"⚠️ setup_macos_overlay: {e}")

    def setup_hotkeys(self):
        """Настройка глобальных хоткеев"""
        try:
            # Останавливаем старый listener если есть
            if self.listener and self.listener.is_alive():
                try:
                    self.listener.stop()
                except:
                    pass

            self.listener = keyboard.Listener(
                on_press=self.on_key_press,
                on_release=self.on_key_release,
                suppress=False
            )
            self.listener.daemon = True  # Не блокирует завершение программы
            self.listener.start()
            logger.info("✅ Глобальные хоткеи активированы")

            if sys.platform == 'darwin':
                logger.warning("⚠️ На macOS нужно разрешить Input Monitoring в System Preferences > Security & Privacy")
        except Exception as e:
            logger.error(f"❌ Ошибка при запуске хоткеев: {e}")
            if sys.platform == 'win32':
                logger.warning("⚠️ Возможно, нужны права администратора на Windows")

    def _check_listener(self):
        """Watchdog — проверяет жив ли listener и перезапускает"""
        if self.listener is None or not self.listener.is_alive():
            logger.warning("⚠️ Keyboard listener умер! Перезапускаю...")
            self.pressed_keys.clear()
            self.setup_hotkeys()

    def _clear_stuck_keys(self):
        """Очищаем залипшие клавиши"""
        self.pressed_keys.clear()
        self.pressed_vks.clear()

    def on_key_press(self, key):
        """Обработка нажатия клавиш — vk-коды не зависят от раскладки"""
        try:
            if hasattr(key, 'vk') and key.vk:
                self.pressed_vks.add(key.vk)
            if hasattr(key, 'char') and key.char:
                self.pressed_keys.add(key.char.lower())
            elif hasattr(key, 'name'):
                self.pressed_keys.add(key.name.lower())
        except Exception as e:
            logger.error(f"❌ Ошибка обработки клавиши: {e}")

        # macOS vk: Shift=56/60, A=0, D=2, F=3, Backslash=42
        # Windows vk: Shift=160/161, A=65, D=68, F=70, Backslash=220
        if sys.platform == 'darwin':
            has_shift = 56 in self.pressed_vks or 60 in self.pressed_vks
            has_a     = 0  in self.pressed_vks
            has_d     = 2  in self.pressed_vks
            has_f     = 3  in self.pressed_vks
            has_bs    = 42 in self.pressed_vks
        else:
            has_shift = 160 in self.pressed_vks or 161 in self.pressed_vks or \
                        'shift' in self.pressed_keys or 'shift_r' in self.pressed_keys
            has_a     = 65  in self.pressed_vks
            has_d     = 68  in self.pressed_vks
            has_f     = 70  in self.pressed_vks
            has_bs    = 220 in self.pressed_vks

        if has_shift and has_a:
            if not self._taking_screenshot:
                logger.info("🎯 Хоткей: Shift+A → Делаю скриншот...")
                self._taking_screenshot = True
                self.screenshot_signal.emit()
                self.pressed_vks.clear()
                self.pressed_keys.clear()

        elif has_shift and has_d:
            logger.info("🎯 Хоткей: Shift+D → Переключаю окно")
            self.toggle_signal.emit()
            self.pressed_vks.clear()
            self.pressed_keys.clear()

        elif has_f and has_bs:
            logger.info("🎯 Хоткей: F+\\ → Закрываю приложение...")
            self.quit_signal.emit()
            self.pressed_vks.clear()
            self.pressed_keys.clear()

    def on_key_release(self, key):
        """Обработка отпускания клавиш"""
        try:
            if hasattr(key, 'vk') and key.vk:
                self.pressed_vks.discard(key.vk)
            if hasattr(key, 'char') and key.char:
                self.pressed_keys.discard(key.char.lower())
            elif hasattr(key, 'name'):
                self.pressed_keys.discard(key.name.lower())
        except:
            pass

    def _take_screenshot(self):
        """Делаем скриншот и отправляем на сервер (вызывается в главном потоке)"""
        try:
            # Скрываем окно перед скриншотом
            was_visible = self.isVisible()
            if was_visible:
                self.hide()
                QApplication.processEvents()

            # Небольшая задержка через таймер, чтобы окно успело скрыться
            QTimer.singleShot(150, lambda: self._do_screenshot())

        except Exception as e:
            logger.error(f"❌ Ошибка при создании скриншота: {str(e)}")
            self._show_message(f"❌ Ошибка: {str(e)}")
            self._taking_screenshot = False

    def _save_screenshot(self, image_bytes):
        """Сохранить скриншот в ~/Pictures/Screenshots/"""
        try:
            from datetime import datetime

            # Создаём папку если её нет
            screenshots_dir = os.path.expanduser("~/Pictures/Screenshots")
            os.makedirs(screenshots_dir, exist_ok=True)

            # Генерируем имя файла с временем
            timestamp = datetime.now().strftime("%Y-%m-%d at %H.%M.%S")
            filename = f"Screenshot {timestamp}.png"
            filepath = os.path.join(screenshots_dir, filename)

            # Сохраняем файл
            with open(filepath, 'wb') as f:
                f.write(image_bytes)

            logger.info(f"💾 Скриншот сохранён: {filepath}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при сохранении скриншота: {e}")

    def _do_screenshot(self):
        """Непосредственно делаем скриншот и отправляем (вызывается в главном потоке)"""
        try:
            # Делаем скриншот
            screenshot = ImageGrab.grab()
            logger.info(f"📸 Скриншот сделан: {screenshot.size[0]}x{screenshot.size[1]}")

            # Конвертируем в bytes
            buffer = io.BytesIO()
            screenshot.save(buffer, format='PNG')
            buffer.seek(0)  # ВАЖНО: Перемещаемся на начало буфера
            image_bytes = buffer.getvalue()
            size_kb = len(image_bytes) / 1024
            logger.info(f"📦 Размер: {size_kb:.1f} KB")

            # Сохраняем скриншот локально
            self._save_screenshot(image_bytes)

            # Сохраняем ответ без показа окна — юзер сам откроет через Shift+D
            self._update_text("⏳ Анализирую скриншот...")

            # Отправляем в фоновом потоке
            # Проверяем, не идёт ли уже другой запрос
            if self.worker and self.worker.isRunning():
                logger.warning("⚠️ Предыдущий запрос ещё обрабатывается, отмена нового")
                self._taking_screenshot = False
                return

            self.worker = ScreenshotWorker(image_bytes)
            self.worker.finished.connect(self.on_response, Qt.ConnectionType.QueuedConnection)
            self.worker.finished.connect(self._on_screenshot_done)
            self.worker.start()

        except Exception as e:
            logger.error(f"❌ Ошибка при создании скриншота: {str(e)}")
            self._show_message(f"❌ Ошибка: {str(e)}")
            self._taking_screenshot = False

    def _on_screenshot_done(self):
        """Вызывается когда скриншот полностью обработан"""
        self._taking_screenshot = False

    def on_response(self, result):
        """Обработка ответа от сервера — обновляем текст, но НЕ показываем окно"""
        if result.get("success"):
            answer = result.get("answer", "Нет ответа")
            self._update_text(f"✅ Ответ:\n\n{answer}")
            logger.info("✅ Ответ получен. Нажми Shift+D чтобы посмотреть")
        else:
            error = result.get("error", "Неизвестная ошибка")
            self._update_text(f"❌ Ошибка: {error}")
            logger.warning(f"⚠️ Ошибка: {error}")

    def _convert_to_html(self, text):
        """Конвертирует markdown в HTML"""
        import re
        html_text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        html_text = html_text.replace('\n', '<br>')
        return f'<div style="color: #ffffff; font-family: {SYSTEM_FONT}, Arial; font-size: 13px;">{html_text}</div>'

    def _update_text(self, text):
        """Обновить текст в окне БЕЗ показа окна (вызывается в главном потоке)"""
        try:
            html_content = self._convert_to_html(text)

            def do_update():
                try:
                    self.text_edit.setHtml(html_content)
                except Exception as e:
                    logger.error(f"❌ Ошибка setHtml: {e}")
                    self.text_edit.setPlainText(text)

            QTimer.singleShot(0, do_update)
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке сообщения: {e}")

    def _show_message(self, text):
        """Обновить текст И показать окно"""
        try:
            html_content = self._convert_to_html(text)

            def do_update():
                try:
                    self.text_edit.setHtml(html_content)
                    self.show()
                    self.activateWindow()
                except Exception as e:
                    logger.error(f"❌ Ошибка setHtml: {e}")
                    self.text_edit.setPlainText(text)
                    self.show()
                    self.activateWindow()

            QTimer.singleShot(0, do_update)
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке сообщения: {e}")

    def _hide_window(self):
        """Скрыть окно"""
        self.hide()

    def _toggle_window(self):
        """Переключить видимость окна"""
        if self.isVisible():
            logger.info("👁️ Окно скрыто")
            self.hide()
        else:
            logger.info("👁️ Окно показано")
            self.show()
            self.raise_()
            if sys.platform == 'darwin':
                try:
                    import objc
                    ns_view = objc.objc_object(c_void_p=int(self.winId()))
                    ns_window = ns_view.window()
                    if ns_window:
                        # Показываем окно поверх всех БЕЗ перехвата фокуса
                        ns_window.orderFrontRegardless()
                except Exception as e:
                    logger.warning(f"⚠️ orderFrontRegardless: {e}")
            self.hide_from_capture()

    def _quit_app(self):
        """Закрыть приложение полностью"""
        logger.info("👋 Завершение работы...")
        if self.listener:
            self.listener.stop()
        QApplication.quit()

    def mousePressEvent(self, event):
        """Клик по окну скрывает его"""
        if event.button() == Qt.MouseButton.RightButton:
            self.hide()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """ESC скрывает окно"""
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        super().keyPressEvent(event)

    def closeEvent(self, event):
        """Останавливаем listener при закрытии"""
        if self.listener:
            self.listener.stop()
        super().closeEvent(event)


# Глобальная переменная для доступа к окну из обработчика сигнала
_app_instance = None
_window_instance = None

def signal_handler(sig, frame):
    """Обработчик Ctrl+C для корректного завершения"""
    print("\n🛑 Получен сигнал завершения (Ctrl+C)...")
    logger.info("👋 Завершение работы по Ctrl+C...")

    if _window_instance:
        # Останавливаем listener хоткеев
        if hasattr(_window_instance, 'listener') and _window_instance.listener:
            _window_instance.listener.stop()

    if _app_instance:
        _app_instance.quit()

    sys.exit(0)

def main():
    global _app_instance, _window_instance

    # Настройка обработчика Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)

    # Выводим информацию о платформе
    platform_name = {
        'darwin': 'macOS',
        'win32': 'Windows',
        'linux': 'Linux'
    }.get(sys.platform, 'Unknown')

    print("=" * 50)
    print(f"🔒 Секретный помощник запущен! ({platform_name})")
    print("=" * 50)
    print()
    print(f"📡 Сервер: {SERVER_URL}")
    print()
    print("📌 Управление:")
    print("   Shift+A   → Сделать скриншот и получить ответ")
    print("   Shift+D   → Показать/скрыть окно с ответом")
    print("   F + '\\' → Закрыть приложение полностью")
    print("   ESC      → Скрыть окно с ответом")
    print("   ПКМ      → Скрыть окно с ответом")
    print("   Ctrl+C   → Закрыть приложение (в терминале)")
    print()

    if sys.platform == 'darwin':
        print("⚠️  На macOS нужно разрешить Input Monitoring:")
        print("   System Preferences → Security & Privacy → Input Monitoring")
        print()

    app = QApplication(sys.argv)
    _app_instance = app

    if sys.platform == 'darwin':
        # Accessory-приложение (без иконки в Dock) может показываться поверх
        # чужого полноэкранного окна, не переключая рабочий стол
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception as e:
            logger.warning(f"⚠️ Не удалось переключить приложение в accessory-режим: {e}")
    app.setQuitOnLastWindowClosed(False)  # Не закрываться при скрытии окна

    window = OverlayWindow()
    _window_instance = window
    # Окно скрыто по умолчанию, появится при первом хоткее

    logger.info("🚀 Клиент запущен, слушаю хоткеи...")
    print("-" * 50)
    print("💡 Нажмите Ctrl+C для выхода")
    print("-" * 50)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
