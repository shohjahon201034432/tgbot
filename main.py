import asyncio
import logging
import random
import sqlite3
import os
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)

# Load environment variables
API_TOKEN = os.getenv("API_TOKEN")
# ADMIN_ID is read as a string from env, so we cast it to int
ADMIN_ID = int(os.getenv("ADMIN_ID"))
BOT_USERNAME = os.getenv("BOT_USERNAME")

# Set up logging for better debugging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)


def init_db():
    """Initializes the SQLite database and creates tables if they don't exist."""
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

init_db()

# --- USER FUNCTIONS ---

def get_channels():
    """Retrieves all channel usernames from the database."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM channels")
        return [row[0] for row in cursor.fetchall()]

def add_channel(username: str):
    """Adds a new channel to the database."""
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
    """Removes a channel from the database."""
    username = username.strip()
    if not username.startswith('@'):
        username = '@' + username
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channels WHERE username=?", (username,))
        conn.commit()

def user_exists(user_id: int):
    """Checks if a user exists in the database."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None

def has_referral(user_id: int):
    """Checks if a user has already been referred by someone."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM referrals WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None

def add_user(user_id: int, username: str = None):
    """Adds a new user to the database if they don't exist."""
    if not user_exists(user_id):
        with sqlite3.connect('bot_db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (user_id, username, refs) VALUES (?,?,0)", (user_id, username))
            conn.commit()

def set_user_phone(user_id: int, phone: str):
    """Sets the phone number for a user."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
        conn.commit()

def get_user_phone(user_id: int):
    """Retrieves the phone number of a user."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else None

def get_user_info(user_id: int):
    """Retrieves a user's username and phone number."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, phone FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res if res else (None, None)

def add_referral(user_id: int, ref_id: int) -> bool:
    """Handles the referral logic and updates scores for the referrer and their parent."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        if user_id == ref_id:
            return False
        cursor.execute("SELECT ref_id FROM referrals WHERE user_id=?", (user_id,))
        if cursor.fetchone():
            return False
        
        # Add the new referral link
        cursor.execute("INSERT INTO referrals (user_id, ref_id) VALUES (?,?)", (user_id, ref_id))
        
        # Update scores for up to 2 levels
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
    """Returns the number of referrals a user has."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT refs FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        return res[0] if res else 0

def get_top_refs(limit=10):
    """Returns the top users by referral count."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, phone, refs FROM users ORDER BY refs DESC LIMIT ?", (limit,))
        return cursor.fetchall()

def get_all_users():
    """Returns all users in the database."""
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, phone, refs FROM users ORDER BY user_id")
        return cursor.fetchall()

async def is_subscribed(user_id: int):
    """Checks if a user is subscribed to all required channels."""
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

# --- MENU FUNCTIONS ---

