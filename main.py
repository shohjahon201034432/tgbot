import asyncio
import logging
import os
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

# Import the correct webhook handler for aiogram 3.x
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# .env faylidan muhit o'zgaruvchilarini yuklash
load_dotenv()

# Loglashni sozlash
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot tokeni va admin ID'ni olish
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
# Render muhiti uchun web app name olinadi
WEB_APP_NAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
# Bot username
BOT_USERNAME = os.getenv("BOT_USERNAME")
if not BOT_USERNAME:
    logging.error("BOT_USERNAME muhit o'zgaruvchisi topilmadi. Bot username'i kerak.")

# Agar RENDER_EXTERNAL_HOSTNAME mavjud bo'lmasa, u ishlamaydi
if not WEB_APP_NAME:
    logging.error("RENDER_EXTERNAL_HOSTNAME muhit o'zgaruvchisi topilmadi. Webhook uchun manzil kerak.")

# Bot va dispatcher'ni ishga tushirish
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

# --- DATABASE FUNKSIYALARI ---
import sqlite3

def init_db():
    conn = sqlite3.connect('bot_db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            refs INTEGER DEFAULT 0,
            pending_ref_id INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            user_id INTEGER UNIQUE,
            ref_id INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def get_channels():
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM channels")
        return [row[0] for row in cursor.fetchall()]

def add_channel(username: str):
    username = username.strip()
    if not username.startswith('@'):
        username = '@' + username
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO channels (username) VALUES (?)", (username,))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def remove_channel(username: str):
    username = username.strip()
    if not username.startswith('@'):
        username = '@' + username
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channels WHERE username=?", (username,))
        conn.commit()

def user_exists(user_id: int):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None

def has_referral(user_id: int):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM referrals WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None

def add_user(user_id: int, username: str = None):
    if not user_exists(user_id):
        with sqlite3.connect('bot_db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (user_id, username, refs) VALUES (?,?,0)", (user_id, username))
            conn.commit()

def set_user_phone(user_id: int, phone: str):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
        conn.commit()

def get_user_phone(user_id: int):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else None

def get_user_info(user_id: int):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, phone FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res if res else (None, None)

def add_referral(user_id: int, ref_id: int) -> bool:
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        if user_id == ref_id:
            return False
        cursor.execute("SELECT ref_id FROM referrals WHERE user_id=?", (user_id,))
        if cursor.fetchone():
            return False
        
        cursor.execute("INSERT INTO referrals (user_id, ref_id) VALUES (?,?)", (user_id, ref_id))
        
        current = ref_id
        level = 1
        while current and level <= 2:
            cursor.execute("UPDATE users SET refs = refs + 1 WHERE user_id=?", (current,))
            cursor.execute("SELECT ref_id FROM referrals WHERE user_id=?", (current,))
            row = cursor.fetchone()
            current = row[0] if row else None
            level += 1
        
        conn.commit()
    return True

def get_user_refs(user_id: int):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT refs FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else 0

def get_top_refs(limit=10):
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, phone, refs FROM users ORDER BY refs DESC LIMIT ?", (limit,))
        return cursor.fetchall()

def get_all_users():
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, phone, refs FROM users ORDER BY user_id")
        return cursor.fetchall()

# --- END DATABASE FUNKSIYALARI ---

# --- YORDAMCHI FUNKSIYALAR ---
import random

async def is_subscribed(bot: Bot, user_id: int):
    channels = get_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logging.error(f"Error checking subscription for {ch}: {e}")
            return False
    return True

def get_main_menu_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Referral link", callback_data="get_ref"),
         InlineKeyboardButton(text="📊 Statistikam", callback_data="my_refs")],
        [InlineKeyboardButton(text="🏆 Top 10", callback_data="top_refs"),
         InlineKeyboardButton(text="ℹ️ Yordam", callback_data="help")]
    ])
    return kb

def get_menu_trigger_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Menyu")]
    ])
    return kb

