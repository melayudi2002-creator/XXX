import os
import time
import uuid
import io
import base64
import asyncio
from datetime import datetime, timezone, timedelta
import pytz

from PIL import Image, ImageFile
from concurrent.futures import ThreadPoolExecutor
from motor.motor_asyncio import AsyncIOMotorClient
import certifi 
from dotenv import load_dotenv
import dns.resolver

# Impor Utils (Generator & Payment)
from utils.pakasir import create_payment, cancel_payment, check_payment_status
from utils.motion import submit_motion_task, poll_motion_task
from utils.vivago import run_vivago_pipeline
from utils.clipfly import (
    login_clipfly, compress_image_sync, upload_to_clipfly,
    create_material, submit_text_to_video_task, submit_video_task, 
    poll_clipfly_task
)

# Impor Admin Commands
from utils.admin import (
    admin_cmd_list, admin_add_member, admin_new_akses, admin_list_member,
    admin_delete_akses, admin_list_harga, admin_delete_listakses,
    admin_broadcast, admin_broadcast_hari, admin_broadcast_lifetime, admin_perbarui_memberlifetime,
    handle_delete_listakses_callback, handle_lifetime_file
)

load_dotenv()
Image.MAX_IMAGE_PIXELS = None  
ImageFile.LOAD_TRUNCATED_IMAGES = True

# ==========================================
# GLOBAL REQUEST LOGGER (Hanya untuk Log)
# ==========================================
import requests
import aiohttp
import aiofiles

_old_requests_request = requests.Session.request

def _logged_requests_request(self, method, url, **kwargs):
    is_tg = "api.telegram.org" in str(url)
    if not is_tg:
        print(f"🌐 [SYNC REQ] -> {method.upper()} {url}")
    response = _old_requests_request(self, method, url, **kwargs)
    if not is_tg:
        print(f"✅ [SYNC RES] <- {method.upper()} {url} | Status: {response.status_code}")
    return response

requests.Session.request = _logged_requests_request

_old_aiohttp_request = aiohttp.ClientSession._request

async def _logged_aiohttp_request(self, method, url, *args, **kwargs):
    is_tg = "api.telegram.org" in str(url)
    if not is_tg:
        print(f"⚡ [ASYNC REQ] -> {method.upper()} {url}")
    response = await _old_aiohttp_request(self, method, url, *args, **kwargs)
    if not is_tg:
        print(f"✅ [ASYNC RES] <- {method.upper()} {url} | Status: {response.status}")
    return response

aiohttp.ClientSession._request = _logged_aiohttp_request

# ==========================================
# KONFIGURASI BOT & TELEGRAM
# ==========================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ConversationHandler, ContextTypes,
)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) 
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
API_KEY = os.getenv("PAKASIR_API_KEY")
PROJECT = os.getenv("PAKASIR_PROJECT")
BOT_NAME = os.environ.get("BOT_NAME", "AI Assistant")
LOG_CHANNEL = os.getenv("LOG_CHANNEL")

JKT = pytz.timezone('Asia/Jakarta')
GLOBAL_TASK_SEMAPHORE = asyncio.Semaphore(1000)
active_async_tasks = {}

# ==========================================
# KONEKSI MONGODB TINGKAT LANJUT
# ==========================================
try:
    dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
    dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4', '1.1.1.1']
    
    mongo_client = AsyncIOMotorClient(
        MONGO_URI, 
        serverSelectionTimeoutMS=5000,
        tlsCAFile=certifi.where(),
        maxPoolSize=100
    )
    db = mongo_client["singebutai_db"] 
    users_col = db["users"]        
    orders_col = db["orders"]
    tasks_col = db["tasks"]
    pricing_col = db["pricing"] 
    print("[+] Database Berhasil Terhubung (Pool & SSL Active)")
except Exception as e:
    print(f"[-] MongoDB Connection Failed: {e}")

# ==========================================
# STATE MESIN
# ==========================================
CHOOSING_FEATURE = 0
CONFIGURING_DASHBOARD = 1
WAITING_IMAGE = 2
WAITING_MULTIPLE_IMAGES = 3
WAITING_PROMPT = 4
WAITING_CLIPFLY_LOGIN = 5 
WAITING_MOTION_IMAGE = 6
WAITING_MOTION_URL = 7
CHOOSING_MOTION_MODE = 8
WAITING_LIFETIME_FILE = 99

IMAGE_MODEL_NAMES = {
    "nano_banana": "Nano Banana Pro", "grok_imagine": "Grok Imagine",
    "flux_2_pro": "Flux 2 Pro", "gpt_image_2": "GPT Image 2.0",
    "vivago_pro": "Vivago Pro", "seedream_3": "Seedream 3.0"
}

# ==========================================
# MANAJEMEN AKSES & UTILS
# ==========================================
def is_access_valid(user):
    if not user or 'access_expired_at' not in user: return False
    exp = user['access_expired_at']
    if exp == "lifetime": return True
    
    if isinstance(exp, datetime):
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < exp
    return False

def cleanup_temp_files(user_data):
    if 'img_path' in user_data and os.path.exists(user_data['img_path']):
        try: os.remove(user_data['img_path'])
        except: pass
    if 'images' in user_data:
        for img_dict in user_data['images']:
            if 'path' in img_dict and os.path.exists(img_dict['path']):
                try: os.remove(img_dict['path'])
                except: pass

