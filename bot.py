import os
import logging
import asyncio
from datetime import date, timedelta, datetime, time
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram import F
from aiogram.enums import ParseMode

import openpyxl
from dotenv import load_dotenv

# Для вебхуков
import uvicorn
from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
from starlette.requests import Request

import database as db

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(',')))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db.init_db()

# ---------- Вспомогательные функции ----------
def get_russian_weekday(dt: date) -> str:
    weekdays = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}
    return weekdays[dt.weekday()]

def format_date_with_weekday(dt: date) -> str:
    return f"<b>{dt.strftime('%d.%m')} ({get_russian_weekday(dt)})</b>"

def format_time(value):
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime('%H:%M')
    if isinstance(value, datetime):
        return value.strftime('%H:%M')
    if isinstance(value, str):
        return value
    return str(value)

# ---------- Клавиатура ----------
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="▶️ Запуск"))
    builder.add(KeyboardButton(text="⏹️ Стоп"))
    builder.row()
    builder.add(KeyboardButton(text="📅 Сегодня"))
    builder.add(KeyboardButton(text="📆 Неделя"))
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)

# ---------- Команда /start ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"User {message.from_user.id} sent /start")
    try:
        await message.answer(
            "Привет! Я бот с афишей мероприятий.\n"
            "Нажми «▶️ Запуск», чтобы подписаться.\n"
            "Нажми «⏹️ Стоп», чтобы отписаться.",
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Start response sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in /start: {e}", exc_info=True)

# ---------- Обработка кнопок ----------
@dp.message(F.text == "▶️ Запуск")
async def subscribe(message: Message):
    logger.info(f"User {message.from_user.id} clicked Subscribe")
    try:
        db.add_user(message.from_user.id, message.from_user.username)
        await message.answer("Вы подписаны на утреннюю рассылку! 🎉")
        logger.info(f"Subscribe OK for user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in subscribe: {e}", exc_info=True)

@dp.message(F.text == "⏹️ Стоп")
async def unsubscribe(message: Message):
    logger.info(f"User {message.from_user.id} clicked Unsubscribe")
    try:
        db.remove_user(message.from_user.id)
        await message.answer("Вы отписались.")
        logger.info(f"Unsubscribe OK for user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in unsubscribe: {e}", exc_info=True)

@dp.message(F.text == "📅 Сегодня")
async def button_today(message: Message):
    logger.info(f"User {message.from_user.id} requested today")
    try:
        today = date.today()
        events = db.get_events_for_date(today)
        text = format_events(events, today)
        await message.answer(text, parse_mode=ParseMode.HTML)
        logger.info(f"Today sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in today: {e}", exc_info=True)
        await message.answer("Произошла ошибка при получении событий.")

@dp.message(F.text == "📆 Неделя")
async def button_week(message: Message):
    logger.info(f"User {message.from_user.id} requested week")
    try:
        today = date.today()
        end_of_week = today + timedelta(days=6)
        events = db.get_events_for_week(today, end_of_week)
        if not events:
            await message.answer("На неделю нет событий.")
            return
        text = format_events_week(events, today, end_of_week)
        await message.answer(text, parse_mode=ParseMode.HTML)
        logger.info(f"Week sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in week: {e}", exc_info=True)
        await message.answer("Произошла ошибка при получении событий.")

# ---------- Форматирование ----------
def format_events(events, target_date):
    if not events:
        return f"На <b>{target_date.strftime('%d.%m.%Y')}</b> нет событий."
    lines = [f"<b>Афиша на {target_date.strftime('%d.%m.%Y')}:</b>\n"]
    for ev in events:
        start, end, ts, te, title, loc, desc = ev
        start_dt = datetime.strptime(start, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end, '%Y-%m-%d').date()
        if start_dt != end_dt:
            date_str = f"{format_date_with_weekday(start_dt)} – {format_date_with_weekday(end_dt)}"
        else:
            date_str = format_date_with_weekday(start_dt)
        time_str = f" {ts}–{te}" if ts and te else f" {ts}" if ts else ""
        line = f"• {date_str}{time_str} – {title}"
        if loc:
            line += f" ({loc})"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)
    return "\n".join(lines)