def get_user_display_name(username, phone, user_id):
    if username:
        return f"@{username}"
    elif phone:
        return f"📱 {phone}"
    else:
        return f"ID: {user_id}"

# --- HANDLERS ---

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    ref_id = int(args[0]) if args and args[0].isdigit() else None

    add_user(user_id, username)

    if ref_id and ref_id != user_id and not get_user_phone(user_id) and not has_referral(user_id):
        with sqlite3.connect('bot_db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET pending_ref_id = ? WHERE user_id=?", (ref_id, user_id))
            conn.commit()

    display_name = get_user_display_name(username, get_user_phone(user_id), user_id)

    welcome_msg = (
        "🎉 *Xush kelibsiz, {}!* 🎉\n\n"
        "Bu bot orqali do'stlaringizni taklif qilib, ball to'plashingiz mumkin! 😎\n\n"
        "📋 *Qadamlar:*\n"
        "1️⃣ Kanal va guruhlarga obuna bo'ling\n"
        "2️⃣ Telefon raqamingizni yuboring\n"
        "3️⃣ Referral linkingizni do'stlaringizga ulashing!\n\n"
        "🚀 Hoziroq boshlash uchun quyidagi ko'rsatmalarga amal qiling!"
    ).format(display_name)

    await message.answer(welcome_msg)

    subscribed = await is_subscribed(message.bot, user_id)
    if not subscribed:
        channels = get_channels()
        if channels:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📢 {ch} ga obuna bo'lish", url=f"https://t.me/{ch.strip('@')}")]
                for ch in channels
            ])
            kb.inline_keyboard.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
            await message.answer(
                "🔗 *Avval quyidagi kanal va guruhlarga obuna bo'ling:*\n\n"
                "Obunadan so'ng *'✅ Obunani tekshirish'* tugmasini bosing! 🚀",
                reply_markup=kb
            )
        return

    phone = get_user_phone(user_id)
    if not phone:
        phone_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ])
        await message.answer(
            "📞 *Telefon raqamingizni yuboring:*\n\n"
            "Bu ro'yxatdan o'tish va referral tizimidan foydalanish uchun zarur.\n\n"
            "🔒 *Xavfsizlik:* Raqamingiz faqat adminlar uchun ko'rinadi va boshqa maqsadlarda ishlatilmaydi.",
            reply_markup=phone_kb
        )
    else:
        ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
        final_msg = (
            f"🎉 *Tabriklaymiz, {display_name}!*\n\n"
            f"✅ Siz muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n"
            f"🔗 *Sizning referral linkingiz:*\n`{ref_link}`\n\n"
            "📋 *Qanday ishlaydi?*\n"
            "• Do'stlaringizga linkingizni ulashing\n"
            "• Ular bot orqali ro'yxatdan o'tgach, sizga ball qo'shiladi\n"
            "• Har bir to'g'ridan-to'g'ri taklif uchun +1 ball\n"
            "• Ikkinchi darajadagi taklif uchun ham +1 ball\n\n"
            "💰 Ko'proq ball to'plang va mukofotlarga ega bo'ling! 🏆"
        )
        await message.answer(final_msg, reply_markup=get_menu_trigger_keyboard())

