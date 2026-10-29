"""
Backend сервер для анализа скриншотов через Claude API (Anthropic).

Что делает:
  * принимает скриншот на POST /analyze
  * анализирует его через Claude Vision (модель задаётся в .env)
  * СОХРАНЯЕТ каждый скриншот на диск (data/screenshots/ГГГГ-ММ-ДД/)
  * ПИШЕТ логи и в консоль, и в файл с ротацией (data/logs/server.log)
  * ведёт построчный JSONL-лог каждого запроса (data/logs/requests.jsonl)
    с токенами и оценкой стоимости — чтобы видеть, как тратится бюджет
  * рассчитан на ~10 одновременных пользователей (async + семафор)

Запуск:
    pip install -r requirements.txt
    # в .env должен лежать ANTHROPIC_API_KEY=sk-ant-...
    python server.py
    # или для продакшена:
    # uvicorn server:app --host 0.0.0.0 --port 8000
"""
import os
import io
import json
import time
import base64
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from PIL import Image
import anthropic
import uvicorn

load_dotenv()

# ---------------------------------------------------------------------------
# Конфигурация (всё можно переопределить через переменные окружения / .env)
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_NAME = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "medium")          # low | medium | high | xhigh | max
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "2000"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "12"))   # сколько запросов к Claude одновременно
MAX_IMAGE_EDGE = int(os.getenv("MAX_IMAGE_EDGE", "1568"))   # ресайз длинной стороны перед отправкой (экономия токенов)
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "15"))     # отсекаем слишком большие загрузки

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
LOG_DIR = os.path.join(DATA_DIR, "logs")
SHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
SAVE_SCREENSHOTS = os.getenv("SAVE_SCREENSHOTS", "1") != "0"

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SHOTS_DIR, exist_ok=True)

# Цены Claude, $/млн токенов (для оценки стоимости в логах).
# По умолчанию — тарифы Opus 5.5. Можно переопределить под другую модель.
PRICE_IN = float(os.getenv("PRICE_IN_PER_MTOK", "4.0"))
PRICE_OUT = float(os.getenv("PRICE_OUT_PER_MTOK", "20.0"))

# ---------------------------------------------------------------------------
# Логирование: консоль + файл с ротацией
# ---------------------------------------------------------------------------
logger = logging.getLogger("screenshot_analyzer")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_console = logging.StreamHandler()
_console.setFormatter(_fmt)
logger.addHandler(_console)

# server.log — до 5 МБ × 10 файлов = ~50 МБ истории
_file = RotatingFileHandler(
    os.path.join(LOG_DIR, "server.log"), maxBytes=5 * 1024 * 1024,
    backupCount=10, encoding="utf-8",
)
_file.setFormatter(_fmt)
logger.addHandler(_file)

# JSONL-лог запросов (одна строка на запрос) — удобно парсить/считать расходы
_requests_log_path = os.path.join(LOG_DIR, "requests.jsonl")
_requests_log_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Клиент Anthropic + ограничитель параллелизма
# ---------------------------------------------------------------------------
if not ANTHROPIC_API_KEY:
    logger.warning("⚠️ ANTHROPIC_API_KEY не задан в .env — запросы к Claude будут падать!")

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=2, timeout=90.0)
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