async def send_new_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode="Markdown", photo=None):
    chat_id = update.effective_chat.id
    if 'last_bot_msg_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=context.user_data['last_bot_msg_id'])
        except Exception: pass
    if update.callback_query:
        try: await update.callback_query.answer()
        except Exception: pass

    if photo:
        new_msg = await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        new_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        
    context.user_data['last_bot_msg_id'] = new_msg.message_id
    return new_msg

def get_main_keyboard(user_id, is_allowed, is_lifetime):
    if not is_allowed:
        keyboard = [
            [InlineKeyboardButton("💳 Beli Akses", callback_data="menu_buy")],
            [InlineKeyboardButton("❌ Tutup", callback_data="cancel")]
        ]
        return InlineKeyboardMarkup(keyboard)

    btn_video = InlineKeyboardButton("🎥 VIDEO GENERATION", callback_data="menu_video") if is_lifetime else InlineKeyboardButton("🔒 VIDEO (Lifetime Only)", callback_data="locked_lifetime")
    
    keyboard = [
        [InlineKeyboardButton("ℹ️ INFORMASI PENGGUNAAN", url="https://t.me/getapikey5")],
        [InlineKeyboardButton("🎨 IMAGE GENERATION", callback_data="menu_image"), btn_video],
        [InlineKeyboardButton("🕺 MOTION CONTROL", callback_data="menu_motion")],
        [InlineKeyboardButton("💳 Beli Akses", callback_data="menu_buy"), InlineKeyboardButton("📋 TASK LIST", callback_data="menu_task_list")],
        [InlineKeyboardButton("❌ Tutup", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_image_dashboard_keyboard(ud):
    f = ud.get('feature_type', 't2i'); r = ud.get('ratio', '9:16'); m = ud.get('image_model_choice', 'vivago_pro')
    kb = [
        [InlineKeyboardButton(f"{'✅ ' if f=='t2i' else ''}📝 Text To Image", callback_data="set_feat_t2i"), InlineKeyboardButton(f"{'✅ ' if f=='i2i' else ''}🖼️ Image To Image", callback_data="set_feat_i2i")],
        [InlineKeyboardButton(f"{'✅ ' if f=='combo' else ''}🖼️ Image Combination", callback_data="set_feat_combo")]
    ]
    if f in ['t2i', 'combo', 'i2i']:
        kb.append([InlineKeyboardButton(f"{'✅ ' if r=='9:16' else ''}📐 9:16", callback_data="set_ratio_9:16"), InlineKeyboardButton(f"{'✅ ' if r=='16:9' else ''}📐 16:9", callback_data="set_ratio_16:9"), InlineKeyboardButton(f"{'✅ ' if r=='1:1' else ''}📐 1:1", callback_data="set_ratio_1:1")])
    kb.append([InlineKeyboardButton("⚙️ --- SELECT MODEL --- ⚙️", callback_data="ignore_btn")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='nano_banana' else ''}🍌 Nano Banana Pro", callback_data="set_img_model_nano_banana"), InlineKeyboardButton(f"{'✅ ' if m=='grok_imagine' else ''}👁️ Grok Imagine", callback_data="set_img_model_grok_imagine")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='flux_2_pro' else ''}⚡ Flux 2 Pro", callback_data="set_img_model_flux_2_pro"), InlineKeyboardButton(f"{'✅ ' if m=='gpt_image_2' else ''}🤖 GPT Image 2.0", callback_data="set_img_model_gpt_image_2")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='vivago_pro' else ''}🟣 Vivago Pro", callback_data="set_img_model_vivago_pro"), InlineKeyboardButton(f"{'✅ ' if m=='seedream_3' else ''}🌊 Seedream 3.0", callback_data="set_img_model_seedream_3")])
    kb.append([InlineKeyboardButton("➡️ Lanjutkan ke Prompt", callback_data="continue_task")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)

def get_video_dashboard_keyboard(ud):
    f = ud.get('feature_type', 't2v'); r = ud.get('ratio', '9:16'); m = ud.get('model_choice', 'pixverse_v6'); a = ud.get('audio', False)
    kb = [
        [InlineKeyboardButton(f"{'✅ ' if f=='t2v' else ''}🎬 Text To Video", callback_data="set_feat_t2v"), InlineKeyboardButton(f"{'✅ ' if f=='i2v' else ''}🎞️ Image To Video", callback_data="set_feat_i2v")]
    ]
    if f == 't2v':
        kb.append([InlineKeyboardButton(f"{'✅ ' if r=='9:16' else ''}📐 9:16", callback_data="set_ratio_9:16"), InlineKeyboardButton(f"{'✅ ' if r=='16:9' else ''}📐 16:9", callback_data="set_ratio_16:9")])
    if m not in ['lumen']:
        kb.append([InlineKeyboardButton(f"{'✅ ' if not a else ''}🔊 Tanpa Suara", callback_data="set_audio_no"), InlineKeyboardButton(f"{'✅ ' if a else ''}🔊 Dengan Suara", callback_data="set_audio_yes")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='lumen' else ''}Lumen 2.3", callback_data="set_model_lumen"), InlineKeyboardButton(f"{'✅ ' if m=='seedance' else ''}Seedance 1.5", callback_data="set_model_seedance")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='pixverse_v6' else ''}PixVerse V6", callback_data="set_model_pixverse_v6"), InlineKeyboardButton(f"{'✅ ' if m=='kling_o1' else ''}Kling O1", callback_data="set_model_kling_o1")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='google_veo' else ''}Google Veo", callback_data="set_model_google_veo"), InlineKeyboardButton(f"{'✅ ' if m=='xai_grok' else ''}xAI GROK", callback_data="set_model_xai_grok")])
    kb.append([InlineKeyboardButton(f"{'✅ ' if m=='seedance_2' else ''}Seedance 2.0", callback_data="set_model_seedance_2"), InlineKeyboardButton(f"{'✅ ' if m=='wan_2_7' else ''}WAN 2.7", callback_data="set_model_wan_2_7")])
    kb.append([InlineKeyboardButton("➡️ Lanjutkan ke Prompt", callback_data="continue_task")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)

# ==========================================
# PEMBAYARAN (BELI AKSES)
# ==========================================
async def handle_buy_akses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = await users_col.find_one({"user_id": chat_id})
    if user and user.get("access_expired_at") == "lifetime":
        await send_new_menu(update, context, "⛔ **Anda sudah memiliki Akses LIFETIME!**", parse_mode="Markdown")
        return CHOOSING_FEATURE

    packages = await pricing_col.find({}).to_list(length=None)
    if not packages:
        await send_new_menu(update, context, "⚠️ _Sistem langganan sedang dalam pemeliharaan._", parse_mode="Markdown")
        return CHOOSING_FEATURE
        
    keyboard = []
    for pkg in packages:
        pkg_days = pkg.get('days', 30)
        hari = "LIFETIME" if pkg_days == "lifetime" else f"{pkg_days} Hari"
        keyboard.append([InlineKeyboardButton(f"📦 {hari} - Rp {pkg.get('price', 0):,}", callback_data=f"buy_pkg_{pkg['_id']}")])
    keyboard.append([InlineKeyboardButton("⬅️ Kembali", callback_data="main_menu")])
    
    await send_new_menu(update, context, "💳 **Pilih Paket Akses:**\n_Pembayaran otomatis via QRIS. Akses otomatis ditambahkan setelah lunas._", InlineKeyboardMarkup(keyboard), "Markdown")
    return CHOOSING_FEATURE

async def process_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    chat_id = query.message.chat_id
    from bson.objectid import ObjectId
    pkg_id = query.data.replace("buy_pkg_", "")
    
    pkg = await pricing_col.find_one({"_id": ObjectId(pkg_id)})
    if not pkg: return await context.bot.send_message(chat_id, "⚠️ Paket tidak ditemukan.")
    
    price = pkg.get('price', 0)
    success, order_id, payment, err = await asyncio.to_thread(create_payment, price, PROJECT, API_KEY)
    if not success: return await context.bot.send_message(chat_id, f"❌ Gagal membuat pembayaran: {err}")
    try: await query.message.delete()
    except: pass

    import qrcode
    buffer = io.BytesIO()
    qrcode.make(payment).save(buffer, format="PNG")
    buffer.seek(0)
    
    msg = await context.bot.send_photo(
        chat_id=chat_id, photo=buffer,
        caption=f"💳 **Menunggu Pembayaran QRIS**\n\n🧾 **Order ID**: `{order_id}`\n💰 **Tagihan**: `Rp {price:,}`\n⏳ _Silakan scan QRIS. Batas 5 Menit_",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batalkan Pesanan", callback_data=f"cancel_order_{order_id}")]]),
        parse_mode="Markdown"
    )

    username = query.from_user.username or "TanpaUsername"
    pkg_days = pkg.get('days', 30)
    
    order_doc = {"order_id": order_id, "chat_id": chat_id, "username": username, "amount": price, "days": pkg_days, "msg_id": msg.message_id, "status": "pending", "created_at": time.time()}
    await orders_col.insert_one(order_doc)
    asyncio.create_task(cek_pembayaran_loop(context.bot, order_doc))

async def cek_pembayaran_loop(bot, order_data):
    order_id = order_data["order_id"]
    chat_id = order_data["chat_id"]
    days_to_add = order_data["days"]
    start_time = order_data["created_at"]
    msg_id = order_data.get("msg_id") 
    
    while True:
        curr_order = await orders_col.find_one({"order_id": order_id})
        if not curr_order or curr_order["status"] != "pending": return
        
        if time.time() - start_time > 300:
            await asyncio.to_thread(cancel_payment, order_data["amount"], order_id, PROJECT, API_KEY)
            await orders_col.delete_one({"order_id": order_id})
            if msg_id:
                try: await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except: pass
            try: await bot.send_message(chat_id, f"❌ **Waktu Habis!** Pembayaran dibatalkan.", parse_mode="Markdown")
            except: pass
            return

        is_completed = await asyncio.to_thread(check_payment_status, order_data["amount"], order_id, PROJECT, API_KEY)
        if is_completed:
            await orders_col.delete_one({"order_id": order_id})
            if msg_id:
                try: await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except: pass

            user = await users_col.find_one({"user_id": chat_id})
            if days_to_add == "lifetime": new_expire = "lifetime"
            else:
                curr_expire = user.get("access_expired_at") if user else None
                now = datetime.now(timezone.utc)
                if curr_expire and isinstance(curr_expire, datetime):
                    if curr_expire.tzinfo is None: curr_expire = curr_expire.replace(tzinfo=timezone.utc)
                    new_expire = (curr_expire + timedelta(days=days_to_add)) if curr_expire > now else (now + timedelta(days=days_to_add))
                else: new_expire = now + timedelta(days=days_to_add)

            await users_col.update_one(
                {"user_id": chat_id}, 
                {"$set": {"access_expired_at": new_expire}, "$setOnInsert": {"joined_at": datetime.now(JKT).strftime('%Y-%m-%d %H:%M:%S')}}, 
                upsert=True
            )
            try: await bot.send_message(chat_id, "🎉 **PEMBAYARAN BERHASIL!** Akses Anda telah ditambahkan.", parse_mode="Markdown")
            except: pass

            if LOG_CHANNEL:
                try: await bot.send_message(chat_id=LOG_CHANNEL, text=f"💰 **Pembayaran berhasil**\n🆔 `{chat_id}`\n👤 @{order_data.get('username', 'Unknown')}\n📦 {days_to_add} Hari\n💵 Rp {order_data['amount']:,}")
                except: pass
            return
            
        await asyncio.sleep(5)

async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = query.data.replace("cancel_order_", "")
    order = await orders_col.find_one({"order_id": order_id})
    if order:
        await asyncio.to_thread(cancel_payment, order["amount"], order_id, PROJECT, API_KEY)
        await orders_col.delete_one({"order_id": order_id})
        try: await query.answer("Pesanan berhasil dibatalkan.", show_alert=True)
        except: pass
        try: await query.message.delete()
        except: pass

# ==========================================
# GENERATION ENGINE & BACKGROUND TASKS
# ==========================================
async def send_media_result(bot, chat_id, status_msg_id, task_title, model_name, processing_time, result_url, is_video):
    msg = f"✅ <b>{task_title} Successfully Generated!</b>\n🤖 <b>Model:</b> {model_name}\n⏱ <b>Processing Time:</b> {processing_time}"
    
    if task_title == "Motion Control":
        try: await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
        except: pass
        await bot.send_message(chat_id=chat_id, text=f"{msg}\n\n🔗 <b>Result Link:</b>\n{result_url}", parse_mode="HTML")
        return

    try: await bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"📥 *Mengunduh media dari server...*", parse_mode="Markdown")
    except: pass
    
    try:
        timeout = aiohttp.ClientTimeout(total=3600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(result_url) as resp:
                if resp.status == 200:
                    media_bytes = await resp.read()
                    try: await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
                    except: pass
                    
                    if not is_video: await bot.send_photo(chat_id=chat_id, photo=media_bytes, caption=msg, parse_mode="HTML", read_timeout=3600, write_timeout=3600)
                    else: await bot.send_video(chat_id=chat_id, video=media_bytes, caption=msg, parse_mode="HTML", read_timeout=3600, write_timeout=3600)
                else: raise Exception("Gagal mengunduh file.")
    except Exception as e:
        try: await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
        except: pass
        err_msg = str(e).lower()
        if "timed out" not in err_msg and "timeout" not in err_msg:
            await bot.send_message(chat_id=chat_id, text=f"❌ Gagal mengirim media.\n\n{msg}\n\nLink: {result_url}", parse_mode="HTML", disable_web_page_preview=True)

async def background_generate_task(chat_id: int, status_msg_id: int, context: ContextTypes.DEFAULT_TYPE, task_data: dict):
    async with GLOBAL_TASK_SEMAPHORE:
        start_time = time.time()
        task_id = task_data['task_id']
        f_type = task_data.get('feature_type')
        prompt = task_data.get('prompt', '')
        ratio = task_data.get('ratio', '9:16')
        is_video = f_type in ['t2v', 'i2v', 'motion']
        
        if 'img_path' in task_data and os.path.exists(task_data['img_path']):
            async with aiofiles.open(task_data['img_path'], 'rb') as f: task_data['img_bytes'] = await f.read()
        if 'images' in task_data:
            for img_dict in task_data['images']:
                if 'path' in img_dict and os.path.exists(img_dict['path']):
                    async with aiofiles.open(img_dict['path'], 'rb') as f: img_dict['bytes'] = await f.read()
                    
        try:
            if f_type == 'motion':
                await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"⏳ *[Task {task_id}]*\n_Uploading & Rendering Motion Video..._", parse_mode="Markdown")
                uid, df_task_id = await submit_motion_task(task_data['img_bytes'], task_data['img_name'], task_data['tiktok_url'], replace_background=task_data.get('replace_background', False))
                if not uid or not df_task_id: raise Exception("Gagal memproses video Motion (Link/Server down).")
                status, result_url = await poll_motion_task(uid, df_task_id, start_time)
                if status != "success": raise Exception(result_url)
                task_title, model_name = "Motion Control", "KLING MOTION PRO"
                
            elif not is_video:
                vivago_images = []
                if f_type == 'i2i' and 'img_bytes' in task_data: vivago_images.append({'bytes': task_data['img_bytes']})
                elif f_type == 'combo' and 'images' in task_data: vivago_images = task_data['images']
                status, result_url = await run_vivago_pipeline(prompt, vivago_images, context, chat_id, status_msg_id, task_id, ratio)
                if status != "success": raise Exception(result_url)
                task_title = {"t2i": "Text To Image", "i2i": "Image To Image", "combo": "Image Combination"}.get(f_type, "Task")
                model_name = IMAGE_MODEL_NAMES.get(task_data.get('image_model_choice', 'vivago_pro'), "Vivago Pro")
                
            else: # CLIPFLY
                await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"⏳ *[Task {task_id}]*\n_Generating Content (Please Wait)..._", parse_mode="Markdown")
                
                # Menggunakan token murni dari session state saat submit
                token = task_data.get("clipfly_token")
                if not token: 
                    raise Exception("Akun Apikey sesi tidak ditemukan. Silakan ulangi mulai dari menu utama.")
                
                model_choice = task_data.get('model_choice', 'pixverse_v6')
                if f_type == 't2v':
                    queue_id, err_msg = await submit_text_to_video_task(prompt, token, task_data.get('audio', False), model_choice, ratio)
                elif f_type == 'i2v':
                    img_bytes, filename, w, h = await asyncio.to_thread(compress_image_sync, task_data['img_bytes'], task_data['img_name'])
                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    storage_path, err_upl = await upload_to_clipfly(b64, filename, token)
                    if not storage_path: raise Exception(err_upl)
                    mat_id, err_mat = await create_material(storage_path, filename, token, w, h)
                    if not mat_id: raise Exception(err_mat)
                    queue_id, err_msg = await submit_video_task(storage_path, prompt, token, task_data.get('audio', False), model_choice, mat_id)

                if not queue_id: raise Exception(err_msg)
                status, result_url = await poll_clipfly_task(queue_id, token)
                if status != "success": raise Exception(result_url)
                
                task_title, model_name = {"t2v": "Text To Video", "i2v": "Image To Video"}.get(f_type, "Task"), model_choice.upper()

            m, s = divmod(int(time.time() - start_time), 60)
            processing_time = f"{m} min {s} sec" if m > 0 else f"{s} sec"
            await send_media_result(context.bot, chat_id, status_msg_id, task_title, model_name, processing_time, result_url, is_video)

            if LOG_CHANNEL:
                try: await context.bot.send_message(chat_id=LOG_CHANNEL, text=f"✅ **Berhasil Generate**\nUsername : @{task_data.get('username', 'Unknown')}\nID : `{chat_id}`\nWaktu : {processing_time}\nNama Model : {model_name}")
                except: pass

        except asyncio.CancelledError:
            try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"🚫 *[Task {task_id}]* Dibatalkan.")
            except: pass
        except Exception as e:
            try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"❌ <b>[Task {task_id}] Failed:</b> {str(e)}", parse_mode="HTML")
            except: pass
        finally:
            cleanup_temp_files(task_data)
            await tasks_col.delete_one({"task_id": task_id})
            if task_id in active_async_tasks: del active_async_tasks[task_id]