@dp.callback_query(F.data == 'check_sub')
async def check_sub_handler(call: types.CallbackQuery):
    user_id = call.from_user.id
    subscribed = await is_subscribed(call.bot, user_id)
    
    if subscribed:
        await call.answer("✅ Obuna muvaffaqiyatli tasdiqlandi!")
        try:
            await call.message.delete()
        except Exception:
            pass
        
        phone = get_user_phone(user_id)
        if not phone:
            phone_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
                [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
            ])
            await call.bot.send_message(
                user_id,
                "🎉 *Obuna tasdiqlandi!*\n\n"
                "📞 Endi telefon raqamingizni yuboring:\n\n"
                "🔒 *Xavfsizlik:* Raqamingiz xavfsiz saqlanadi va faqat adminlar uchun ko'rinadi.",
                reply_markup=phone_kb
            )
        else:
            username, phone = get_user_info(user_id)
            display_name = get_user_display_name(username, phone, user_id)
            ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
            
            final_msg = (
                f"🎉 *Tabriklaymiz, {display_name}!*\n\n"
                f"✅ Siz muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n"
                f"🔗 *Sizning referral linkingiz:*\n`{ref_link}`\n\n"
                "Do'stlaringizni taklif qiling va ball to'plang! 😎"
            )
            await call.bot.send_message(user_id, final_msg, reply_markup=get_menu_trigger_keyboard())
    else:
        await call.answer("❌ Hali barcha kanallarga obuna bo'lmadingiz! Iltimos, avval obuna bo'lib, keyin qayta urinib ko'ring.", show_alert=True)

