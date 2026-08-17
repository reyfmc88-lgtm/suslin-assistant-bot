import os
import logging
import requests
import csv
import io
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# Configuration from Environment Variables
BOT_TOKEN = os.getenv('BOT_TOKEN', '8959543595:AAGt6WfZEiesptCFCHhaU_mg2p2pZO_8rws')
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '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')
GROQ_API_BASE = os.getenv('GROQ_API_BASE', 'https://api.groq.com/openai/v1')
OWNER_USERNAME = os.getenv('OWNER_USERNAME', '@Oleg_Suslin')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '1sD8mYWY5j5Eo-nv7S1rOw_tAmdGl2OC7lXgxgTuGRIU')
MODEL_NAME = os.getenv('MODEL_NAME', 'llama-3.3-70b-versatile')

# Groq is OpenAI-compatible
client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_API_BASE)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variable to store owner chat_id
owner_chat_id = None

# Bot persona
BOT_PERSONA_BASE = (
    "Ты — ИИ-ассистент Олега Суслина, PPM проектов Альфа-Инвестиции. "
    "Твоя задача — отвечать на вопросы коллег и клиентов от его имени. "
    "Отвечай вежливо, профессионально и на русском языке. "
    "Используй данные из базы знаний ниже как основной источник информации для ответов. "
    "Если вопрос совсем не касается работы или Альфа-Инвестиций, мягко верни разговор к теме."
)


def fetch_sheet_csv(sheet_name: str) -> list:
    """Fetches public Google Sheet tab as list of rows using CSV export URL."""
    url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={sheet_name}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            reader = csv.reader(io.StringIO(response.text))
            return list(reader)
        else:
            logger.error(f"Failed to fetch sheet {sheet_name}, status code: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Exception fetching sheet {sheet_name}: {e}")
        return []


def format_rows_as_text(rows: list, sheet_label: str) -> str:
    """Formats a list of CSV rows into a readable text block."""
    if not rows:
        return f"[{sheet_label}: данные отсутствуют]\n"

    lines = [f"=== {sheet_label} ==="]
    headers = rows[0] if rows else []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row if cell):
            continue
        parts = []
        for i, cell in enumerate(row):
            header = headers[i] if i < len(headers) else f"Столбец {i+1}"
            if cell:
                parts.append(f"{header}: {cell}")
        if parts:
            lines.append(" | ".join(parts))
    lines.append("")
    return "\n".join(lines)


def build_knowledge_context() -> str:
    """Reads all three sheets via public CSV export and builds a knowledge context string."""
    logger.info("Reading Google Sheets data via CSV export...")

    projects_rows = fetch_sheet_csv("Проекты")
    backlog_rows = fetch_sheet_csv("Бэклог")
    faq_rows = fetch_sheet_csv("FAQ")

    context_parts = [
        "БАЗА ЗНАНИЙ ПО ПРОЕКТАМ АЛЬФА-ИНВЕСТИЦИИ (актуальные данные из Google Таблицы):\n",
        format_rows_as_text(projects_rows, "Проекты: Название, PO, Команда, Статус, Описание, Метрики"),
        format_rows_as_text(backlog_rows, "Бэклог: Задачи и фичи по проектам"),
        format_rows_as_text(faq_rows, "FAQ: Часто задаваемые вопросы и ответы"),
    ]

    context = "\n".join(context_parts)
    logger.info(f"Knowledge context built: {len(context)} chars")
    return context


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message on /start."""
    user = update.effective_user
    welcome_text = (
        f"Привет, {user.mention_html()}!\n\n"
        "Я — ИИ-ассистент Олега Суслина, PPM проектов Альфа-Инвестиции. "
        "Готов ответить на ваши вопросы по нашим проектам и продуктам. "
        "Мои ответы основаны на актуальных данных из базы знаний."
    )
    await update.message.reply_html(welcome_text)


async def register_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers the owner's chat ID."""
    global owner_chat_id
    if update.effective_user.username == OWNER_USERNAME.lstrip('@'):
        owner_chat_id = update.effective_chat.id
        await update.message.reply_text("Вы успешно зарегистрированы как владелец бота.")
        logger.info(f"Owner chat_id registered: {owner_chat_id}")
    else:
        await update.message.reply_text("Только Олег Суслин может зарегистрироваться как владелец бота.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles ALL incoming text messages using Groq API with Google Sheets context."""
    user_message = update.message.text
    username = update.effective_user.username or update.effective_user.first_name
    logger.info(f"Received message from {username}: {user_message}")

    try:
        # Build knowledge context from Google Sheets on every request
        knowledge_context = build_knowledge_context()

        # Compose full system prompt with knowledge base
        system_prompt = f"{BOT_PERSONA_BASE}\n\n{knowledge_context}"

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=1000,
            temperature=0.7
        )

        logger.info(f"Groq Response received: choices={bool(response.choices)}")

        # Safe parsing
        if response and hasattr(response, 'choices') and response.choices and len(response.choices) > 0:
            choice = response.choices[0]
            if choice.message and choice.message.content:
                ai_response = choice.message.content
                await update.message.reply_text(ai_response)
                logger.info(f"Sent AI response to {username}")
            else:
                raise ValueError("Empty content in AI response")
        else:
            raise ValueError("Invalid response structure from AI model")

    except Exception as e:
        logger.error(f"Error generating AI response: {e}", exc_info=True)
        error_text = "Извините, произошла техническая ошибка при обработке вашего запроса."

        if owner_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_chat_id,
                    text=(
                        f"Ошибка у бота при ответе пользователю "
                        f"{update.effective_user.mention_html() if update.effective_user else 'Unknown'}.\n"
                        f"Сообщение: {user_message}\nОшибка: {e}"
                    )
                )
                await update.message.reply_text(f"{error_text} Ваш вопрос переслан Олегу Суслину.")
            except Exception as forward_err:
                logger.error(f"Failed to forward error to owner: {forward_err}")
                await update.message.reply_text(error_text)
        else:
            await update.message.reply_text(error_text)


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("register_owner", register_owner))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(f"Starting bot polling on Railway (v8 - Groq API + CSV) using {MODEL_NAME}...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == '__main__':
    main()
