"""
Windows клиент для анализа скриншотов.

Горячие клавиши (глобальные):
    Shift+A  — скриншот всего экрана и отправка на сервер
    Shift+D  — показать/скрыть окно с ответом
    Shift+Q  — выход

На Windows глобальные хоткеи работают без каких-либо разрешений.
Окно оверлея скрывается от записи экрана через SetWindowDisplayAffinity
(WDA_EXCLUDEFROMCAPTURE) — его не видно в Zoom/демонстрации/записи.

Запросы к серверу идентичны серверному контракту: POST /analyze, поле "file".
Адрес берётся из client.env (SERVER_URL=...), по умолчанию localhost:8000.
"""
import sys
import os
import io
import ctypes
import logging

import requests
from PIL import ImageGrab
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget, QScrollArea, QTextEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from pynput import keyboard
from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------
# 1) значения по умолчанию из вшитого/локального client.env
if getattr(sys, "frozen", False):
    load_dotenv(os.path.join(sys._MEIPASS, "client.env"))
else:
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "client.env"))

# 2) внешний файл ~/.systemapp.env переопределяет (адрес туннеля меняется —
#    правим только его, .exe пересобирать не надо)
_EXTERNAL_ENV = os.path.expanduser("~/.systemapp.env")
if os.path.exists(_EXTERNAL_ENV):
    load_dotenv(_EXTERNAL_ENV, override=True)

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000").rstrip("/")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("client_win")


# --------------------------------------------------------------------------
# Сетевой воркер
# --------------------------------------------------------------------------
class ScreenshotWorker(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, image_bytes: bytes):
        super().__init__()
        self.image_bytes = image_bytes

    def run(self):
        try:
            logger.info(f"📤 Отправляю на {SERVER_URL}/analyze ...")
            files = {"file": ("screenshot.png", self.image_bytes, "image/png")}
            headers = {"X-Auth-Token": AUTH_TOKEN} if AUTH_TOKEN else {}
            resp = requests.post(f"{SERVER_URL}/analyze", files=files, headers=headers, timeout=90)
            resp.raise_for_status()
            self.finished.emit(resp.json())
        except requests.exceptions.ConnectionError:
            self.finished.emit({"success": False, "error": f"Сервер недоступен ({SERVER_URL})"})
        except requests.exceptions.Timeout:
            self.finished.emit({"success": False, "error": "Таймаут (90с)"})
        except Exception as e:
            self.finished.emit({"success": False, "error": str(e)})


# --------------------------------------------------------------------------
# Окно-оверлей (скрыто от записи экрана)
# --------------------------------------------------------------------------
class Overlay(QMainWindow):
    sig_capture = pyqtSignal()
    sig_toggle = pyqtSignal()
    sig_quit = pyqtSignal()
    sig_text = pyqtSignal(str, bool)  # (text, show_window)

    def __init__(self):
        super().__init__()
        self.worker = None
        self._build_ui()

        self.sig_capture.connect(self._capture, Qt.ConnectionType.QueuedConnection)
        self.sig_toggle.connect(self._toggle, Qt.ConnectionType.QueuedConnection)
        self.sig_quit.connect(QApplication.quit, Qt.ConnectionType.QueuedConnection)
        self.sig_text.connect(self._set_text, Qt.ConnectionType.QueuedConnection)

        # глобальные хоткеи (отдельный поток pynput)
        self.listener = keyboard.GlobalHotKeys({
            "<shift>+a": lambda: self.sig_capture.emit(),
            "<shift>+d": lambda: self.sig_toggle.emit(),
            "<shift>+q": lambda: self.sig_quit.emit(),
        })
        self.listener.daemon = True
        self.listener.start()
        logger.info("✅ Хоткеи зарегистрированы: Shift+A / Shift+D / Shift+Q")

        QTimer.singleShot(200, self._hide_from_capture)

    # ---- UI ----
    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        central = QWidget()
        central.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{background:rgba(10,10,15,150);border:none;border-radius:12px;}"
            "QScrollBar:vertical{background:transparent;width:6px;}"
            "QScrollBar::handle:vertical{background:rgba(150,150,150,120);border-radius:3px;}"
        )
        self.text = QTextEdit("Готов. Shift+A — скриншот.")
        self.text.setReadOnly(True)
        self.text.setStyleSheet(
            "QTextEdit{background:transparent;color:#fff;padding:18px;"
            "font-size:14px;border:none;}"
        )
        f = QFont()
        f.setFamilies(["Segoe UI", "Arial"])
        f.setPointSize(11)
        self.text.setFont(f)
        scroll.setWidget(self.text)
        layout.addWidget(scroll)
        self.setCentralWidget(central)

        screen = QApplication.primaryScreen().geometry()
        self.resize(420, 280)
        self.move(screen.width() - 440, screen.height() - 320)

    def _hide_from_capture(self):
        """WDA_EXCLUDEFROMCAPTURE — окно не попадает в скриншоты и запись экрана."""
        try:
            hwnd = int(self.winId())
            WDA_EXCLUDEFROMCAPTURE = 0x11
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if not ok:
                logger.warning("⚠️ SetWindowDisplayAffinity вернул 0")
        except Exception as e:
            logger.warning(f"⚠️ hide_from_capture: {e}")

    # ---- действия ----
    def _capture(self):
        if self.worker and self.worker.isRunning():
            logger.warning("⚠️ Предыдущий запрос ещё идёт")
            return
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
            QApplication.processEvents()
        QTimer.singleShot(120, lambda: self._do_capture())

    def _do_capture(self):
        try:
            shot = ImageGrab.grab()
            logger.info(f"📸 Скриншот {shot.size[0]}x{shot.size[1]}")
            buf = io.BytesIO()
            shot.save(buf, format="PNG")
            data = buf.getvalue()
            self._set_text("⏳ Анализирую...", False)
            self.worker = ScreenshotWorker(data)
            self.worker.finished.connect(self._on_response, Qt.ConnectionType.QueuedConnection)
            self.worker.start()
        except Exception as e:
            logger.error(f"❌ Скриншот не удался: {e}")
            self.sig_text.emit(f"❌ Не удалось снять экран: {e}", True)

    def _on_response(self, result: dict):
        # Окно само НЕ открывается — ответ готовится молча, смотреть по Shift+D.
        if result.get("success"):
            self.sig_text.emit(f"✅ Ответ:\n\n{result.get('answer', '—')}", False)
            logger.info("✅ Ответ получен (Shift+D — показать)")
        else:
            self.sig_text.emit(f"❌ Ошибка: {result.get('error', '—')}", False)
            logger.warning(f"⚠️ {result.get('error')}")

    # ---- отрисовка текста ----
    def _set_text(self, text: str, show_window: bool):
        import re
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text).replace("\n", "<br>")
        self.text.setHtml(f'<div style="color:#fff;font-size:13px;">{html}</div>')
        if show_window:
            self.show()
            self.raise_()
            self._hide_from_capture()

    def _toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self._hide_from_capture()

    def closeEvent(self, event):
        try:
            self.listener.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    print("=" * 52)
    print("🔒 Помощник (Windows) запущен")
    print(f"🌐 Сервер: {SERVER_URL}")
    print("   Shift+A — скриншот и анализ")
    print("   Shift+D — показать/скрыть ответ")
    print("   Shift+Q — выход")
    print("=" * 52)
    logger.info("🚀 Жду хоткеи...")

    win = Overlay()
    app._win = win
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
