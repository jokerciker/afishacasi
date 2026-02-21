import os
import logging
from datetime import date, timedelta, datetime, time
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram import F
from aiogram.enums import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db.init_db()

# ---------- Вспомогательные функции ----------
def get_russian_weekday(dt: date) -> str:
    weekdays = {
        0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"
    }
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

def format_description_with_bold(text: str) -> str:
    """
    Форматирует описание:
    - Заменяет | на перевод строки.
    - В каждой строке делает жирным текст до первого двоеточия (включая двоеточие).
    """
    if not text:
        return text

    text = text.replace('|', '\n')
    lines = text.split('\n')
    formatted_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            formatted_lines.append('')
            continue

        if ':' in line:
            parts = line.split(':', 1)
            key = parts[0].strip()
            value = parts[1].strip()
            formatted_lines.append(f"<b>{key}:</b> {value}")
        else:
            formatted_lines.append(line)

    return '\n'.join(formatted_lines)

# ---------- Клавиатура ----------
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="▶️ Запуск"))
    builder.add(KeyboardButton(text="⏹️ Стоп"))
    builder.row()
    builder.add(KeyboardButton(text="📅 Сегодня"))
    builder.add(KeyboardButton(text="📆 Неделя"))
    builder.add(KeyboardButton(text="📅 Месяц"))
    builder.adjust(2, 3)
    return builder.as_markup(resize_keyboard=True)

# ---------- Команда /start ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот с афишей мероприятий.\n"
        "Нажми «▶️ Запуск», чтобы получать ежедневные уведомления в 7 утра о событиях на сегодня и на неделю.\n"
        "Нажми «⏹️ Стоп», чтобы отписаться.",
        reply_markup=get_main_keyboard()
    )

# ---------- Обработка кнопок ----------
@dp.message(F.text == "▶️ Запуск")
async def subscribe(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    db.add_user(user_id, username)
    await message.answer("Вы подписаны на утреннюю рассылку! 🎉")
    today = date.today()
    events = db.get_events_for_date(today)
    text = format_events_for_date(events, today)
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "⏹️ Стоп")
async def unsubscribe(message: Message):
    user_id = message.from_user.id
    db.remove_user(user_id)
    await message.answer("Вы отписались от рассылки. Чтобы вернуться, нажмите «▶️ Запуск».")

@dp.message(F.text == "📅 Сегодня")
async def button_today(message: Message):
    await cmd_today(message)

@dp.message(F.text == "📆 Неделя")
async def button_week(message: Message):
    await cmd_week(message)

@dp.message(F.text == "📅 Месяц")
async def button_month(message: Message):
    await cmd_month(message)

# ---------- Команды /today, /week, /month ----------
@dp.message(Command("today"))
async def cmd_today(message: Message):
    today = date.today()
    events = db.get_events_for_date(today)
    text = format_events_for_date(events, today)
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("week"))
async def cmd_week(message: Message):
    today = date.today()
    end_of_week = today + timedelta(days=6)
    events = db.get_events_for_week(today, end_of_week)
    text = format_events_for_week(events, today, end_of_week, "неделю")
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("month"))
async def cmd_month(message: Message):
    today = date.today()
    end_of_month = today + timedelta(days=30)
    events = db.get_events_for_week(today, end_of_month)
    text = format_events_for_week(events, today, end_of_month, "месяц")
    await message.answer(text, parse_mode=ParseMode.HTML)

# ---------- Команда /clear ----------
@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администратора.")
        return
    db.clear_events()
    await message.answer("✅ Все события удалены из базы данных.")