def get_main_menu():
    """Returns the main menu keyboard for the user."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔗 Referral link", callback_data="get_ref"),
        InlineKeyboardButton("📊 Statistikam", callback_data="my_refs")
    )
    kb.add(
        InlineKeyboardButton("🏆 Top 10", callback_data="top_refs"),
        InlineKeyboardButton("ℹ️ Yordam", callback_data="help")
    )
    return kb

def get_user_display_name(username, phone, user_id):
    """Returns a formatted display name for the user."""
    if username:
        return f"@{username}"
    elif phone:
        return f"📱 {phone}"
    else:
        return f"ID: {user_id}"

# --- HANDLERS ---

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    args = message.get_args()

    add_user(user_id, username)

    # Referral logic: save pending referrer ID
    if args and args.isdigit():
        ref_id = int(args)
        if ref_id != user_id and not get_user_phone(user_id) and not has_referral(user_id):
            with sqlite3.connect('bot_db.sqlite3') as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET pending_ref_id = ? WHERE user_id=?", (ref_id, user_id))
                conn.commit()

    display_name = get_user_display_name(username, get_user_phone(user_id), user_id)

    # Welcome message
    welcome_msg = (
        "🎉 *Xush kelibsiz, {display_name}!* 🎉\n\n"
        "Bu bot orqali do'stlaringizni taklif qilib, ball to'plashingiz mumkin! 😎\n\n"
        "📋 *Qadamlar:*\n"
        "1️⃣ Kanal va guruhlarga obuna bo'ling\n"
        "2️⃣ Telefon raqamingizni yuboring\n"
        "3️⃣ Referral linkingizni do'stlaringizga ulashing!\n\n"
        "🚀 Hoziroq boshlash uchun quyidagi ko'rsatmalarga amal qiling!"
    ).format(display_name=display_name)

    await message.answer(welcome_msg, parse_mode="Markdown")

    # Check subscription
    subscribed = await is_subscribed(user_id)
    if not subscribed:
        channels = get_channels()
        if channels:
            kb = InlineKeyboardMarkup(row_width=1)
            for ch in channels:
                kb.add(InlineKeyboardButton(text=f"📢 {ch} ga obuna bo'lish", url=f"https://t.me/{ch.strip('@')}"))
            kb.add(InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub"))
            await message.answer(
                "🔗 *Avval quyidagi kanal va guruhlarga obuna bo'ling:*\n\n"
                "Obunadan so'ng *'✅ Obunani tekshirish'* tugmasini bosing! 🚀",
                parse_mode="Markdown",
                reply_markup=kb
            )
        return

    # Check phone number
    phone = get_user_phone(user_id)
    if not phone:
        phone_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        phone_kb.add(KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True))
        await message.answer(
            "📞 *Telefon raqamingizni yuboring:*\n\n"
            "Bu ro'yxatdan o'tish va referral tizimidan foydalanish uchun zarur.\n\n"
            "🔒 *Xavfsizlik:* Raqamingiz faqat adminlar uchun ko'rinadi va boshqa maqsadlarda ishlatilmaydi.",
            parse_mode="Markdown",
            reply_markup=phone_kb
        )
    else:
        # User is fully registered
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
        await message.answer(final_msg, parse_mode="Markdown", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'check_sub')
async def check_sub(call: types.CallbackQuery):
    user_id = call.from_user.id
    subscribed = await is_subscribed(user_id)
    
    if subscribed:
        await call.answer("✅ Obuna muvaffaqiyatli tasdiqlandi!")
        try:
            await call.message.delete()
        except Exception:
            pass
        
        phone = get_user_phone(user_id)
        if not phone:
            phone_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            phone_kb.add(KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True))
            await bot.send_message(
                user_id,
                "🎉 *Obuna tasdiqlandi!*\n\n"
                "📞 Endi telefon raqamingizni yuboring:\n\n"
                "🔒 *Xavfsizlik:* Raqamingiz xavfsiz saqlanadi va faqat adminlar uchun ko'rinadi.",
                parse_mode="Markdown",
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
            await bot.send_message(user_id, final_msg, parse_mode="Markdown", reply_markup=get_main_menu())
    else:
        await call.answer("❌ Hali barcha kanallarga obuna bo'lmadingiz! Iltimos, avval obuna bo'lib, keyin qayta urinib ko'ring.", show_alert=True)

@dp.message_handler(content_types=types.ContentType.CONTACT)
async def contact_handler(message: types.Message):
    user_id = message.from_user.id
    
    if message.contact is None or message.contact.user_id != user_id:
        await message.answer("🚫 Iltimos, faqat o'zingizning telefon raqamingizni yuboring.")
        return

    phone = message.contact.phone_number
    previous_phone = get_user_phone(user_id)

    # Check subscription before accepting phone
    if not await is_subscribed(user_id):
        await message.answer(
            "🚫 Avval kanal va guruhlarga obuna bo'ling!\n\n"
            "Obuna bo'lgandan keyin /start buyrug'ini qayta bosing.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    set_user_phone(user_id, phone)

    # If updating phone number
    if previous_phone:
        await message.answer("📱 Telefon raqamingiz muvaffaqiyatli yangilandi! ✅", reply_markup=ReplyKeyboardRemove())
        await message.answer("🔙 Asosiy menyuga qaytish uchun quyidagi tugmalarni ishlating:", reply_markup=get_main_menu())
        return

    # Handle pending referral
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
                    await bot.send_message(
                        ref_id, 
                        "🎉 *Yangi referral!*\n\n"
                        "Sizga yangi referral qo'shildi! Ballaringiz +1 ga oshdi! 🚀\n\n"
                        "📊 Statistikangizni ko'rish uchun menyudan foydalaning.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logging.error(f"Error notifying referrer {ref_id}: {e}")

    # Success message
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
    
    await message.answer(success_msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await asyncio.sleep(1)
    await message.answer("🚀 *Asosiy menyudan foydalaning:*", parse_mode="Markdown", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'get_ref')
async def callback_get_ref(call: types.CallbackQuery):
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
    await bot.send_message(user_id, ref_msg, parse_mode="Markdown", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'my_refs')
async def callback_my_refs(call: types.CallbackQuery):
    user_id = call.from_user.id
    refs = get_user_refs(user_id)
    username, phone = get_user_info(user_id)
    display_name = get_user_display_name(username, phone, user_id)
    
    # Get user's rank
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
    await bot.send_message(user_id, stats_msg, parse_mode="Markdown", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'top_refs')
async def callback_top_refs(call: types.CallbackQuery):
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
    await bot.send_message(call.from_user.id, msg, parse_mode="Markdown", reply_markup=get_main_menu())

@dp.callback_query_handler(lambda c: c.data == 'help')
async def callback_help(call: types.CallbackQuery):
    help_msg = (
        "ℹ️ *Yordam bo'limi*\n\n"
        "Bu bot orqali do'stlaringizni taklif qilib ball to'plashingiz mumkin! 😎\n\n"
        "🔍 *Bot qanday ishlaydi?*\n\n"
        "1️⃣ *Ro'yxatdan o'tish:*\n"
        "   • /start buyrug'ini bosing\n"
        "   • Kanal va guruhlarga obuna bo'ling\n"
        "   • Telefon raqamingizni yuboring\n\n"
        "2️⃣ *Referral tizimi:*\n"
        "   • Sizning maxsus linkingizni oling\n"
        "   • Do'stlaringizga ulashing\n"
        "   • Ular ro'yxatdan o'tganda ball oling\n\n"
        "3️⃣ *Ball tizimi:*\n"
        "   • To'g'ridan-to'g'ri taklif: +1 ball\n"
        "   • Ikkinchi darajadagi taklif: +1 ball\n\n"
        "🎯 *Maqsad:* Ko'proq ball to'plang va top reytingda bo'ling!\n\n"
        "📞 *Yordam kerakmi?* Admin: @admin\n\n"
        "🚀 *Muvaffaqiyatlar tilaymiz!*"
    )
    
    await call.answer()
    await bot.send_message(call.from_user.id, help_msg, parse_mode="Markdown", reply_markup=get_main_menu())

# --- ADMIN COMMANDS ---

@dp.message_handler(commands=['addchannel'])
async def addchannel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    args = message.get_args()
    if not args:
        await message.answer("📥 Iltimos, kanal yoki guruh username'ini kiriting.\n\nMisol: `/addchannel @kanalim`", parse_mode="Markdown")
        return
    success = add_channel(args)
    if success:
        await message.answer(f"✅ Kanal/guruh `{args}` muvaffaqiyatli qo'shildi!")
    else:
        await message.answer(f"⚠️ Kanal/guruh `{args}` allaqachon ro'yxatda mavjud.")

@dp.message_handler(commands=['removechannel'])
async def removechannel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    args = message.get_args()
    if not args:
        await message.answer("📥 Iltimos, kanal yoki guruh username'ini kiriting.\n\nMisol: `/removechannel @kanalim`", parse_mode="Markdown")
        return
    remove_channel(args)
    await message.answer(f"🗑️ Kanal/guruh `{args}` ro'yxatdan olib tashlandi!", parse_mode="Markdown")

@dp.message_handler(commands=['channels'])
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
    await message.answer(msg, parse_mode="Markdown")

@dp.message_handler(commands=['random'])
async def random_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    args = message.get_args()
    try:
        n = int(args.strip())
    except (ValueError, IndexError):
        await message.answer("📥 Iltimos, to'g'ri son kiriting.\n\nMisol: `/random 5`", parse_mode="Markdown")
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
    await message.answer(msg, parse_mode="Markdown")

@dp.message_handler(commands=['allusers'])
async def allusers_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    users = get_all_users()
    if not users:
        await message.answer("📋 Hozircha ro'yxatdan o'tgan foydalanuvchilar yo'q.")
        return
    
    msg = "📋 *Barcha foydalanuvchilar:*\n\n"
    for u in users:
        user_id, username, phone, refs = u
        display_name = get_user_display_name(username, phone, user_id)
        status = "✅" if phone else "❌"
        msg += f"{status} {display_name} | 🏆 {refs} ball\n"
    
    if len(msg) > 4000:
        msgs = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
        for m in msgs:
            await message.answer(m, parse_mode="Markdown")
    else:
        await message.answer(msg, parse_mode="Markdown")

@dp.message_handler(commands=['stats'])
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
    await message.answer(stats_msg, parse_mode="Markdown")

@dp.message_handler(commands=['broadcast'])
async def broadcast_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔐 Faqat adminlar uchun!")
        return
    
    msg_text = message.text[len('/broadcast '):].strip()
    
    if not msg_text:
        await message.answer("📥 Foydalanish: `/broadcast xabar matni`", parse_mode="Markdown")
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
            await bot.send_message(user_id, msg_text)
            success += 1
        except Exception:
            fail += 1
            continue
    
    await message.answer(
        f"📢 Xabar yuborildi!\n\n"
        f"✅ Muvaffaqiyatli: {success}\n"
        f"❌ Xatolik: {fail}"
    )

# --- DEFAULT HANDLER ---

@dp.message_handler()
async def default_handler(message: types.Message):
    user_id = message.from_user.id
    phone = get_user_phone(user_id)
    
    if not phone:
        await message.answer(
            "🚫 Siz hali ro'yxatdan o'tmadingiz!\n\n"
            "Iltimos, /start buyrug'ini bosing va ro'yxatdan o'ting. 😊"
        )
    else:
        await message.answer(
            "🤖 *Noto'g'ri buyruq!*\n\n"
            "Quyidagi tugmalardan foydalaning yoki /start buyrug'ini bosing:",
            parse_mode="Markdown",
            reply_markup=get_main_menu()
        )

# --- ERROR HANDLER ---

@dp.errors_handler()
async def errors_handler(update, exception):
    logging.error(f"Update {update} caused error {exception}")
    return True

# --- STARTUP AND SHUTDOWN ---

async def on_startup(dp):
    logging.info("🚀 Bot ishga tushdi va foydalanuvchilarni kutmoqda!")
    try:
        await bot.send_message(ADMIN_ID, "🚀 *Bot muvaffaqiyatli ishga tushdi!*", parse_mode="Markdown")
    except Exception:
        pass

async def on_shutdown(dp):
    logging.info("Bot yopilmoqda...")
    try:
        await bot.send_message(ADMIN_ID, "🛑 *Bot yopildi.*", parse_mode="Markdown")
    except Exception:
        pass
    await dp.storage.close()
    await dp.storage.wait_closed()
    await bot.close()
    logging.info("Bot sessiyasi yopildi.")

if __name__ == '__main__':
    print("Bot ishga tushirilmoqda...")
    print(f"📱 Bot username: @{BOT_USERNAME}")
    print(f"👨‍💻 Admin ID: {ADMIN_ID}")
    print("⏳ Iltimos kuting...")
    
    # This is the correct way to start the bot
    try:
        executor.start_polling(
            dp, 
            skip_updates=True, 
            on_startup=on_startup, 
            on_shutdown=on_shutdown
        )
    except KeyboardInterrupt:
        print("\n🛑 Bot to'xtatildi (Ctrl+C)")
    except Exception as e:
        logging.error(f"❌ Bot ishga tushmadi: {e}")
        print(f"❌ Xatolik: {e}")