# ==========================================
# FLOW UTAMA / UI BOT
# ==========================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not query and update.message:
        try: await update.message.set_reaction(reaction="🎉")
        except: pass
        try: temp_msg = await update.message.reply_text("🔄", reply_markup=ReplyKeyboardRemove()); await temp_msg.delete()
        except: pass

    user = await users_col.find_one({"user_id": user_id})
    has_access = is_access_valid(user)
    is_allowed = has_access or (user_id == ADMIN_ID) 
    is_lifetime = (user and user.get("access_expired_at") == "lifetime") or (user_id == ADMIN_ID)
    
    if not is_allowed:
        if user and 'access_expired_at' in user: 
            await users_col.update_one({"user_id": user_id}, {"$unset": {"access_expired_at": ""}})
        exp_text = "❌ Akses Tidak Aktif (Harap Beli Akses)"
    else:
        if is_lifetime: exp_text = "♾️ LIFETIME (Admin)" if user_id == ADMIN_ID else "♾️ LIFETIME"
        else:
            exp = user.get("access_expired_at") if user else None
            if isinstance(exp, datetime):
                if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
                exp_text = exp.astimezone(JKT).strftime('%d %b %Y, %H:%M WIB')
            else: exp_text = "❌ Akses Tidak Aktif"

    time_str = datetime.now(JKT).strftime("%d %B %Y, %H:%M WIB")
    
    welcome_msg = (
        f"⚡ <b>{BOT_NAME}</b> ⚡\n\n🆔 <b>ID Anda:</b> <code>{user_id}</code>\n"
        f"🕒 <b>Waktu Server:</b> {time_str}\n👑 <b>Status Akses:</b> {exp_text}\n\n"
        "📖 <b>Silakan buka dan baca INFORMASI PENGGUNAAN terlebih dahulu!</b>\n\n"
        "Silakan pilih layanan yang ingin digunakan melalui tombol di bawah ini."
    )
    
    bot_photo = None
    try:
        photos = await context.bot.get_user_profile_photos(context.bot.id)
        if photos.total_count > 0: bot_photo = photos.photos[0][-1].file_id
    except: pass 

    await send_new_menu(update, context, welcome_msg, get_main_keyboard(user_id, is_allowed, is_lifetime), "HTML", bot_photo)
    return CHOOSING_FEATURE

