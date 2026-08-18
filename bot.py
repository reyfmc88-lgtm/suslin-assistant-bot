import asyncio
import csv
import io
import json
import logging
import os
import re
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Railway environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_BASE = os.getenv("GROQ_API_BASE", "https://api.groq.com/openai/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "@Oleg_Suslin")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1sD8mYWY5j5Eo-nv7S1rOw_tAmdGl2OC7lXgxgTuGRIU")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# The service account must be granted Editor access to the spreadsheet.
GOOGLE_SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
INCOMING_SHEET_NAME = "Входящие"
TASK_TRIGGERS = (
    "создай задачу",
    "запиши таску",
    "запиши задачу",
    "есть работа",
    "добавь в бэклог",
)
PRIORITIES = {"высокий": "Высокий", "средний": "Средний", "низкий": "Низкий"}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is required")

# Groq is compatible with the OpenAI Python SDK.
llm_client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_API_BASE)
owner_chat_id: int | None = None

BOT_PERSONA_BASE = (
    "Ты — ИИ-ассистент Олега Суслина, PPM проектов Альфа-Инвестиции. "
    "Твоя задача — отвечать на вопросы коллег и клиентов от его имени. "
    "Отвечай вежливо, профессионально и на русском языке. "
    "Используй данные из базы знаний ниже как основной источник информации для ответов. "
    "Если вопрос совсем не касается работы или Альфа-Инвестиций, мягко верни разговор к теме. "
    "СТРОГО ЗАПРЕЩЕНО использовать Markdown-разметку в ответах. "
    "НИКОГДА не используй таблицы с символом |, звёздочки **, решётки #, обратные кавычки `. "
    "Пиши ТОЛЬКО простым текстом. Для списков используй тире (—) или нумерацию (1. 2. 3.). "
    "Для выделения используй ЗАГЛАВНЫЕ БУКВЫ, а не звёздочки."
)


def clean_markdown(text: str) -> str:
    """Remove Markdown formatting from LLM response for clean Telegram display."""
    # Remove markdown table separators like |---|---|---|
    text = re.sub(r'\|[-:]+\|[-:| ]+\|', '', text)
    # Remove table row pipes at start/end of lines
    text = re.sub(r'^\s*\|\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*\|\s*$', '', text, flags=re.MULTILINE)
    # Replace remaining pipes used as column separators
    text = re.sub(r'\s*\|\s*', ' — ', text)
    # Remove bold/italic markers (including multiline)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_', r'\1', text, flags=re.DOTALL)
    # Catch any remaining standalone ** or *
    text = text.replace('**', '')
    text = text.replace('__', '')
    # Remove headers
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Clean up multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_sheet_csv(sheet_name: str) -> list[list[str]]:
    """Read a publicly viewable tab from Google Sheets through CSV export."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={requests.utils.quote(sheet_name)}"
    )
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return list(csv.reader(io.StringIO(response.text)))
    except Exception as exc:
        logger.error("Could not read sheet %s: %s", sheet_name, exc)
        return []


def format_rows_as_text(rows: list[list[str]], sheet_label: str) -> str:
    """Convert the rows of a knowledge-base tab to concise LLM context."""
    if not rows:
        return f"[{sheet_label}: данные отсутствуют]\n"

    headers = rows[0]
    lines = [f"=== {sheet_label} ==="]
    for row in rows[1:]:
        parts = [
            f"{headers[index] if index < len(headers) else f'Столбец {index + 1}'}: {value}"
            for index, value in enumerate(row)
            if value.strip()
        ]
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines) + "\n"


def build_knowledge_context() -> str:
    """Fetch current Projects, Backlog and FAQ data for every answer."""
    logger.info("Reading knowledge base through public CSV export")
    return "\n".join(
        [
            "БАЗА ЗНАНИЙ ПО ПРОЕКТАМ АЛЬФА-ИНВЕСТИЦИИ (актуальные данные из Google Таблицы):\n",
            format_rows_as_text(fetch_sheet_csv("Проекты"), "Проекты"),
            format_rows_as_text(fetch_sheet_csv("Бэклог"), "Бэклог"),
            format_rows_as_text(fetch_sheet_csv("FAQ"), "FAQ"),
        ]
    )


@lru_cache(maxsize=1)
def get_sheets_service():
    """Build an authenticated Google Sheets client from a Railway secret."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not configured. "
            "Set it in Railway and give the service-account email Editor access to the spreadsheet."
        )

    try:
        service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON contains invalid JSON") from exc

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=GOOGLE_SHEETS_SCOPES,
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def extract_task(message: str) -> tuple[str, str] | None:
    """Return (description, priority) when a message starts with a configured task trigger."""
    normalized_message = message.casefold().strip()
    matched_trigger = next(
        (trigger for trigger in TASK_TRIGGERS if trigger in normalized_message),
        None,
    )
    if not matched_trigger:
        return None

    trigger_position = normalized_message.find(matched_trigger)
    description = message[trigger_position + len(matched_trigger):].strip(" \t\n:,-\u2014\u2013")
    if not description:
        return "", "Средний"

    priority_match = re.search(
        r"(?:приоритет\s*[:=-]?\s*)(высокий|средний|низкий)\b",
        description,
        flags=re.IGNORECASE,
    )
    priority = PRIORITIES.get(priority_match.group(1).casefold(), "Средний") if priority_match else "Средний"
    if priority_match:
        description = (description[:priority_match.start()] + description[priority_match.end():]).strip(" \t\n:,-\u2014\u2013")

    return description, priority


