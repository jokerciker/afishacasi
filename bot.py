import os
import logging
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram import F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import pandas as pd
from dotenv import load_dotenv

import database as db
from database import DB_NAME

# Для вебхуков
import uvicorn
from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
from starlette.requests import Request

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(',')))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

db.init_db()

# ---------- Вспомогательные функции ----------
def get_russian_weekday(dt: date) -> str:
    """Возвращает краткое название дня недели по-русски."""
    weekdays = {
        0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"
    }
    return weekdays[dt.weekday()]

def format_date_with_weekday(dt: date) -> str:
    """Форматирует дату как 'дд.мм (день)' и оборачивает в жирный текст."""
    return f"<b>{dt.strftime('%d.%m')} ({get_russian_weekday(dt)})</b>"

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

    is_new = not db.user_exists(user_id)
    db.add_user(user_id, username)

    if is_new:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🎉 **Новый подписчик!**\n"
                    f"Пользователь: {username or 'нет юзернейма'}\n"
                    f"ID: {user_id}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить админа {admin_id}: {e}")

    await message.answer("Вы подписаны на утреннюю рассылку! 🎉")

    # Отправляем сегодняшнюю афишу сразу после подписки
    today = date.today()
    events = db.get_events_for_date(today)
    text = format_events_for_date(events, today)
    await message.answer(text)

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
    await message.answer(text)

@dp.message(Command("week"))
async def cmd_week(message: Message):
    today = date.today()
    end_of_week = today + timedelta(days=6)
    events = db.get_events_for_week(today, end_of_week)
    text = format_events_for_week(events, today, end_of_week, period="неделю")
    await message.answer(text)

@dp.message(Command("month"))
async def cmd_month(message: Message):
    today = date.today()
    end_of_month = today + timedelta(days=30)
    events = db.get_events_for_week(today, end_of_month)
    text = format_events_for_week(events, today, end_of_month, period="месяц")
    await message.answer(text)

# ---------- Команда /stats ----------
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администратора.")
        return

    total, recent = db.get_user_stats()

    text = f"📊 <b>Статистика подписчиков</b>\n\n"
    text += f"Всего активных: <b>{total}</b>\n\n"
    if recent:
        text += "<b>Последние 10 подписчиков:</b>\n"
        for uid, uname, created in recent:
            username = f"@{uname}" if uname else "нет юзернейма"
            date_part = created[:10] if created else "неизвестно"
            text += f"• {username} (ID: {uid}) — {date_part}\n"
    else:
        text += "Пока нет подписчиков."

    await message.answer(text, parse_mode=ParseMode.HTML)

# ---------- Команда /clear (только для админа) ----------
@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администратора.")
        return

    db.clear_events()
    await message.answer("✅ Все события удалены из базы данных.")

# ---------- Загрузка Excel (только для админа) ----------
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
        df = pd.read_excel("temp_events.xlsx", dtype=str)
        required_columns = ['start_date', 'end_date', 'title']
        if not all(col in df.columns for col in required_columns):
            await message.answer("Ошибка: файл должен содержать колонки: start_date, end_date, title (также могут быть time_start, time_end, location, description)")
            return

        events_list = []
        for _, row in df.iterrows():
            try:
                start_date = pd.to_datetime(row['start_date'], dayfirst=True).strftime('%Y-%m-%d')
                end_date = pd.to_datetime(row['end_date'], dayfirst=True).strftime('%Y-%m-%d')
            except Exception as e:
                await message.answer(f"Ошибка преобразования даты в строке {_+2}: {e}")
                return

            time_start = row.get('time_start') if pd.notna(row.get('time_start')) else None
            time_end = row.get('time_end') if pd.notna(row.get('time_end')) else None
            title = row['title']
            location = row.get('location') if pd.notna(row.get('location')) else None
            description = row.get('description') if pd.notna(row.get('description')) else None

            events_list.append((start_date, end_date, time_start, time_end, title, location, description))

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

    lines = [f"📅 <b>Афиша на сегодня ({target_date.strftime('%d.%m.%Y')}):</b>\n"]
    for ev in events:
        start, end, ts, te, title, loc, desc = ev
        start_dt = datetime.strptime(start, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end, '%Y-%m-%d').date()

        if start_dt != end_dt:
            # Многодневное событие
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
            line += f"\n  <i>{desc}</i>"
        lines.append(line)
        lines.append("")  # пустая строка для отступа

    return "\n".join(lines)

def format_events_for_week(events, start_date, end_date, period="неделю"):
    if not events:
        return f"На ближайшую {period} (с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')}) мероприятий не запланировано."

    lines = [f"📅 <b>Планы на {period} с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')}:</b>\n"]
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
            line += f"\n  <i>{desc}</i>"
        lines.append(line)
        lines.append("")

    return "\n".join(lines)

# ---------- Планировщик рассылкиы ----------
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

async def daily_mailing():
    today = date.today()
    today_events = db.get_events_for_date(today)
    today_text = format_events_for_date(today_events, today)

    end_of_week = today + timedelta(days=6)
    week_events = db.get_events_for_week(today, end_of_week)
    week_text = format_events_for_week(week_events, today, end_of_week, period="неделю")

    users = db.get_active_users()
    for user_id in users:
        try:
            await bot.send_message(user_id, today_text, parse_mode=ParseMode.HTML)
            await bot.send_message(user_id, week_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

scheduler.add_job(daily_mailing, CronTrigger(hour=7, minute=0, timezone=TIMEZONE))

# ---------- ВЕБХУКИ ----------
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