# Telegram AI Assistant Bot (Альфа-Инвестиции) - Groq Version

Telegram-бот — ИИ-ассистент для коллег от имени Олега Суслина (PPM проектов Альфа-Инвестиции). Бот отвечает на вопросы по проектам, используя базу знаний из Google Таблицы и модель `llama-3.3-70b-versatile` через **Groq API**.

## Архитектура деплоя на Railway

Поскольку бот должен работать 24/7, он развертывается на **Railway** с использованием Docker. 
Интеграция с Google Таблицей реализована через прямое чтение публичного CSV-экспорта, что избавляет от необходимости настраивать ключи сервисного аккаунта.

---

## Переменные окружения (Environment Variables)

В настройках вашего проекта на Railway (вкладка **Variables**) необходимо добавить следующие переменные:

| Переменная | Описание | Значение по умолчанию / Пример |
| :--- | :--- | :--- |
| `BOT_TOKEN` | Токен Telegram-бота | `8959543595:AAGt6WfZEiesptCFCHhaU_mg2p2pZO_8rws` |
| `GROQ_API_KEY` | Ключ доступа к Groq API | `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` |
| `GROQ_API_BASE` | Базовый URL для Groq API | `https://api.groq.com/openai/v1` |
| `MODEL_NAME` | Модель ИИ | `llama-3.3-70b-versatile` |
| `OWNER_USERNAME` | Username владельца для пересылки ошибок | `@Oleg_Suslin` |
| `SPREADSHEET_ID` | ID Google Таблицы с базой знаний | `1sD8mYWY5j5Eo-nv7S1rOw_tAmdGl2OC7lXgxgTuGRIU` |

---

## Пошаговая инструкция по деплою на Railway

### Шаг 1. Подготовка репозитория
1. Создайте новый репозиторий на GitHub.
2. Загрузите в него файлы: `bot.py`, `Dockerfile`, `requirements.txt`, `railway.toml`.

### Шаг 2. Создание проекта в Railway
1. Нажмите **New Project** -> **Deploy from GitHub repo**.
2. Выберите ваш репозиторий.

### Шаг 3. Настройка переменных окружения
1. Перейдите во вкладку **Variables**.
2. Добавьте все переменные из таблицы выше.

### Шаг 4. Проверка работы
1. В логах Railway вы должны увидеть: `Starting bot polling on Railway (v8 - Groq API + CSV)...`
2. Напишите боту в Telegram для проверки.
