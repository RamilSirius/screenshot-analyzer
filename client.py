"""
Desktop клиент - скрытое приложение с хоткеями
Нажатие F + "-" делает скриншот и отправляет на сервер
"""
import sys
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

# Конфигурация
SERVER_URL = "https://backend-helper-6nmh.onrender.com"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

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
            response = requests.post(f"{SERVER_URL}/analyze", files=files, timeout=60)
            result = response.json()
            
            if result.get("success"):
                logger.info("✅ Ответ от сервера получен успешно")
            else:
                logger.warning(f"⚠️ Сервер вернул ошибку: {result.get('error')}")
            
            self.finished.emit(result)
        except requests.exceptions.ConnectionError:
            logger.error("❌ Не удалось подключиться к серверу! Запущен ли server.py?")
            self.finished.emit({"success": False, "error": "Сервер недоступен. Запустите server.py"})
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
        self.listener = None
        
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
        
        # Автоочистка залипших клавиш каждые 2 сек
        self._key_cleaner = QTimer()
        self._key_cleaner.timeout.connect(self._clear_stuck_keys)
        self._key_cleaner.start(2000)
        
    def setup_ui(self):
        # Прозрачное окно без рамки, поверх всех
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
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
        self.text_edit.setFont(QFont("Segoe UI", 10))
        
        scroll.setWidget(self.text_edit)
        layout.addWidget(scroll)
        
        self.setCentralWidget(central)
        
        # Позиция - правый нижний угол
        screen = QApplication.primaryScreen().geometry()
        self.resize(500, 350)
        self.move(screen.width() - 520, screen.height() - 420)
        
        # Скрываем от записи экрана и делаем прозрачным для кликов (Windows)
        if sys.platform == 'win32':
            QTimer.singleShot(100, self.setup_window_properties)
    
    def setup_window_properties(self):
        """Настройка свойств окна: скрытие от записи"""
        hwnd = int(self.winId())
        
        # Скрываем от захвата экрана
        WDA_EXCLUDEFROMCAPTURE = 0x11
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        
        logger.info("✅ Окно настроено: скрыто от записи")
    
    def hide_from_capture(self):
        """Обновить свойства окна (скрытие от записи)"""
        if sys.platform == 'win32':
            self.setup_window_properties()
    
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
        except Exception as e:
            logger.error(f"❌ Ошибка при запуске хоткеев: {e}")
            logger.warning("⚠️ Возможно, нужны права администратора на Windows")
    
    def _check_listener(self):
        """Watchdog — проверяет жив ли listener и перезапускает"""
        if self.listener is None or not self.listener.is_alive():
            logger.warning("⚠️ Keyboard listener умер! Перезапускаю...")
            self.pressed_keys.clear()
            self.setup_hotkeys()
    
    def _clear_stuck_keys(self):
        """Очищаем залипшие клавиши"""
        if len(self.pressed_keys) > 0:
            self.pressed_keys.clear()
    
    def on_key_press(self, key):
        """Обработка нажатия клавиш"""
        try:
            # Получаем символ клавиши или её имя
            key_str = None
            
            if hasattr(key, 'char') and key.char:
                key_str = key.char.lower()
                self.pressed_keys.add(key_str)
            elif hasattr(key, 'name'):
                # Для функциональных клавиш (F1-F12, etc)
                key_str = key.name.lower()
                self.pressed_keys.add(key_str)
            elif hasattr(key, 'vk'):
                # Для специальных клавиш через vk код
                # F клавиша имеет vk = 70
                if key.vk == 70:  # F key
                    key_str = 'f'
                    self.pressed_keys.add('f')
                else:
                    self.pressed_keys.add(key.vk)
            
            # Логируем только если это может быть частью хоткея
            if key_str in ['f', '-', '=', '+', '\\'] or (hasattr(key, 'vk') and key.vk == 70):
                key_repr = str(key_str) if key_str else (f"vk:{key.vk}" if hasattr(key, 'vk') else str(key))
                logger.debug(f"🔑 Нажата клавиша: {key_repr} | Активные: {list(self.pressed_keys)}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки клавиши: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        has_shift = 'shift' in self.pressed_keys or 'shift_r' in self.pressed_keys
        has_a = 'a' in self.pressed_keys or 'A' in self.pressed_keys
        has_d = 'd' in self.pressed_keys or 'D' in self.pressed_keys
        
        # Shift + A → Скриншот и анализ
        if has_shift and has_a:
            logger.info("🎯 Хоткей: Shift+A → Делаю скриншот...")
            self.screenshot_signal.emit()
            self.pressed_keys.clear()
        
        # Shift + D → Toggle окна (показать/скрыть)
        elif has_shift and has_d:
            logger.info("🎯 Хоткей: Shift+D → Переключаю окно")
            self.toggle_signal.emit()
            self.pressed_keys.clear()
        
        # F + "\" → Закрыть приложение полностью
        elif 'f' in self.pressed_keys and '\\' in self.pressed_keys:
            logger.info("🎯 Хоткей: F + '\\' → Закрываю приложение...")
            self.quit_signal.emit()
            self.pressed_keys.clear()
    
    def on_key_release(self, key):
        """Обработка отпускания клавиш"""
        try:
            if hasattr(key, 'char') and key.char:
                self.pressed_keys.discard(key.char.lower())
            elif hasattr(key, 'name'):
                self.pressed_keys.discard(key.name.lower())
            elif hasattr(key, 'vk'):
                if key.vk == 70:  # F key
                    self.pressed_keys.discard('f')
                else:
                    self.pressed_keys.discard(key.vk)
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
    
    def _do_screenshot(self):
        """Непосредственно делаем скриншот и отправляем (вызывается в главном потоке)"""
        try:
            # Делаем скриншот
            screenshot = ImageGrab.grab()
            logger.info(f"📸 Скриншот сделан: {screenshot.size[0]}x{screenshot.size[1]}")
            
            # Конвертируем в bytes
            buffer = io.BytesIO()
            screenshot.save(buffer, format='PNG')
            image_bytes = buffer.getvalue()
            size_kb = len(image_bytes) / 1024
            logger.info(f"📦 Размер: {size_kb:.1f} KB")
            
            # Сохраняем ответ без показа окна — юзер сам откроет через F + "+"
            self._update_text("⏳ Анализирую скриншот...")
            
            # Отправляем в фоновом потоке
            self.worker = ScreenshotWorker(image_bytes)
            self.worker.finished.connect(self.on_response, Qt.ConnectionType.QueuedConnection)
            self.worker.start()
            
        except Exception as e:
            logger.error(f"❌ Ошибка при создании скриншота: {str(e)}")
            self._show_message(f"❌ Ошибка: {str(e)}")
    
    def on_response(self, result):
        """Обработка ответа от сервера — обновляем текст, но НЕ показываем окно"""
        if result.get("success"):
            answer = result.get("answer", "Нет ответа")
            self._update_text(f"✅ Ответ:\n\n{answer}")
            logger.info("✅ Ответ получен. Нажми F + '+' чтобы посмотреть")
        else:
            error = result.get("error", "Неизвестная ошибка")
            self._update_text(f"❌ Ошибка: {error}")
            logger.warning(f"⚠️ Ошибка: {error}")
    
    def _convert_to_html(self, text):
        """Конвертирует markdown в HTML"""
        import re
        html_text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        html_text = html_text.replace('\n', '<br>')
        return f'<div style="color: #ffffff; font-family: Segoe UI; font-size: 13px;">{html_text}</div>'
    
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
            self.hide_from_capture()
            self.activateWindow()
    
    def _quit_app(self):
        """Закрыть приложение полностью"""
        logger.info("👋 Завершение работы...")
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
        if hasattr(_window_instance, 'listener'):
            _window_instance.listener.stop()
    
    if _app_instance:
        _app_instance.quit()
    
    sys.exit(0)

def main():
    global _app_instance, _window_instance
    
    # Настройка обработчика Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    print("=" * 50)
    print("🔒 Секретный помощник запущен!")
    print("=" * 50)
    print()
    print("📌 Управление:")
    print("   Shift+A   → Сделать скриншот и получить ответ")
    print("   Shift+D   → Показать/скрыть окно с ответом")
    print("   F + '\\' → Закрыть приложение полностью")
    print("   ESC      → Скрыть окно с ответом")
    print("   ПКМ      → Скрыть окно с ответом")
    print("   Ctrl+C   → Закрыть приложение (в терминале)")
    print()
    print("⚠️  Убедитесь, что сервер запущен: python server.py")
    print()
    
    app = QApplication(sys.argv)
    _app_instance = app
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

