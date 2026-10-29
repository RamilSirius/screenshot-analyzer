"""
Backend сервер для обработки скриншотов через OpenAI Vision API
"""
import os
import base64
import logging
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from dotenv import load_dotenv
import uvicorn

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Screenshot Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenAI клиент
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.post("/analyze")
async def analyze_screenshot(file: UploadFile = File(...)):
    """
    Принимает скриншот и анализирует его через GPT-4 Vision
    """
    try:
        # Читаем изображение
        image_data = await file.read()
        image_size_kb = len(image_data) / 1024
        logger.info(f"📥 Получен скриншот: {file.filename} ({image_size_kb:.1f} KB)")
        
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        # Определяем MIME тип
        content_type = file.content_type or "image/png"
        
        # Отправляем в OpenAI Vision
        logger.info("🔄 Отправляю в OpenAI GPT-4o Vision...")
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """Ты помощник, который анализирует скриншоты экрана.
                    Твоя задача - понять контекст (тест, задача, вопрос) и дать краткий полезный ответ.
                    Отвечай кратко и по делу. Если это вопрос - дай ответ.
                    Если это задача - помоги решить. Отвечай на русском."""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Проанализируй этот скриншот и помоги с тем, что на нём показано:"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{content_type};base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000
        )
        
        answer = response.choices[0].message.content
        tokens_used = response.usage.total_tokens if response.usage else "N/A"
        
        logger.info(f"✅ Ответ получен! Токенов использовано: {tokens_used}")
        logger.info(f"📝 Ответ: {answer[:100]}..." if len(answer) > 100 else f"📝 Ответ: {answer}")
        
        return {"success": True, "answer": answer}
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {str(e)}")
        return {"success": False, "error": str(e)}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Сервер запускается на http://localhost:8000")
    print("📡 Endpoint для анализа: POST /analyze")
    print("=" * 50)
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

