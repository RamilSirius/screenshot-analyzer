# Запуск бэкенда на Claude (VPS)

Бэкенд переведён с Gemini на **Claude API** (`server.py`, модель `claude-opus-5-5`).
Логи и скриншоты сохраняются на диск, сервер держит ~10 одновременных пользователей.

## 1. Что где лежит

```
server.py              ← новый бэкенд на Claude
server_gemini.py       ← старый Gemini (не используется, оставлен на всякий)
server_openai_backup.py← старый OpenAI (бэкап)
.env                   ← сюда вставить ANTHROPIC_API_KEY
data/
  screenshots/ГГГГ-ММ-ДД/ЧЧММСС_<id>.jpg   ← все присланные скриншоты
  logs/server.log                           ← текстовый лог (с ротацией, ~50 МБ истории)
  logs/requests.jsonl                       ← по строке на запрос: токены + стоимость
```

## 2. Установка на сервере

```bash
cd /path/to/system\ app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# вписать реальный ключ Claude
nano .env            # ANTHROPIC_API_KEY=sk-ant-...
```

## 3. Запуск

Разовый тест:
```bash
python server.py
```

Продакшен (один async-процесс спокойно тянет 10 параллельных запросов,
потому что всё время уходит на ожидание ответа Claude — это I/O, не CPU):
```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Автозапуск через systemd (чтобы жил после перезагрузки)

`/etc/systemd/system/analyzer.service`:
```ini
[Unit]
Description=Screenshot Analyzer (Claude)
After=network.target

[Service]
WorkingDirectory=/path/to/system app
ExecStart=/path/to/system app/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now analyzer
sudo journalctl -u analyzer -f     # смотреть живой лог
```

## 4. Проверка

```bash
curl http://СЕРВЕР:8000/health      # {"status":"ok","model":"claude-opus-5-5"}
curl http://СЕРВЕР:8000/stats       # запросы, токены, сколько уже потрачено $
```

## 5. Клиент

В клиенте укажи адрес своего сервера. В `client.py` строка:
```python
SERVER_URL = "https://backend-helper-6nmh.onrender.com"
```
поменяй на `http://ТВОЙ_VPS:8000` (или домен с https через nginx).
`client_fixed.py` берёт адрес из `client.env` → поправь там `SERVER_URL`.

## 6. Деньги ($200 бюджет)

- Модель Opus 5.5: вход $4 / выход $20 за 1 млн токенов.
- Один скриншот теста ≈ 1500–2500 входных + ~300 выходных токенов ≈ **$0.015–0.02**.
- $200 ≈ **10–13 тысяч скриншотов**.
- Текущий расход всегда виден в `/stats` и в конце каждой строки `server.log`.
- Скриншоты перед отправкой ужимаются до 1568px по длинной стороне — это
  экономит токены и не упирается в лимит картинки.

### Хочешь дешевле
В `.env` поменяй модель и цены для корректного подсчёта:
```
CLAUDE_MODEL=claude-sonnet-5-5
PRICE_IN_PER_MTOK=2.0
PRICE_OUT_PER_MTOK=10.0
```
Sonnet 5.5 примерно вдвое дешевле (~25 тыс. скриншотов на $200), качество на тестах близкое.

## 7. Полезные ручки в .env

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5-5` | какая модель |
| `CLAUDE_EFFORT` | `medium` | глубина рассуждений: low/medium/high/xhigh/max |
| `MAX_CONCURRENCY` | `12` | сколько запросов к Claude одновременно |
| `SAVE_SCREENSHOTS` | `1` | `0` — не сохранять скриншоты |
| `DATA_DIR` | `./data` | куда писать логи и скриншоты |
