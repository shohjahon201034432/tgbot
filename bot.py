import asyncio
import logging
import random
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.utils import executor

API_TOKEN = os.getenv("7734446929:AAEMYPupJ72QnCYMKYGo9TOg6RDXR9HxK1E") # Bot token
ADMIN_ID = int(os.getenv("5718626045"))
BOT_USERNAME = os.getenv("@madridasia_bot") # Replace with your actual bot username

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# --- DB SETUP ---

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

# --- FOYDALANUVCHI FUNKSIYALARI ---

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

def add_user(user_id: int):
    if not user_exists(user_id):
        with sqlite3.connect('bot_db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (user_id, refs) VALUES (?,0)", (user_id,))
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

def add_referral(user_id: int, ref_id: int) -> bool:
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        if user_id == ref_id:
            return False
        cursor.execute("SELECT ref_id FROM referrals WHERE user_id=?", (user_id,))
        if cursor.fetchone():
            return False
        cursor.execute("INSERT INTO referrals (user_id, ref_id) VALUES (?,?)", (user_id, ref_id))
        # Multi-level referral (up to 2 levels, +1 point each)
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
        cursor.execute("SELECT user_id, refs FROM users ORDER BY refs DESC LIMIT ?", (limit,))
        return cursor.fetchall()

def get_all_users():
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, phone, refs FROM users ORDER BY user_id")
        return cursor.fetchall()

async def is_subscribed(user_id: int):
    channels = get_channels()
    if not channels:
        return True  # If no channels, consider subscribed
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logging.error(f"Error checking subscription for {ch}: {e}")
            return False
    return True

# --- HANDLERLAR ---

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    args = message.get_args()

    add_user(user_id)

    if args and args.isdigit():
        ref_id = int(args)
        if ref_id != user_id and not get_user_phone(user_id) and not has_referral(user_id):
            with sqlite3.connect('bot_db.sqlite3') as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET pending_ref_id = ? WHERE user_id=?", (ref_id, user_id))
                conn.commit()

    await message.answer("Xush kelibsiz! Botdan foydalanish uchun quyidagi qadamlarni bajaring:")

    subscribed = await is_subscribed(user_id)
    if not subscribed:
        channels = get_channels()
        kb = InlineKeyboardMarkup()
        for ch in channels:
            kb.add(InlineKeyboardButton(text=f"Obuna bo'ling: {ch}", url=f"https://t.me/{ch.strip('@')}"))
        kb.add(InlineKeyboardButton(text="Obunani tasdiqlash", callback_data="check_sub"))
        await message.answer(
            "Avval quyidagi kanal va guruhlarga obuna bo'ling. Obuna bo'lgandan so'ng, 'Obunani tasdiqlash' tugmasini bosing.\n\n"
            "Bu jarayon botning to'liq funksiyalaridan foydalanish uchun majburiydir.",
            reply_markup=kb
        )
        return

    phone = get_user_phone(user_id)
    if not phone:
        phone_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        phone_kb.add(KeyboardButton("Telefon raqamimni yuborish", request_contact=True))
        await message.answer(
            "Endi telefon raqamingizni yuboring. Bu ro'yxatdan o'tish va referral tizimidan foydalanish uchun zarur.\n\n"
            "Raqamingiz xavfsiz saqlanadi va faqat admin tomonidan ko'riladi.",
            reply_markup=phone_kb
        )
    else:
        ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("Mening referral linkim", callback_data="get_ref"))
        kb.add(InlineKeyboardButton("Mening referallarim", callback_data="my_refs"))
        kb.add(InlineKeyboardButton("Top referrallar", callback_data="top_refs"))
        await message.answer(
            f"Tabriklaymiz! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.\n\n"
            f"Sizning referral linkingiz: {ref_link}\n\n"
            "Quyidagi tugmalar orqali bot funksiyalaridan foydalaning:",
            reply_markup=kb
        )

@dp.callback_query_handler(lambda c: c.data == 'check_sub')
async def check_sub(call: types.CallbackQuery):
    user_id = call.from_user.id
    subscribed = await is_subscribed(user_id)
    if subscribed:
        await call.answer("Obuna muvaffaqiyatli tasdiqlandi!")
        try:
            await call.message.delete()
        except:
            pass
        phone = get_user_phone(user_id)
        if not phone:
            phone_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            phone_kb.add(KeyboardButton("Telefon raqamimni yuborish", request_contact=True))
            await bot.send_message(
                user_id,
                "Obuna tasdiqlandi! Endi telefon raqamingizni yuboring. Bu ro'yxatdan o'tish va referral tizimidan foydalanish uchun zarur.\n\n"
                "Raqamingiz xavfsiz saqlanadi va faqat admin tomonidan ko'riladi.",
                reply_markup=phone_kb
            )
        else:
            ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("Mening referral linkingiz", callback_data="get_ref"))
            kb.add(InlineKeyboardButton("Mening referallarim", callback_data="my_refs"))
            kb.add(InlineKeyboardButton("Top referral qilganlar", callback_data="top_refs"))
            await bot.send_message(
                user_id,
                f"Tabriklaymiz! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.\n\n"
                f"Sizning referral linkingiz: {ref_link}\n\n"
                "Quyidagi tugmalar orqali bot funksiyalaridan foydalaning:",
                reply_markup=kb
            )
    else:
        await call.answer("Hali barcha kanal va guruhlarga obuna bo'lmadingiz! Iltimos, obuna bo'ling va qayta tasdiqlang.", show_alert=True)