def format_events_week(events, start_date, end_date):
    lines = [f"<b>События с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')}:</b>\n"]
    for ev in events:
        start, end, ts, te, title, loc, desc = ev
        start_dt = datetime.strptime(start, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end, '%Y-%m-%d').date()
        if start_dt != end_dt:
            date_str = f"{format_date_with_weekday(start_dt)} – {format_date_with_weekday(end_dt)}"
        else:
            date_str = format_date_with_weekday(start_dt)
        time_str = f" {ts}–{te}" if ts and te else f" {ts}" if ts else ""
        line = f"• {date_str}{time_str} – {title}"
        if loc:
            line += f" ({loc})"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)
    return "\n".join(lines)

# ---------- Загрузка Excel ----------
@dp.message(F.document)
async def handle_document(message: Message):
    logger.info(f"Admin {message.from_user.id} uploaded a file")
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет прав.")
        return
    if not message.document.file_name.endswith(('.xlsx', '.xls')):
        await message.answer("Нужен Excel.")
        return

    file = await bot.get_file(message.document.file_id)
    await bot.download_file(file.file_path, "temp.xlsx")

    try:
        wb = openpyxl.load_workbook("temp.xlsx", data_only=True)
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        required = ['start_date', 'end_date', 'title']
        if not all(col in headers for col in required):
            await message.answer("Нет нужных колонок.")
            return
        idx = {h: headers.index(h) for h in headers if h in required + ['time_start', 'time_end', 'location', 'description']}
        events = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(c is None for c in row):
                continue
            def parse_date(cell):
                if isinstance(cell, datetime):
                    return cell.date().isoformat()
                try:
                    return datetime.strptime(cell, '%d.%m.%Y').date().isoformat()
                except:
                    return None
            sd = parse_date(row[idx['start_date']])
            ed = parse_date(row[idx['end_date']])
            if not sd or not ed:
                continue
            ts = format_time(row[idx.get('time_start')]) if 'time_start' in idx else None
            te = format_time(row[idx.get('time_end')]) if 'time_end' in idx else None
            title = row[idx['title']]
            loc = row[idx.get('location')] if 'location' in idx else None
            desc = row[idx.get('description')] if 'description' in idx else None
            events.append((sd, ed, ts, te, title, loc, desc))
        wb.close()
        db.clear_events()
        db.insert_events(events)
        await message.answer(f"Загружено {len(events)} событий.")
        logger.info(f"Excel processed: {len(events)} events")
    except Exception as e:
        logger.error(f"Error processing Excel: {e}", exc_info=True)
        await message.answer("Ошибка при обработке.")
    finally:
        if os.path.exists("temp.xlsx"):
            os.remove("temp.xlsx")

# ---------- Keep-alive задача ----------
async def keep_alive():
    while True:
        await asyncio.sleep(30)
        logger.info("Keep-alive signal")

# ---------- ВЕБХУКИ ----------
async def on_startup():
    webhook_url = f"{os.environ.get('RENDER_EXTERNAL_URL', '')}/webhook"
    await bot.set_webhook(webhook_url)
    asyncio.create_task(keep_alive())
    logger.info(f"Вебхук установлен: {webhook_url}")

async def on_shutdown():
    await bot.delete_webhook()
    logger.info("Вебхук удалён")

async def webhook(request: Request) -> Response:
    update_data = await request.json()
    update = types.Update(**update_data)
    await dp.feed_update(bot, update)
    return Response()

async def healthcheck(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")

app = Starlette(
    routes=[
        Route("/webhook", webhook, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
    ],
    on_startup=[on_startup],
    on_shutdown=[on_shutdown]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