async def handle_login_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    try: await update.message.delete()
    except: pass
    
    if 'last_bot_msg_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=context.user_data['last_bot_msg_id'])
        except: pass

    if "|" not in text:
        temp_msg = await context.bot.send_message(chat_id, "❌ *Format salah!*\nGunakan format: `email|password`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="main_menu")]]))
        context.user_data['last_bot_msg_id'] = temp_msg.message_id
        return CHOOSING_FEATURE
        
    email, password = text.split("|", 1)
    temp_msg = await context.bot.send_message(chat_id, "⏳ *Memvalidasi Akun Apikey...*", parse_mode="Markdown")
    
    token, err = await login_clipfly(email.strip(), password.strip())
    
    if not token:
        # Menampilkan pesan error asli dari API ke user
        await temp_msg.edit_text(f"❌ *Validasi Gagal!*\nAlasan: `{err}`\n\nSilakan coba input ulang dari menu utama.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="main_menu")]]))
        context.user_data['last_bot_msg_id'] = temp_msg.message_id
        return CHOOSING_FEATURE

    # Token hanya disimpan pada sesi memori RAM per task, bukan ke DB (Selalu Bersih)
    context.user_data['clipfly_token'] = token
    context.user_data.update({'feature_type': 't2v', 'ratio': '9:16', 'model_choice': 'pixverse_v6', 'audio': False})
    
    await temp_msg.edit_text("✅ *Akun Apikey Valid!*\n\n🎥 *VIDEO DASHBOARD*\n_Pilih pengaturan lalu klik Lanjutkan:_", parse_mode="Markdown", reply_markup=get_video_dashboard_keyboard(context.user_data))
    context.user_data['last_bot_msg_id'] = temp_msg.message_id
    
    return CONFIGURING_DASHBOARD

async def handle_feature_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if query.data == "locked_lifetime":
        try: await query.answer("⛔ Fitur ini KHUSUS untuk member akses LIFETIME!", show_alert=True)
        except: pass
        return CHOOSING_FEATURE
        
    user = await users_col.find_one({"user_id": user_id})
    if user_id != ADMIN_ID and not is_access_valid(user):
        try: await query.answer("⛔ Anda tidak memiliki akses aktif! Silakan Beli Akses terlebih dahulu.", show_alert=True)
        except: pass
        return CHOOSING_FEATURE

    if query.data == "menu_video":
        # Paksa user untuk input Email & Pass baru setiap kali mereka mengklik VIDEO GENERATION
        await send_new_menu(update, context, "🔐 *Akun Apikey Dibutuhkan*\n\nSilakan kirim Akun Apikey Anda dengan format:\n`email|password`", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
        return WAITING_CLIPFLY_LOGIN
            
    if query.data == "menu_image": 
        context.user_data.update({'feature_type': 't2i', 'ratio': '9:16', 'image_model_choice': 'vivago_pro'})
        await send_new_menu(update, context, "🎨 *IMAGE DASHBOARD (Vivago)*\n_Pilih pengaturan lalu klik Lanjutkan:_", get_image_dashboard_keyboard(context.user_data), "Markdown")
        return CONFIGURING_DASHBOARD
        
    if query.data == "menu_motion":
        context.user_data.update({'feature_type': "motion", 'replace_background': True})
        await send_new_menu(update, context, "📸 *Kirim 1 Foto Wajah yang jelas:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
        return WAITING_MOTION_IMAGE

async def handle_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "ignore_btn":
        try: await query.answer()
        except: pass
        return CONFIGURING_DASHBOARD

    if data == "continue_task":
        f_type = context.user_data.get('feature_type')
        if f_type in ["i2i", "i2v"]:
            await send_new_menu(update, context, "📸 *Kirim 1 Foto Referensi:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
            return WAITING_IMAGE
        elif f_type == "combo":
            context.user_data['images'] = []
            await send_new_menu(update, context, "📸 *Kirim 1-7 Foto.*\nKlik '✅ Selesai' jika sudah.", InlineKeyboardMarkup([[InlineKeyboardButton("✅ Selesai Upload", callback_data="upload_done")], [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
            return WAITING_MULTIPLE_IMAGES
        else:
            await send_new_menu(update, context, "✍️ *Masukkan Prompt:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
            return WAITING_PROMPT

    if data.startswith("set_feat_"): context.user_data['feature_type'] = data.replace("set_feat_", "")
    elif data.startswith("set_ratio_"): context.user_data['ratio'] = data.replace("set_ratio_", "")
    elif data.startswith("set_img_model_"): context.user_data['image_model_choice'] = data.replace("set_img_model_", "")
    elif data.startswith("set_model_"): m = data.replace("set_model_", ""); context.user_data['model_choice'] = m; context.user_data['audio'] = False if m in ['lumen'] else context.user_data.get('audio', False)
    elif data.startswith("set_audio_"): context.user_data['audio'] = (data == "set_audio_yes")

    is_img = context.user_data.get('feature_type') in ['t2i', 'i2i', 'combo']
    kb = get_image_dashboard_keyboard(context.user_data) if is_img else get_video_dashboard_keyboard(context.user_data)
    try: await query.edit_message_reply_markup(reply_markup=kb)
    except: pass
    try: await query.answer()
    except: pass
    return CONFIGURING_DASHBOARD

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        file_obj = await (update.message.document.get_file() if update.message.document else update.message.photo[-1].get_file())
        img_name = update.message.document.file_name if update.message.document else f"{uuid.uuid4().hex[:8]}.jpg"
        temp_path = os.path.join(os.getcwd(), img_name)
        await file_obj.download_to_drive(custom_path=temp_path)
        context.user_data['img_path'], context.user_data['img_name'] = temp_path, img_name
        await send_new_menu(update, context, "✍️ *Masukkan Prompt:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
        return WAITING_PROMPT
    except Exception: return WAITING_IMAGE

async def handle_multiple_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query and query.data == "upload_done":
        if not context.user_data.get('images'): return WAITING_MULTIPLE_IMAGES
        await send_new_menu(update, context, "✍️ *Masukkan Prompt:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
        return WAITING_PROMPT
    try:
        file_obj = await (update.message.document.get_file() if update.message.document else update.message.photo[-1].get_file())
        img_name = update.message.document.file_name if update.message.document else f"{uuid.uuid4().hex[:8]}.jpg"
        temp_path = os.path.join(os.getcwd(), img_name)
        await file_obj.download_to_drive(custom_path=temp_path)
        if 'images' not in context.user_data: context.user_data['images'] = []
        context.user_data['images'].append({"path": temp_path, "name": img_name})
        await update.message.reply_text(f"📥 Foto #{len(context.user_data['images'])} diterima.")
        return WAITING_MULTIPLE_IMAGES
    except: return WAITING_MULTIPLE_IMAGES

async def handle_motion_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data['replace_background'] = True if query.data == "mmode_swap" else False
    await send_new_menu(update, context, "📸 *Kirim 1 Foto Wajah yang jelas:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
    return WAITING_MOTION_IMAGE

async def handle_motion_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        file_obj = await (update.message.document.get_file() if update.message.document else update.message.photo[-1].get_file())
        img_name = update.message.document.file_name if update.message.document else f"{uuid.uuid4().hex[:8]}.jpg"
        temp_path = os.path.join(os.getcwd(), img_name)
        await file_obj.download_to_drive(custom_path=temp_path)
        context.user_data['img_path'], context.user_data['img_name'] = temp_path, img_name
        await send_new_menu(update, context, "🔗 *Kirim URL Video TikTok Referensi:*", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]), "Markdown")
        return WAITING_MOTION_URL
    except: return WAITING_MOTION_IMAGE

async def handle_motion_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['tiktok_url'] = update.message.text.strip()
    return await submit_to_background(update, context)

async def process_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['prompt'] = update.message.text
    return await submit_to_background(update, context)

async def submit_to_background(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if 'last_bot_msg_id' in context.user_data:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=context.user_data['last_bot_msg_id'])
        except Exception: pass
        
    user = await users_col.find_one({"user_id": user_id})
    is_lifetime = (user and user.get("access_expired_at") == "lifetime") or (user_id == ADMIN_ID)
    
    max_tasks = 3 if is_lifetime else 1
    active_count = await tasks_col.count_documents({"chat_id": chat_id})
    if active_count >= max_tasks:
        await update.message.reply_text(
            f"⛔ *Limit Antrean Tercapai!*\n"
            f"Maksimal {max_tasks} task berjalan bersamaan untuk paket aksesmu.\n"
            f"Silakan tunggu hingga selesai atau batalkan task dari menu Task List.", 
            parse_mode="Markdown"
        )
        cleanup_temp_files(context.user_data)
        return ConversationHandler.END

    task_id = str(uuid.uuid4())[:8]
    status_msg = await update.message.reply_text(f"⏳ *[Task ID: {task_id}]*\n_Connecting to AI..._", parse_mode="Markdown")
    
    task_data = dict(context.user_data)
    username = update.effective_user.username or "TanpaUsername"
    
    # Token dimasukkan langsung dari session user_data (Tanpa baca/tulis DB)
    task_data.update({
        'task_id': task_id, 
        'username': username, 
        'user_id': user_id,
        'clipfly_token': context.user_data.get('clipfly_token')
    })
    
    # Save ke DB beserta TTL Index
    await tasks_col.insert_one({
        "task_id": task_id, "chat_id": chat_id, "start_time": time.time(),
        "prompt": task_data.get('prompt', task_data.get('tiktok_url', '')),
        "createdAt": datetime.now(timezone.utc)
    })
    
    t = asyncio.create_task(background_generate_task(chat_id, status_msg.message_id, context, task_data))
    active_async_tasks[task_id] = t
    
    # Buang SEMUA data dari memory, termasuk token
    for k in ['feature_type', 'ratio', 'model_choice', 'audio', 'prompt', 'img_path', 'img_name', 'images', 'tiktok_url', 'replace_background', 'clipfly_token']: 
        context.user_data.pop(k, None)
        
    return ConversationHandler.END

async def handle_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    try: await query.answer()
    except: pass
    
    user_tasks = await tasks_col.find({"chat_id": chat_id}).to_list(length=None)
    
    if not user_tasks:
        await send_new_menu(update, context, "📋 *Tidak ada task yang sedang berjalan saat ini.*", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="main_menu")]]), "Markdown")
        return CHOOSING_FEATURE
        
    text = "📋 *DAFTAR TASK AKTIF*\n\n"
    keyboard = []
    for t in user_tasks:
        m, s = divmod(int(time.time() - t["start_time"]), 60)
        text += f"🔹 *ID:* `{t['task_id']}`\n   *Prompt:* {t.get('prompt', '')[:25]}...\n   *Waktu:* {m}m {s}s\n\n"
        keyboard.append([InlineKeyboardButton(f"❌ Batalkan Task: {t['task_id']}", callback_data=f"cancel_task_{t['task_id']}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Kembali", callback_data="main_menu")])
    await send_new_menu(update, context, text, InlineKeyboardMarkup(keyboard), "Markdown")
    return CHOOSING_FEATURE

async def handle_cancel_task_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer("Task dibatalkan.", show_alert=True)
    except: pass
    
    task_id = query.data.replace("cancel_task_", "")
    if task_id in active_async_tasks: active_async_tasks[task_id].cancel()
    await tasks_col.delete_one({"task_id": task_id})
    return await handle_task_list(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_temp_files(context.user_data)
    for k in ['feature_type', 'ratio', 'model_choice', 'audio', 'prompt', 'img_path', 'img_name', 'images', 'clipfly_token']: context.user_data.pop(k, None)
    return await start_cmd(update, context)

# ==========================================
# SETUP & EVENT LOOP
# ==========================================
async def error_handler_global(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"[!] Terjadi Error Global (Ignored to prevent crash): {context.error}")

async def post_init_setup(app: Application):
    app.bot_data["ADMIN_ID"] = ADMIN_ID
    app.bot_data["users_col"] = users_col
    app.bot_data["pricing_col"] = pricing_col
    app.bot_data["orders_col"] = orders_col

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=300))

    try:
        await users_col.create_index("user_id")
        await tasks_col.create_index("chat_id")
        await orders_col.create_index("order_id")
        
        # Penanganan khusus jika terjadi bentrok Index TTL sebelumnya
        try:
            await tasks_col.create_index("createdAt", expireAfterSeconds=1800)
        except Exception as idx_err:
            if "IndexOptionsConflict" in str(idx_err) or "already exists" in str(idx_err):
                print("♻️ Menimpa Index TTL lama di database...")
                await tasks_col.drop_index("createdAt_1")
                await tasks_col.create_index("createdAt", expireAfterSeconds=1800)
                
        print("⚡ Database Indexing & TTL Active (Fast Response Mode)")
    except Exception as e: 
        print(f"⚠️ Index warning: {e}")

    pending_orders = await orders_col.find({"status": "pending"}).to_list(length=None)
    for order in pending_orders: asyncio.create_task(cek_pembayaran_loop(app.bot, order))
        
    pending_tasks = await tasks_col.find({}).to_list(length=None)
    if pending_tasks:
        for task in pending_tasks:
            try:
                await app.bot.send_message(
                    chat_id=task["chat_id"], 
                    text=f"⚠️ *[Task {task['task_id']}]* Dibatalkan karena sistem mengalami restart (Anti-Crash Protocol). Silakan request ulang.",
                    parse_mode="Markdown"
                )
            except: pass
        await tasks_col.delete_many({})
        print(f"🧹 Membersihkan {len(pending_tasks)} task lama akibat restart sistem.")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True) 
        .post_init(post_init_setup)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    
    app.add_error_handler(error_handler_global)
    
    app.add_handler(CallbackQueryHandler(process_buy_callback, pattern="^buy_pkg_"))
    app.add_handler(CallbackQueryHandler(handle_cancel_order, pattern="^cancel_order_"))
    app.add_handler(CallbackQueryHandler(handle_delete_listakses_callback, pattern="^delpkg_"))
    
    app.add_handler(CommandHandler('cmd', admin_cmd_list))
    app.add_handler(CommandHandler('add_member', admin_add_member))
    app.add_handler(CommandHandler('new_akses', admin_new_akses))
    app.add_handler(CommandHandler('list_member', admin_list_member))
    app.add_handler(CommandHandler('delete_akses', admin_delete_akses))
    app.add_handler(CommandHandler('list_harga', admin_list_harga))
    app.add_handler(CommandHandler('delete_listakses', admin_delete_listakses))
    app.add_handler(CommandHandler('broadcast', admin_broadcast))
    app.add_handler(CommandHandler('broadcast_hari', admin_broadcast_hari)) # Tambahan
    app.add_handler(CommandHandler('broadcast_lifetime', admin_broadcast_lifetime)) # Tambahan
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start_cmd),
            CommandHandler('perbarui_memberlifetime', admin_perbarui_memberlifetime),
            CallbackQueryHandler(start_cmd, pattern="^main_menu$")
        ],
        states={
            CHOOSING_FEATURE: [
                CallbackQueryHandler(handle_feature_category, pattern="^(menu_image|menu_video|menu_motion|locked_lifetime)$"),
                CallbackQueryHandler(handle_buy_akses, pattern="^menu_buy$"),
                CallbackQueryHandler(handle_task_list, pattern="^menu_task_list$"),
                CallbackQueryHandler(handle_cancel_task_btn, pattern="^cancel_task_"),
                CallbackQueryHandler(cancel, pattern="^cancel$")
            ],
            CONFIGURING_DASHBOARD: [
                CallbackQueryHandler(handle_dashboard, pattern="^(set_|continue_task|ignore_btn)"),
                CallbackQueryHandler(cancel, pattern="^cancel$")
            ],
            WAITING_CLIPFLY_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_login_input)],
            WAITING_IMAGE: [MessageHandler((filters.PHOTO | filters.Document.IMAGE), handle_image)],
            WAITING_MOTION_IMAGE: [MessageHandler((filters.PHOTO | filters.Document.IMAGE), handle_motion_image)],
            WAITING_MULTIPLE_IMAGES: [
                MessageHandler((filters.PHOTO | filters.Document.IMAGE), handle_multiple_images),
                CallbackQueryHandler(handle_multiple_images, pattern="^upload_done$")
            ],
            WAITING_MOTION_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_motion_url)],
            WAITING_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_prompt)],
            WAITING_LIFETIME_FILE: [MessageHandler(filters.Document.ALL, handle_lifetime_file)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
        allow_reentry=True
    )
    
    app.add_handler(conv_handler)
    
    while True:
        try:
            print(f"🚀 {BOT_NAME} Bot Started with Access Subscription System & Resiliency enabled!")
            app.run_polling(drop_pending_updates=True, close_loop=False)
            break 
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️ Telegram Polling Terputus: {e}. Bot akan merestart jaringan dalam 1 detik...")
            time.sleep(1)

if __name__ == "__main__":
    main()