"""
macOS клиент для анализа скриншотов.

Горячие клавиши (глобальные):
    Shift+A  — скриншот всего экрана и отправка на сервер
    Shift+D  — показать/скрыть окно с ответом
    Shift+Q  — выход

Ключевое отличие от старой версии:
    хоткеи регистрируются через Carbon RegisterEventHotKey — это системный
    API именно для горячих клавиш, который НЕ требует разрешения
    Accessibility / Input Monitoring (в отличие от pynput, который слушает
    весь ввод и поэтому требует этих разрешений).

    Единственное разрешение, которое всё же нужно, — Screen Recording
    (без него macOS не даёт снять экран). Система запросит его сама при
    первом скриншоте; после выдачи приложение надо один раз перезапустить.

Запросы к серверу идентичны серверному контракту: POST /analyze, поле "file".
Адрес сервера берётся из client.env (SERVER_URL=...), по умолчанию localhost:8000.
"""
import sys
import os
import io
import ctypes
import logging
from ctypes import (
    Structure, CFUNCTYPE, POINTER, byref, sizeof,
    c_void_p, c_uint32, c_int32, c_ulong,
)

import requests
from PIL import ImageGrab
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget, QScrollArea, QTextEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
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
#    правим только его, .app пересобирать не надо)
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
logger = logging.getLogger("client_mac")

# id хоткеев
HK_CAPTURE = 1
HK_TOGGLE = 2
HK_QUIT = 3


# --------------------------------------------------------------------------
# Глобальные хоткеи через Carbon (без Accessibility)
# --------------------------------------------------------------------------
_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")


def _fourcc(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


kEventClassKeyboard = _fourcc("keyb")
kEventHotKeyPressed = 6
kEventParamDirectObject = _fourcc("----")
typeEventHotKeyID = _fourcc("hkid")

# модификаторы Carbon
cmdKey, shiftKey, optionKey, controlKey = 0x100, 0x200, 0x800, 0x1000
# виртуальные коды клавиш (ANSI)
VK_A, VK_D, VK_Q = 0x00, 0x02, 0x0C


class _EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


class _EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


_HandlerProc = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)

_carbon.GetApplicationEventTarget.restype = c_void_p
_carbon.InstallEventHandler.argtypes = [
    c_void_p, _HandlerProc, c_ulong, POINTER(_EventTypeSpec), c_void_p, c_void_p,
]
_carbon.InstallEventHandler.restype = c_int32
_carbon.RegisterEventHotKey.argtypes = [
    c_uint32, c_uint32, _EventHotKeyID, c_void_p, c_uint32, POINTER(c_void_p),
]
_carbon.RegisterEventHotKey.restype = c_int32
_carbon.GetEventParameter.argtypes = [
    c_void_p, c_uint32, c_uint32, c_void_p, c_ulong, c_void_p, c_void_p,
]
_carbon.GetEventParameter.restype = c_int32