# Счётчики за время жизни процесса (видно в /health и /stats)
_stats = {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

PROMPT = (
    "Ты помощник, который анализирует скриншоты экрана с тестами или вопросами.\n"
    "Твоя задача — определить правильные ответы на вопросы.\n\n"
    "ВАЖНО: Отвечай СТРОГО в таком формате:\n"
    "1) b - текст ответа\n"
    "2) a - текст ответа\n"
    "3) c - текст ответа\n\n"
    "Где:\n"
    "- Первая цифра — номер вопроса\n"
    "- Буква (a, b, c, d) — правильный вариант ответа\n"
    "- После дефиса — краткое объяснение или текст ответа\n\n"
    "Если на скриншоте несколько вопросов, перечисли все ответы в таком формате.\n"
    "Если это не тест, а просто вопрос — ответь кратко и по делу.\n\n"
    "Проанализируй скриншот и дай ответы в указанном формате:"
)

ALLOWED_MEDIA = {"image/png", "image/jpeg", "image/gif", "image/webp"}

app = FastAPI(title="Screenshot Analyzer - Claude")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _save_screenshot(image_bytes: bytes, req_id: str, ext: str) -> str:
    """Сохраняем оригинал скриншота на диск. Возвращаем путь (или '')"""
    if not SAVE_SCREENSHOTS:
        return ""
    day = datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(SHOTS_DIR, day)
    os.makedirs(day_dir, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    path = os.path.join(day_dir, f"{ts}_{req_id}.{ext}")
    try:
        with open(path, "wb") as f:
            f.write(image_bytes)
        return path
    except Exception as e:
        logger.error(f"[{req_id}] ❌ Не смог сохранить скриншот: {e}")
        return ""


def _prepare_for_api(image_bytes: bytes, content_type: str):
    """
    Готовим картинку для Claude: при необходимости ужимаем длинную сторону
    до MAX_IMAGE_EDGE (экономия токенов и денег, плюс не упираемся в лимит 5МБ).
    Возвращаем (base64_str, media_type).
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        longest = max(w, h)
        if longest > MAX_IMAGE_EDGE:
            scale = MAX_IMAGE_EDGE / longest
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
        return base64.standard_b64encode(data).decode("utf-8"), "image/jpeg"
    except Exception as e:
        # Если Pillow не справился — отправляем как есть
        logger.warning(f"⚠️ Не удалось пережать картинку ({e}), отправляю оригинал")
        media = content_type if content_type in ALLOWED_MEDIA else "image/png"
        return base64.standard_b64encode(image_bytes).decode("utf-8"), media


async def _log_request(record: dict):
    """Дописываем одну JSON-строку в requests.jsonl (под замком)"""
    try:
        line = json.dumps(record, ensure_ascii=False)
        async with _requests_log_lock:
            with open(_requests_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        logger.error(f"❌ Не смог записать requests.jsonl: {e}")


async def call_claude(b64: str, media_type: str):
    """Запрос к Claude Vision. Семафор ограничивает число одновременных вызовов."""
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {"type": "text", "text": PROMPT},
            ],
        }
    ]
    async with _semaphore:
        try:
            return await client.messages.create(
                model=MODEL_NAME,
                max_tokens=MAX_TOKENS,
                output_config={"effort": EFFORT},
                messages=messages,
            )
        except TypeError:
            # старый SDK без output_config/effort — шлём без него
            logger.warning("⚠️ SDK не поддерживает output_config — обнови: pip install -U anthropic")
            return await client.messages.create(
                model=MODEL_NAME,
                max_tokens=MAX_TOKENS,
                messages=messages,
            )


@app.post("/analyze")
async def analyze_screenshot(file: UploadFile = File(...)):
    req_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        image_data = await file.read()
        if not image_data:
            raise HTTPException(status_code=400, detail="Пустой файл")

        size_kb = len(image_data) / 1024
        if size_kb / 1024 > MAX_UPLOAD_MB:
            raise HTTPException(status_code=413, detail=f"Файл больше {MAX_UPLOAD_MB} МБ")

        content_type = (file.content_type or "image/png").lower()
        logger.info(f"[{req_id}] 📥 Скриншот {file.filename} ({size_kb:.1f} KB, {content_type})")

        # 1) сохраняем оригинал на диск
        ext = "png"
        if "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "webp" in content_type:
            ext = "webp"
        saved_path = _save_screenshot(image_data, req_id, ext)
        if saved_path:
            logger.info(f"[{req_id}] 💾 Сохранён: {saved_path}")

        # 2) готовим (ужимаем) и шлём в Claude
        b64, media_type = _prepare_for_api(image_data, content_type)
        logger.info(f"[{req_id}] 🔄 → Claude {MODEL_NAME} (effort={EFFORT})")
        msg = await call_claude(b64, media_type)

        # 3) собираем текст ответа
        answer = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip() or "Пустой ответ от модели."

        # 4) токены / стоимость
        usage = msg.usage
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = (in_tok * PRICE_IN + out_tok * PRICE_OUT) / 1_000_000

        _stats["requests"] += 1
        _stats["input_tokens"] += in_tok
        _stats["output_tokens"] += out_tok
        _stats["cost_usd"] += cost

        elapsed = time.monotonic() - t0
        logger.info(
            f"[{req_id}] ✅ {elapsed:.1f}s | in={in_tok} out={out_tok} "
            f"| ~${cost:.4f} | всего ~${_stats['cost_usd']:.2f}"
        )
        preview = answer[:120] + ("..." if len(answer) > 120 else "")
        logger.info(f"[{req_id}] 📝 {preview}")

        await _log_request({
            "req_id": req_id, "ts": started_at, "model": MODEL_NAME,
            "filename": file.filename, "size_kb": round(size_kb, 1),
            "screenshot": saved_path, "elapsed_s": round(elapsed, 2),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "cache_read_tokens": cache_read, "cost_usd": round(cost, 6),
            "stop_reason": msg.stop_reason, "ok": True, "answer": answer,
        })

        return {"success": True, "answer": answer}

    except HTTPException as he:
        _stats["errors"] += 1
        logger.warning(f"[{req_id}] ⚠️ {he.detail}")
        await _log_request({
            "req_id": req_id, "ts": started_at, "ok": False,
            "error": str(he.detail), "elapsed_s": round(time.monotonic() - t0, 2),
        })
        raise

    except anthropic.APIStatusError as e:
        _stats["errors"] += 1
        logger.error(f"[{req_id}] ❌ Claude API {e.status_code}: {e.message}")
        await _log_request({
            "req_id": req_id, "ts": started_at, "ok": False,
            "error": f"claude_{e.status_code}: {e.message}",
            "elapsed_s": round(time.monotonic() - t0, 2),
        })
        return {"success": False, "error": f"Ошибка Claude API ({e.status_code})"}

    except Exception as e:
        _stats["errors"] += 1
        logger.error(f"[{req_id}] ❌ {e}")
        await _log_request({
            "req_id": req_id, "ts": started_at, "ok": False,
            "error": str(e), "elapsed_s": round(time.monotonic() - t0, 2),
        })
        return {"success": False, "error": str(e)}


@app.get("/health")
async def health_check():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/stats")
async def stats():
    """Текущая статистика процесса: запросы, токены, примерная стоимость."""
    return {
        **_stats,
        "cost_usd": round(_stats["cost_usd"], 4),
        "model": MODEL_NAME,
        "max_concurrency": MAX_CONCURRENCY,
    }


if __name__ == "__main__":
    print("=" * 56)
    print(f"🚀 Сервер (Claude) на http://0.0.0.0:8000")
    print(f"🤖 Модель: {MODEL_NAME} | effort={EFFORT}")
    print(f"💾 Скриншоты: {SHOTS_DIR}" if SAVE_SCREENSHOTS else "💾 Сохранение скриншотов ВЫКЛ")
    print(f"📄 Логи: {LOG_DIR}")
    print(f"👥 Параллельно до {MAX_CONCURRENCY} запросов к Claude")
    print("=" * 56)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
