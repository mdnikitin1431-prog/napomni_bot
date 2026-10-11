import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)

API_TOKEN = '8666205463:AAEADmz7GC5ozip_Tf-tjV1zkNj68EVTFng'
DB_NAME = 'workout_bot.db'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# --- РАБОТА С БАЗОЙ ДАННЫХ (SQLite) ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            streak INTEGER DEFAULT 0,
            last_workout TEXT,
            evening_hour INTEGER DEFAULT 19,
            evening_minute INTEGER DEFAULT 30
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT streak, last_workout, evening_hour, evening_minute FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"streak": row[0], "last_workout": row[1], "hour": row[2], "minute": row[3]}
    return None

def save_user(user_id, streak=0, last_workout=None, hour=19, minute=30):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, streak, last_workout, evening_hour, evening_minute)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            streak = excluded.streak,
            last_workout = excluded.last_workout,
            evening_hour = excluded.evening_hour,
            evening_minute = excluded.evening_minute
    ''', (user_id, streak, last_workout, hour, minute))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

# --- КЛАВИАТУРЫ ---
def get_workout_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выполнил!", callback_data="workout_done")
    return builder.as_markup()

def get_time_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="7 вечера (19:00)", callback_data="set_time_19_00")
    builder.button(text="Половина 8 (19:30)", callback_data="set_time_19_30")
    builder.button(text="8 вечера (20:00)", callback_data="set_time_20_00")
    builder.adjust(1)
    return builder.as_markup()

# --- ОТПРАВКА НАПОМИНАНИЙ ---
async def send_reminder():
    user_ids = get_all_users()
    for u_id in user_ids:
        try:
            await bot.send_message(
                chat_id=u_id,
                text="💪 Пора на тренировку! Нажми кнопку, когда закончишь.",
                reply_markup=get_workout_keyboard()
            )
        except Exception as e:
            logging.error(f"Ошибка отправки пользователю {u_id}: {e}")

def update_evening_schedule(hour, minute):
    for job in scheduler.get_jobs():
        if job.id == 'evening_reminder':
            scheduler.remove_job(job.id)
    scheduler.add_job(send_reminder, 'cron', hour=hour, minute=minute, id='evening_reminder')

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        save_user(user_id, streak=0, last_workout=None, hour=19, minute=30)
    
    await message.answer(
        "Привет! Я твой личный тренер. Стрики активированы! 🔥\n"
        "Твоя цель — заниматься **3–4 раза в неделю**.\n\n"
        "Утреннее напоминание будет приходить в **9:00**.\n"
        "Выбери время для вечернего напоминания:",
        reply_markup=get_time_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data.startswith("set_time_"))
async def process_time_selection(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    time_part = callback_query.data.replace("set_time_", "")
    hour, minute = map(int, time_part.split("_"))
    
    user = get_user(user_id) or {"streak": 0, "last_workout": None}
    save_user(user_id, streak=user["streak"], last_workout=user["last_workout"], hour=hour, minute=minute)
    
    update_evening_schedule(hour, minute)
    
    await bot.edit_message_text(
        text=f"🎯 Время сохранено! Буду напоминать в **9:00** и в **{hour}:{minute:02d}**.",
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        parse_mode="Markdown"
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "workout_done")
async def process_workout_done(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user = get_user(user_id)
    
    if not user:
        await callback_query.answer("Сначала введи /start", show_alert=True)
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    current_streak = user["streak"]
    last_workout = user["last_workout"]
    
    if last_workout == today_str:
        # Уже тренировался сегодня
        msg = f"🎉 Тренировка засчитана! Сегодня ты уже занимался. Текущий стрик: **{current_streak}** дн. подряд! 🔥"
    elif last_workout == yesterday_str or last_workout is None:
        # Тренировался вчера или это самая первая тренировка
        current_streak += 1
        save_user(user_id, streak=current_streak, last_workout=today_str, hour=user["hour"], minute=user["minute"])
        msg = f"🎉 Отлично! Тренировка засчитана. Твой стрик растет: **{current_streak}** дн. подряд! 🔥"
    else:
        # Прошлый стрик сгорел (прошло больше 1 дня)
        current_streak = 1
        save_user(user_id, streak=current_streak, last_workout=today_str, hour=user["hour"], minute=user["minute"])
        msg = f"Упс, был перерыв, и прошлый стрик сбросился. 🐾 Но ничего! Начинаем новый стрик: **1** день! Погнали! 💪"

    await bot.edit_message_text(
        text=msg,
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        parse_mode="Markdown"
    )
    await callback_query.answer()

# --- ЗАПУСК ---
async def main():
    init_db()
    scheduler.add_job(send_reminder, 'cron', hour=9, minute=0, id='morning_reminder')
    scheduler.add_job(send_reminder, 'cron', hour=19, minute=30, id='evening_reminder')
    scheduler.start()
    
    print("Бот запущен локально с поддержкой базы данных SQLite и стриков...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