# ---------- Загрузка Excel ----------
@dp.message(F.document)
async def handle_document(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав на загрузку файлов.")
        return

    document = message.document
    if not document.file_name.endswith(('.xlsx', '.xls')):
        await message.answer("Пожалуйста, загрузите файл формата Excel (.xlsx или .xls).")
        return

    file_id = document.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    await bot.download_file(file_path, "temp_events.xlsx")

    try:
        wb = openpyxl.load_workbook("temp_events.xlsx", data_only=True)
        ws = wb.active

        headers = [cell.value for cell in ws[1]]
        required = ['start_date', 'end_date', 'title']
        if not all(col in headers for col in required):
            await message.answer("Ошибка: файл должен содержать колонки: start_date, end_date, title")
            return

        idx = {h: headers.index(h) for h in headers if h in required + ['time_start', 'time_end', 'location', 'description']}

        events_list = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(cell is None for cell in row):
                continue

            def parse_date(cell):
                if isinstance(cell, datetime):
                    return cell.date().isoformat()
                elif isinstance(cell, str):
                    try:
                        return datetime.strptime(cell, '%d.%m.%Y').date().isoformat()
                    except:
                        raise ValueError(f"Неверный формат даты: {cell}")
                else:
                    raise ValueError(f"Неверный тип даты: {cell}")

            try:
                start_date = parse_date(row[idx['start_date']])
                end_date = parse_date(row[idx['end_date']])
            except Exception as e:
                await message.answer(f"Ошибка в строке {row[0].row if hasattr(row, 'row') else '?'}: {e}")
                return

            time_start = format_time(row[idx.get('time_start')]) if 'time_start' in idx else None
            time_end = format_time(row[idx.get('time_end')]) if 'time_end' in idx else None
            title = row[idx['title']]
            location = row[idx.get('location')] if 'location' in idx else None
            description = row[idx.get('description')] if 'description' in idx else None

            events_list.append((start_date, end_date, time_start, time_end, title, location, description))

        wb.close()
        db.clear_events()
        db.insert_events(events_list)

        await message.answer(f"Расписание успешно обновлено! Загружено событий: {len(events_list)}")

    except Exception as e:
        await message.answer(f"Произошла ошибка при обработке файла: {e}")
    finally:
        if os.path.exists("temp_events.xlsx"):
            os.remove("temp_events.xlsx")

# ---------- Форматирование сообщений ----------
def format_events_for_date(events, target_date):
    if not events:
        return f"На сегодня (<b>{target_date.strftime('%d.%m.%Y')}</b>) мероприятий не запланировано."

    lines = [f"<b>Афиша на сегодня ({target_date.strftime('%d.%m.%Y')}):</b>\n"]
    for ev in events:
        start, end, ts, te, title, loc, desc = ev
        start_dt = datetime.strptime(start, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end, '%Y-%m-%d').date()

        if start_dt != end_dt:
            date_str = f"{format_date_with_weekday(start_dt)} – {format_date_with_weekday(end_dt)}"
        else:
            date_str = format_date_with_weekday(start_dt)

        time_str = ""
        if ts and te:
            time_str = f" {ts}–{te}"
        elif ts:
            time_str = f" {ts}"

        line = f"• {date_str}{time_str} – {title}"
        if loc:
            line += f" ({loc})"
        if desc:
            formatted_desc = format_description_with_bold(desc)
            line += f"\n  <i>{formatted_desc}</i>"
        lines.append(line)
        lines.append("")

    return "\n".join(lines)

def format_events_for_week(events, start_date, end_date, period="неделю"):
    if not events:
        return f"На ближайшую {period} (с <b>{start_date.strftime('%d.%m')}</b> по <b>{end_date.strftime('%d.%m')}</b>) мероприятий не запланировано."

    lines = [f"<b>Планы на {period} с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')}:</b>\n"]
    for ev in events:
        start, end, ts, te, title, loc, desc = ev
        start_dt = datetime.strptime(start, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end, '%Y-%m-%d').date()

        if start_dt != end_dt:
            date_str = f"{format_date_with_weekday(start_dt)} – {format_date_with_weekday(end_dt)}"
        else:
            date_str = format_date_with_weekday(start_dt)

        time_str = ""
        if ts and te:
            time_str = f" {ts}–{te}"
        elif ts:
            time_str = f" {ts}"

        line = f"• {date_str}{time_str} – {title}"
        if loc:
            line += f" ({loc})"
        if desc:
            formatted_desc = format_description_with_bold(desc)
            line += f"\n  <i>{formatted_desc}</i>"
        lines.append(line)
        lines.append("")

    return "\n".join(lines)

# ---------- Планировщик ----------
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

async def daily_mailing():
    today = date.today()
    today_events = db.get_events_for_date(today)
    today_text = format_events_for_date(today_events, today)

    end_of_week = today + timedelta(days=6)
    week_events = db.get_events_for_week(today, end_of_week)
    week_text = format_events_for_week(week_events, today, end_of_week, "неделю")

    users = db.get_active_users()
    for user_id in users:
        try:
            await bot.send_message(user_id, today_text, parse_mode=ParseMode.HTML)
            await bot.send_message(user_id, week_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

scheduler.add_job(daily_mailing, CronTrigger(hour=7, minute=0, timezone=TIMEZONE))

# ---------- ВЕБХУКИ (для Render) ----------
async def on_startup():
    webhook_url = f"{os.environ.get('RENDER_EXTERNAL_URL', '')}/webhook"
    await bot.set_webhook(webhook_url)
    scheduler.start()
    logging.info(f"Бот запущен с вебхуком: {webhook_url}")

async def on_shutdown():
    await bot.delete_webhook()
    logging.info("Бот остановлен")

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
