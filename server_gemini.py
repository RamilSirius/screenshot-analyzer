import os
import logging
import base64

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import uvicorn

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Screenshot Analyzer - Gemini (HTTP)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

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


async def call_gemini_with_image(image_bytes: bytes, content_type: str) -> str:
    """
    Аналог sendImageToGeminiHttp из JS:
    – картинка как base64 inlineData
    – текстовый prompt во второй части contents
    Теперь полностью async — не блокирует event loop.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан в .env")

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": content_type,
                            "data": b64,
                        }
                    },
                    {"text": PROMPT},
                ]
            }
        ]
    }

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }

    logger.info(f"🔄 HTTP → Gemini {MODEL_NAME}")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(GEMINI_URL, headers=headers, json=payload)

    if resp.status_code == 429:
        raise RuntimeError(
            f"429 QUOTA / RATE LIMIT: {resp.text}"
        )

    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP error {resp.status_code}: {resp.text}")

    data = resp.json()
    # Структура ответа: candidates -> content -> parts[] -> text
    candidates = data.get("candidates") or []
    if not candidates:
        return "Модель не вернула ответ."

    parts = candidates[0].get("content", {}).get("parts", [])
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    answer = "\n".join(t for t in texts if t.strip())
    return answer or "Пустой ответ от модели."


@app.post("/analyze")
async def analyze_screenshot(file: UploadFile = File(...)):
    try:
        image_data = await file.read()
        if not image_data:
            raise HTTPException(status_code=400, detail="Empty file")

        content_type = file.content_type or "image/png"
        logger.info(
            f"📥 Получен файл: {file.filename} ({len(image_data)/1024:.1f} KB) {content_type}"
        )

        answer = await call_gemini_with_image(image_data, content_type)
        logger.info("✅ Ответ получен")
        logger.info(
            f"📝 Ответ: {answer[:120]}..." if len(answer) > 120 else f"📝 Ответ: {answer}"
        )
        return {"success": True, "answer": answer}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        # Возвращаем текст ошибки клиенту (как у тебя в JS)
        return {"success": False, "error": str(e)}


@app.get("/health")
async def health_check():
    return {"status": "ok", "model": MODEL_NAME}