class GlobalHotKeys:
    """Регистрирует системные горячие клавиши. Не требует Accessibility."""

    def __init__(self, on_hotkey):
        self._on_hotkey = on_hotkey
        self._refs = []
        # держим ссылку на callback, иначе его соберёт GC и будет краш
        self._handler = _HandlerProc(self._callback)
        target = _carbon.GetApplicationEventTarget()
        spec = _EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        _carbon.InstallEventHandler(target, self._handler, 1, byref(spec), None, None)

    def register(self, hotkey_id: int, keycode: int, modifiers: int) -> bool:
        hk = _EventHotKeyID(_fourcc("htk1"), hotkey_id)
        ref = c_void_p()
        status = _carbon.RegisterEventHotKey(
            keycode, modifiers, hk, _carbon.GetApplicationEventTarget(), 0, byref(ref)
        )
        if status == 0:
            self._refs.append(ref)
            return True
        logger.error(f"❌ RegisterEventHotKey id={hotkey_id} вернул статус {status}")
        return False

    def _callback(self, next_handler, event, user_data):
        hk = _EventHotKeyID()
        _carbon.GetEventParameter(
            event, kEventParamDirectObject, typeEventHotKeyID,
            None, sizeof(hk), None, byref(hk),
        )
        try:
            self._on_hotkey(hk.id)
        except Exception as e:
            logger.error(f"❌ Ошибка обработки хоткея: {e}")
        return 0


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

        # регистрируем хоткеи
        self.hotkeys = GlobalHotKeys(self._on_hotkey)
        mods = shiftKey
        ok = all([
            self.hotkeys.register(HK_CAPTURE, VK_A, mods),
            self.hotkeys.register(HK_TOGGLE, VK_D, mods),
            self.hotkeys.register(HK_QUIT, VK_Q, mods),
        ])
        if ok:
            logger.info("✅ Хоткеи зарегистрированы: Shift+A / Shift+D / Shift+Q")
        else:
            logger.error("❌ Не все хоткеи удалось зарегистрировать")

        QTimer.singleShot(200, self._hide_from_capture)

    # ---- UI ----
    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
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
        f.setFamilies(["San Francisco", "Helvetica", "Arial"])
        f.setPointSize(13)
        self.text.setFont(f)
        scroll.setWidget(self.text)
        layout.addWidget(scroll)
        self.setCentralWidget(central)

        screen = QApplication.primaryScreen().geometry()
        self.resize(420, 280)
        self.move(screen.width() - 440, screen.height() - 320)

    def _hide_from_capture(self, bring_front: bool = False):
        """Скрыть от записи экрана + показывать поверх полноэкранных приложений."""
        try:
            import objc
            from AppKit import (
                NSScreenSaverWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
            )
            ns_win = objc.objc_object(c_void_p=int(self.winId())).window()
            if not ns_win:
                return
            # не попадает в скриншоты и запись экрана
            ns_win.setSharingType_(0)
            # выше всего, включая нативный fullscreen других приложений
            ns_win.setLevel_(NSScreenSaverWindowLevel)
            # присутствовать во всех Spaces и поверх fullscreen-окон
            ns_win.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
            )
            if bring_front:
                # вывести поверх текущего Space (в т.ч. чужого fullscreen), не активируя приложение
                ns_win.orderFrontRegardless()
                logger.info(
                    f"overlay: level={int(ns_win.level())} "
                    f"behavior={int(ns_win.collectionBehavior())}"
                )
        except Exception as e:
            logger.warning(f"⚠️ hide_from_capture: {e}")

    # ---- хоткеи (вызывается из Carbon-callback на главном потоке) ----
    def _on_hotkey(self, hk_id: int):
        if hk_id == HK_CAPTURE:
            logger.info("🎯 Shift+A")
            self.sig_capture.emit()
        elif hk_id == HK_TOGGLE:
            logger.info("🎯 Shift+D")
            self.sig_toggle.emit()
        elif hk_id == HK_QUIT:
            logger.info("🎯 Shift+Q")
            self.sig_quit.emit()

    # ---- действия ----
    def _capture(self):
        if self.worker and self.worker.isRunning():
            logger.warning("⚠️ Предыдущий запрос ещё идёт")
            return
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
            QApplication.processEvents()
        QTimer.singleShot(120, lambda: self._do_capture(was_visible))

    def _do_capture(self, was_visible):
        try:
            shot = ImageGrab.grab()
            logger.info(f"📸 Скриншот {shot.size[0]}x{shot.size[1]}")
            buf = io.BytesIO()
            shot.save(buf, format="PNG")
            data = buf.getvalue()
            if not data or shot.getbbox() is None:
                self.sig_text.emit(
                    "❌ Пустой скриншот. Разреши Screen Recording для этого "
                    "приложения в System Settings → Privacy & Security и перезапусти.",
                    True,
                )
                return
            self._set_text("⏳ Анализирую...", False)
            self.worker = ScreenshotWorker(data)
            self.worker.finished.connect(self._on_response, Qt.ConnectionType.QueuedConnection)
            self.worker.start()
        except Exception as e:
            logger.error(f"❌ Скриншот не удался: {e}")
            self.sig_text.emit(
                f"❌ Не удалось снять экран: {e}\n\nПроверь разрешение Screen Recording.",
                True,
            )

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
            self._show_overlay()

    def _show_overlay(self):
        self.show()
        self.raise_()
        self._hide_from_capture(bring_front=True)
        # переприменяем после того, как Qt закончит свою настройку окна
        QTimer.singleShot(100, lambda: self._hide_from_capture(bring_front=True))

    def _toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self._show_overlay()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Accessory-режим: без иконки в доке/меню-бара, как фоновый агент.
    # Нужно, чтобы оверлей показывался поверх чужого полноэкранного приложения.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except Exception as e:
        logger.warning(f"⚠️ setActivationPolicy: {e}")

    print("=" * 52)
    print("🔒 Помощник (macOS) запущен")
    print(f"🌐 Сервер: {SERVER_URL}")
    print("   Shift+A — скриншот и анализ")
    print("   Shift+D — показать/скрыть ответ")
    print("   Shift+Q — выход")
    print("=" * 52)
    logger.info("🚀 Жду хоткеи...")

    win = Overlay()  # noqa: F841  (держим ссылку живой)
    app._win = win
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