@dp.message_handler(content_types=types.ContentType.CONTACT)
async def contact_handler(message: types.Message):
    user_id = message.from_user.id
    if message.contact is None or message.contact.user_id != user_id:
        await message.answer("Iltimos, faqat o'zingizning telefon raqamingizni yuboring.")
        return

    phone = message.contact.phone_number
    previous_phone = get_user_phone(user_id)

    set_user_phone(user_id, phone)

    if previous_phone:
        await message.answer("Telefon raqamingiz muvaffaqiyatli yangilandi.", reply_markup=ReplyKeyboardRemove())
        return

    # Yangi ro'yxatdan o'tish - obuna va referralni tekshir
    if not await is_subscribed(user_id):
        set_user_phone(user_id, None)  # Revert
        await message.answer("Avval kanal va guruhlarga obuna bo'ling va /start buyrug'ini bosing!")
        return

    # Referralni qo'sh
    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pending_ref_id FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            ref_id = row[0]
            if add_referral(user_id, ref_id):
                cursor.execute("UPDATE users SET pending_ref_id = NULL WHERE user_id=?", (user_id,))
                conn.commit()
                # Notify referrer
                await bot.send_message(ref_id, "Sizga yangi referral qo'shildi! Ballaringiz oshdi.")

    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("Mening referral linkingiz", callback_data="get_ref"))
    kb.add(InlineKeyboardButton("Mening referallarim", callback_data="my_refs"))
    kb.add(InlineKeyboardButton("Top referral qilganlar", callback_data="top_refs"))
    await message.answer(
        f"Rahmat! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.\n\n"
        f"Sizning referral linkingiz: {ref_link}\n\n"
        "Quyidagi tugmalar orqali bot funksiyalaridan foydalaning:",
        reply_markup=kb
    )
    await message.answer("Ro'yxatdan o'tish tugallandi.", reply_markup=ReplyKeyboardRemove())

@dp.callback_query_handler(lambda c: c.data == 'get_ref')
async def callback_get_ref(call: types.CallbackQuery):
    user_id = call.from_user.id
    phone = get_user_phone(user_id)
    if not phone:
        await call.answer("Avval telefon raqamingizni yuboring.", show_alert=True)
        return
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    await call.answer()
    await bot.send_message(user_id, f"Sizning referral linkingiz:\n{ref_link}")

@dp.callback_query_handler(lambda c: c.data == 'my_refs')
async def callback_my_refs(call: types.CallbackQuery):
    user_id = call.from_user.id
    refs = get_user_refs(user_id)
    await call.answer()
    await bot.send_message(user_id, f"Sizning referral ballaringiz (to'g'ridan-to'g'ri va bilvosita): {refs}")

@dp.callback_query_handler(lambda c: c.data == 'top_refs')
async def callback_top_refs(call: types.CallbackQuery):
    top = get_top_refs(10)
    if not top:
        await call.answer("Hech kim referral qilmagan.", show_alert=True)
        return
    msg = "Eng ko'p referral ball to'plaganlar reytingi:\n"
    for idx, (user_id, refs) in enumerate(top, start=1):
        msg += f"{idx}. User ID: {user_id} — {refs} ball\n"
    await call.answer()
    await bot.send_message(call.from_user.id, msg)

# --- ADMIN COMMANDLAR ---

@dp.message_handler(commands=['addchannel'])
async def addchannel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Faqat adminlar uchun.")
        return
    args = message.get_args()
    if not args:
        await message.answer("Iltimos, kanal yoki guruh username (@kanal) kiriting.")
        return
    success = add_channel(args)
    if success:
        await message.answer(f"Kanal/guruh {args} qo'shildi!")
    else:
        await message.answer(f"Kanal/guruh {args} allaqachon mavjud.")

@dp.message_handler(commands=['removechannel'])
async def removechannel_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Faqat adminlar uchun.")
        return
    args = message.get_args()
    if not args:
        await message.answer("Iltimos, kanal yoki guruh username (@kanal) kiriting.")
        return
    remove_channel(args)
    await message.answer(f"Kanal/guruh {args} o'chirildi!")

@dp.message_handler(commands=['random'])
async def random_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Faqat adminlar uchun.")
        return
    args = message.get_args()
    try:
        n = int(args.strip())
    except:
        await message.answer("Iltimos, to'g'ri son kiriting, masalan: /random 5")
        return

    with sqlite3.connect('bot_db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        all_users = [row[0] for row in cursor.fetchall()]
    if n > len(all_users):
        await message.answer(f"Botda faqat {len(all_users)} ta foydalanuvchi bor.")
        return

    chosen = random.sample(all_users, n)
    msg = f"Random tanlangan {n} ta foydalanuvchi:\n"
    for u in chosen:
        msg += f"{u}\n"
    await message.answer(msg)

@dp.message_handler(commands=['allusers'])
async def allusers_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Bu komanda faqat adminlar uchun.")
        return
    users = get_all_users()
    msg = "Botga ro'yxatdan o'tgan foydalanuvchilar:\n"
    for u in users:
        msg += f"ID: {u[0]} | Phone: {u[1]} | Ballar: {u[2]}\n"
    await message.answer(msg)

# --- DEFAULT HANDLER ---

@dp.message_handler()
async def default_handler(message: types.Message):
    await message.answer("Iltimos, /start buyrug'ini bosing va ko'rsatmalarga amal qiling.")

# --- BOTNI ISHGA TUSHURISH ---

async def on_shutdown(dp):
    print("Bot to'xtatilmoqda...")

if __name__ == '__main__':
    print("Bot ishga tushmoqda...")
    executor.start_polling(dp, skip_updates=True, on_shutdown=on_shutdown)