def append_incoming_task(description: str, author: str, priority: str) -> None:
    """Append a new incoming task to Google Sheets using service-account credentials."""
    current_date = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M")
    body = {
        "values": [[description, author, current_date, priority, "Новая"]]
    }

    get_sheets_service().spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{INCOMING_SHEET_NAME}!A:E",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message."""
    user = update.effective_user
    await update.message.reply_html(
        f"Привет, {user.mention_html()}!\n\n"
        "Я — ИИ-ассистент Олега Суслина, PPM проектов Альфа-Инвестиции. "
        "Отвечаю на вопросы по проектам и записываю задачи в общий входящий бэклог.\n\n"
        "Чтобы создать задачу, напишите, например: «Создай задачу: подготовить статус по ИИ-агенту».")


async def register_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register the owner's chat ID for error notifications."""
    global owner_chat_id
    if update.effective_user.username == OWNER_USERNAME.lstrip("@"):
        owner_chat_id = update.effective_chat.id
        await update.message.reply_text("Вы успешно зарегистрированы как владелец бота.")
        logger.info("Owner chat_id registered: %s", owner_chat_id)
    else:
        await update.message.reply_text("Только Олег Суслин может зарегистрироваться как владелец бота.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record task requests or answer normal questions through Groq."""
    message = update.message.text
    user = update.effective_user
    username = user.username or user.full_name or str(user.id)
    logger.info("Message from %s: %s", username, message)

    task = extract_task(message)
    if task is not None:
        description, priority = task
        if not description:
            await update.message.reply_text(
                "Укажите описание задачи после триггерной фразы. Например: "
                "«Создай задачу: подготовить статус по ИИ-агенту»."
            )
            return

        try:
            await asyncio.to_thread(append_incoming_task, description, username, priority)
            await update.message.reply_text(
                "Задача записана во вкладку «Входящие».\n"
                f"Описание: {description}\n"
                f"Приоритет: {priority}\n"
                "Статус: Новая"
            )
            logger.info("Incoming task recorded for %s", username)
        except Exception as exc:
            logger.error("Could not record incoming task: %s", exc, exc_info=True)
            await update.message.reply_text(
                "Не удалось записать задачу в Google Таблицу. "
                "Пожалуйста, попробуйте позже или обратитесь к Олегу Суслину."
            )
        return

    try:
        knowledge_context = await asyncio.to_thread(build_knowledge_context)
        response = await asyncio.to_thread(
            llm_client.chat.completions.create,
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": f"{BOT_PERSONA_BASE}\n\n{knowledge_context}"},
                {"role": "user", "content": message},
            ],
            max_tokens=1000,
            temperature=0.7,
        )

        if response and response.choices and response.choices[0].message.content:
            ai_text = clean_markdown(response.choices[0].message.content)
            await update.message.reply_text(ai_text)
            logger.info("Sent Groq answer to %s", username)
        else:
            raise ValueError("Empty or malformed LLM response")
    except Exception as exc:
        logger.error("Error generating response: %s", exc, exc_info=True)
        if owner_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_chat_id,
                    text=f"Ошибка бота. Сообщение от {username}: {message}\nОшибка: {exc}",
                )
            except Exception as forwarding_error:
                logger.error("Could not forward the error to owner: %s", forwarding_error)
        await update.message.reply_text(
            "Извините, произошла техническая ошибка при обработке вашего запроса."
        )


def main() -> None:
    """Run the long-polling Telegram bot."""
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("register_owner", register_owner))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting bot with task recording; model=%s", MODEL_NAME)
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