@dp.message(F.content_type == types.ContentType.CONTACT)
async def contact_handler(message: types.Message):
    user_id = message.from_user.id
    
    if message.contact is None or message.contact.user_id != user_id:
        await message.answer("🚫 Iltimos, faqat o'zingizning telefon raqamingizni yuboring.")
        return

    phone = message.contact.phone_number
    previous_phone = get_user_phone(user_id)

    if not await is_subscribed(message.bot, user_id):
        await message.answer(
            "🚫 Avval kanal va guruhlarga obuna bo'ling!\n\n"
            "Obuna bo'lgandan keyin /start buyrug'ini qayta bosing.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    set_user_phone(user_id, phone)

    if previous_phone:
        await message.answer("📱 Telefon raqamingiz muvaffaqiyatli yangilandi! ✅", reply_markup=get_menu_trigger_keyboard())
        return

    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pending_ref_id FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            ref_id = row[0]
            if add_referral(user_id, ref_id):
                cursor.execute("UPDATE users SET pending_ref_id = NULL WHERE user_id=?", (user_id,))
                conn.commit()
                try:
                    await message.bot.send_message(
                        ref_id, 
                        "🎉 *Yangi referral!*\n\n"
                        "Sizga yangi referral qo'shildi! Ballaringiz +1 ga oshdi! 🚀\n\n"
                        "📊 Statistikangizni ko'rish uchun menyudan foydalaning."
                    )
                except Exception as e:
                    logging.error(f"Error notifying referrer {ref_id}: {e}")

    username, phone = get_user_info(user_id)
    display_name = get_user_display_name(username, phone, user_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    
    success_msg = (
        f"🎉 *Tabriklaymiz, {display_name}!*\n\n"
        f"✅ Siz muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n"
        f"🔗 *Sizning referral linkingiz:*\n`{ref_link}`\n\n"
        "📋 *Endi quyidagilarni qiling:*\n"
        "• Linkingizni do'stlaringizga ulashing\n"
        "• Har bir yangi a'zo uchun ball oling\n"
        "• Top 10 da o'rningizni egalang!\n\n"
        "💰 Ko'proq ball to'plang va mukofotlarga ega bo'ling! 🏆"
    )
    
    await message.answer(success_msg, reply_markup=get_menu_trigger_keyboard())

@dp.message(F.text == "Menyu")
async def show_menu_handler(message: types.Message):
    user_id = message.from_user.id
    phone = get_user_phone(user_id)
    
    if not phone:
        await message.answer(
            "🚫 Siz hali ro'yxatdan o'tmadingiz!\n\n"
            "Iltimos, /start buyrug'ini bosing va ro'yxatdan o'ting. 😊",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    await message.answer("🚀 *Asosiy menyu:*", reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == 'get_ref')
async def callback_get_ref_handler(call: types.CallbackQuery):
    user_id = call.from_user.id
    phone = get_user_phone(user_id)
    
    if not phone:
        await call.answer("🚫 Avval telefon raqamingizni yuboring!", show_alert=True)
        return
    
    username, phone = get_user_info(user_id)
    display_name = get_user_display_name(username, phone, user_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    refs_count = get_user_refs(user_id)
    
    ref_msg = (
        f"🔗 *{display_name}, sizning referral linkingiz:*\n\n"
        f"`{ref_link}`\n\n"
        f"📊 *Hozirgi ballaringiz:* {refs_count}\n\n"
        "📋 *Qanday ishlatish:*\n"
        "• Linkni nusxalab oling\n"
        "• Do'stlaringiz bilan ulashing\n"
        "• Telegram, WhatsApp, Instagram va boshqa ijtimoiy tarmoqlarda tarqating\n\n"
        "💡 *Maslahat:* Ko'proq do'stlaringizni taklif qiling va top reytingga chiqing! 🏆"
    )
    
    await call.answer()
    await call.message.edit_text(ref_msg, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == 'my_refs')
async def callback_my_refs_handler(call: types.CallbackQuery):
    user_id = call.from_user.id
    refs = get_user_refs(user_id)
    username, phone = get_user_info(user_id)
    display_name = get_user_display_name(username, phone, user_id)
    
    all_users = get_top_refs(1000)
    user_rank = None
    for idx, (uid, _, _, _) in enumerate(all_users, 1):
        if uid == user_id:
            user_rank = idx
            break
    
    rank_text = f"🏅 *Sizning o'rningiz:* {user_rank}-o'rin" if user_rank else "🏅 *O'riningiz:* Aniqlanmagan"
    
    stats_msg = (
        f"📊 *{display_name} - Sizning statistikangiz:*\n\n"
        f"👥 *Jami referrallar:* {refs} ta\n"
        f"{rank_text}\n\n"
        "📋 *Tafsilot:*\n"
        "• To'g'ridan-to'g'ri takliflar va ikkinchi darajadagi takliflar hisobga olinadi\n"
        "• Har bir faol taklif +1 ball\n\n"
        f"🎯 *Maqsad:* {max(10, refs + 5)} ta referral to'plang!\n\n"
        "💪 Ko'proq do'stlaringizni taklif qiling va yuqori o'rinlarga chiqing! 🚀"
    )
    
    await call.answer()
    await call.message.edit_text(stats_msg, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == 'top_refs')
async def callback_top_refs_handler(call: types.CallbackQuery):
    top = get_top_refs(10)
    if not top:
        await call.answer("❌ Hali hech kim referral qilmagan!", show_alert=True)
        return
    
    msg = "🏆 *Top 10 Referral Liderlari:*\n\n"
    medals = ["🥇", "🥈", "🥉"]
    
    for idx, (user_id, username, phone, refs) in enumerate(top, start=1):
        medal = medals[idx-1] if idx <= 3 else f"{idx}."
        display_name = get_user_display_name(username, phone, user_id)
        msg += f"{medal} {display_name} — *{refs} ball*\n"
    
    msg += "\n💡 *Sizning o'rningizni yaxshilash uchun ko'proq do'stlaringizni taklif qiling!*"
    
    await call.answer()
    await call.message.edit_text(msg, reply_markup=get_main_menu_keyboard())

@dp.callback_query(F.data == 'help')
async def callback_help_handler(call: types.CallbackQuery):
    help_msg = (
        "ℹ️ *Yordam bo'limi*\n\n"
        "Bu bot orqali do'stlaringizni taklif qilib ball to'plashingiz mumkin! 😎\n\n"
        "🔍 *Bot qanday ishlaydi?*\n\n"
        "1️⃣ *Ro'yxatdan o'tish:*\n"
        "   • /start buyrug'ini bosing\n"
        "   • Kanal va guruhlarga obuna bo'ling\n"
        "   • Telefon raqamingizni yuboring\n\n"
        "2️⃣ *Referral tizimi:*\n"
        "   • Sizning maxsus linkingizni oling\n"
        "   • Do'stlaringizga ulashing\n"
        "   • Ular ro'yxatdan o'tganda ball oling\n\n"
        "3️⃣ *Ball tizimi:*\n"
        "   • To'g'ridan-to'g'ri taklif: +1 ball\n"
        "   • Ikkinchi darajadagi taklif: +1 ball\n\n"
        "🎯 *Maqsad:* Ko'proq ball to'plang va top reytingda bo'ling!\n\n"
        "📞 *Yordam kerakmi?* Admin: @murojat_adm\n\n"
        "🚀 *Muvaffaqiyatlar tilaymiz!*"
    )
    
    await call.answer()
    await call.message.edit_text(help_msg, reply_markup=get_main_menu_keyboard())

@dp.message(Command("addchannel"))
async def addchannel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    args = message.text.split()[1:]
    if not args:
        await message.answer("📥 Iltimos, kanal yoki guruh username'ini kiriting.\n\nMisol: `/addchannel @kanalim`")
        return
    
    username = args[0]
    success = add_channel(username)
    if success:
        await message.answer(f"✅ Kanal/guruh `{username}` muvaffaqiyatli qo'shildi!")
    else:
        await message.answer(f"⚠️ Kanal/guruh `{username}` allaqachon ro'yxatda mavjud.")

@dp.message(Command("removechannel"))
async def removechannel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    args = message.text.split()[1:]
    if not args:
        await message.answer("📥 Iltimos, kanal yoki guruh username'ini kiriting.\n\nMisol: `/removechannel @kanalim`")
        return
    username = args[0]
    remove_channel(username)
    await message.answer(f"🗑️ Kanal/guruh `{username}` ro'yxatdan olib tashlandi!")

@dp.message(Command("channels"))
async def channels_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    channels = get_channels()
    if not channels:
        await message.answer("📋 Hozircha kanallar ro'yxati bo'sh.")
        return
    msg = "📋 *Joriy kanallar ro'yxati:*\n\n"
    for i, ch in enumerate(channels, 1):
        msg += f"{i}. `{ch}`\n"
    await message.answer(msg)

@dp.message(Command("random"))
async def random_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    args = message.text.split()[1:]
    try:
        n = int(args[0])
    except (ValueError, IndexError):
        await message.answer("📥 Iltimos, to'g'ri son kiriting.\n\nMisol: `/random 5`")
        return

    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE phone IS NOT NULL")
        all_users = [row[0] for row in cursor.fetchall()]
    
    if n > len(all_users):
        await message.answer(f"⚠️ Botda faqat {len(all_users)} ta ro'yxatdan o'tgan foydalanuvchi bor.")
        return

    chosen = random.sample(all_users, n)
    msg = f"🎲 *Tasodifiy tanlangan {n} ta foydalanuvchi:*\n\n"
    for i, u in enumerate(chosen, 1):
        username, phone = get_user_info(u)
        display_name = get_user_display_name(username, phone, u)
        msg += f"{i}. {display_name} (ID: {u})\n"
    await message.answer(msg)

@dp.message(Command("allusers"))
async def allusers_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    users = get_all_users()
    if not users:
        await message.answer("📋 Hozircha ro'yxatdan o'tgan foydalanuvchilar yo'q.")
        return
    
    lines = ["📋 *Barcha foydalanuvchilar:*\n\n"]
    for u in users:
        user_id, username, phone, refs = u
        display_name = get_user_display_name(username, phone, user_id)
        status = "✅" if phone else "❌"
        lines.append(f"{status} {display_name} | 🏆 {refs} ball")
    
    current_msg = ""
    for line in lines:
        if len(current_msg) + len(line) + 1 > 4096:
            await message.answer(current_msg, parse_mode=ParseMode.MARKDOWN)
            current_msg = ""
        current_msg += line + "\n"
    
    if current_msg:
        await message.answer(current_msg, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE phone IS NOT NULL")
        registered_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM referrals")
        total_referrals = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM channels")
        total_channels = cursor.fetchone()[0]
    
    stats_msg = (
        "📊 *Bot statistikasi:*\n\n"
        f"👥 *Jami foydalanuvchilar:* {total_users}\n"
        f"✅ *Ro'yxatdan o'tganlar:* {registered_users}\n"
        f"🔗 *Jami referrallar:* {total_referrals}\n"
        f"📢 *Kanallar soni:* {total_channels}\n\n"
        f"📈 *Ro'yxatdan o'tish foizi:* {round(registered_users/total_users*100, 1) if total_users > 0 else 0}%"
    )
    await message.answer(stats_msg)

@dp.message(Command("broadcast"))
async def broadcast_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    
    msg_text = message.text[len('/broadcast '):].strip()
    
    if not msg_text:
        await message.answer("📥 Foydalanish: `/broadcast xabar matni`")
        return
    
    users = get_all_users()
    if not users:
        await message.answer("❌ Hali foydalanuvchilar yo'q.")
        return
    
    success = 0
    fail = 0
    for u in users:
        user_id = u[0]
        try:
            await message.bot.send_message(user_id, msg_text)
        except Exception:
            fail += 1
            continue
    
    await message.answer(
        f"📢 Xabar yuborildi!\n\n"
        f"✅ Muvaffaqiyatli: {success}\n"
        f"❌ Xatolik: {fail}"
    )

@dp.message()
async def default_handler(message: types.Message):
    user_id = message.from_user.id
    phone = get_user_phone(user_id)
    
    if not phone:
        await message.answer(
            "🚫 Siz hali ro'yxatdan o'tmadingiz!\n\n"
            "Iltimos, /start buyrug'ini bosing va ro'yxatdan o'ting. 😊",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await message.answer(
            "🤖 *Noto'g'ri buyruq!*\n\n"
            "Asosiy menyuga kirish uchun 'Menyu' tugmasidan foydalaning.",
            reply_markup=get_menu_trigger_keyboard()
        )

# --- BOTNI ISHGA TUSHIRISH ---
WEBHOOK_PATH = f"/{API_TOKEN}"
WEBHOOK_URL = f"https://{WEB_APP_NAME}{WEBHOOK_PATH}"

# This on_startup function is an async handler that will be automatically called by aiohttp.
async def on_startup(app):
    # Initializes the database.
    init_db()
    logging.info("🚀 Bot ishga tushirildi va ma'lumotlar bazasi tayyorlandi!")
    logging.info(f"✅ Webhook o'rnatilmoqda: {WEBHOOK_URL}")
    try:
        # Sets the bot's webhook URL.
        await bot.set_webhook(url=WEBHOOK_URL, allowed_updates=dp.resolve_used_update_types())
        logging.info("✅ Webhook muvaffaqiyatli o'rnatildi!")
    except TelegramBadRequest as e:
        logging.error(f"❌ Webhook o'rnatishda xato: {e}")

# This on_shutdown function is an async handler that will be automatically called by aiohttp.
async def on_shutdown(app):
    logging.info("🛑 Bot o'chirilmoqda. Webhook o'chirilmoqda...")
    # Deletes the webhook before shutting down.
    await bot.delete_webhook()
    # Closes the bot's session to free up resources.
    await bot.session.close()
    logging.info("✅ Webhook muvaffaqiyatli o'chirildi!")

# The main entry point for the application.
def main():
    if not WEB_APP_NAME:
        logging.error("RENDER_EXTERNAL_HOSTNAME topilmadi. Webhook rejimida ishga tushirish mumkin emas.")
        return

    # Creates the aiohttp application.
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Correctly adds the webhook handler to the application router.
    app.router.add_post(WEBHOOK_PATH, SimpleRequestHandler(dispatcher=dp, bot=bot))
    
    # Uses web.run_app to start the server, which handles the entire lifecycle
    # including graceful shutdown and keeping the event loop running.
    web.run_app(app, host='0.0.0.0', port=int(os.getenv("PORT", 80)))
    logging.info("🚀 Web server da ishlamoqda...")

if __name__ == "__main__":
    main()