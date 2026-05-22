import io
import asyncio
from datetime import datetime, timedelta, timezone
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

JKT = pytz.timezone('Asia/Jakarta')

def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ADMIN_ID = context.bot_data.get("ADMIN_ID")
    return update.effective_chat.id == ADMIN_ID

async def admin_cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    text = (
        "🛠️ *DAFTAR COMMAND ADMIN* 🛠️\n\n"
        "• `/add_member <chat_id> <hari/lifetime>`\n"
        "• `/new_akses <harga> <hari/lifetime>`\n"
        "• `/list_member`\n"
        "• `/delete_akses <id>`\n"
        "• `/list_harga`\n"
        "• `/delete_listakses`\n"
        "• `/broadcast <teks>` (Semua User)\n"
        "• `/broadcast_hari <teks>` (Member Harian)\n"
        "• `/broadcast_lifetime <teks>` (Member Lifetime)\n"
        "• `/perbarui_memberlifetime` (Via file .txt)\n"
        "• `/cmd` (Lihat menu ini)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_add_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    try:
        users_col = context.bot_data["users_col"]
        target_id = int(context.args[0])
        durasi = context.args[1].lower()
            
        if durasi == "lifetime":
            new_expire = "lifetime"
            text_resp = "Lifetime"
        else:
            hari = int(durasi)
            now = datetime.now(timezone.utc)
            new_expire = now + timedelta(days=hari)
            text_resp = f"{hari} Hari"
            
        update_data = {
            "$set": {"access_expired_at": new_expire},
            "$setOnInsert": {
                "username": "Unknown", 
                "joined_at": datetime.now(JKT).strftime('%Y-%m-%d %H:%M:%S')
            }
        }
        await users_col.update_one({"user_id": target_id}, update_data, upsert=True)
        await update.message.reply_text(f"✅ Berhasil set akses user `{target_id}` menjadi **{text_resp}**.", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("⚠️ Format salah! Gunakan: `/add_member <chat_id> <hari/lifetime>`", parse_mode="Markdown")

async def admin_new_akses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    try:
        pricing_col = context.bot_data["pricing_col"]
        harga = int(context.args[0])
        durasi = context.args[1].lower()
        
        if durasi == "lifetime":
            await pricing_col.delete_many({"days": "lifetime"}) 
            await pricing_col.insert_one({"price": harga, "days": "lifetime"})
            await update.message.reply_text(f"✅ Berhasil membuat paket Akses:\n📦 **LIFETIME** seharga **Rp {harga:,}**", parse_mode="Markdown")
        else:
            hari = int(durasi)
            await pricing_col.update_one({"days": hari}, {"$set": {"price": harga, "days": hari}}, upsert=True)
            await update.message.reply_text(f"✅ Berhasil membuat paket Akses:\n📦 **{hari} Hari** seharga **Rp {harga:,}**", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("⚠️ Format salah! Gunakan: `/new_akses <harga> <hari/lifetime>`", parse_mode="Markdown")

async def admin_list_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    users_col = context.bot_data["users_col"]
    
    msg = await update.message.reply_text("⏳ *Mengekstrak data member aktif...*", parse_mode="Markdown")
    try:
        users = await users_col.find({"access_expired_at": {"$exists": True}}).to_list(length=None)
        if not users:
            return await msg.edit_text("⚠️ Belum ada member yang memiliki akses.")
            
        text_data = "ID | EXPIRED WAKTU\n--------------------\n"
        for u in users:
            uid = u.get("user_id")
            exp = u.get("access_expired_at")
            if exp == "lifetime":
                text_data += f"{uid} | LIFETIME\n"
            else:
                try:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    exp_jkt = exp.astimezone(JKT)
                    text_data += f"{uid} | {exp_jkt.strftime('%Y-%m-%d %H:%M:%S WIB')}\n"
                except: pass
                
        file_obj = io.BytesIO(text_data.encode('utf-8'))
        file_obj.name = "list_member.txt"
        
        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_obj, caption="📋 **Daftar Akses Member**", parse_mode="Markdown")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ *Gagal mengekstrak data:* `{e}`", parse_mode="Markdown")

async def admin_delete_akses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    try:
        users_col = context.bot_data["users_col"]
        target_id = int(context.args[0])
        result = await users_col.update_one({"user_id": target_id}, {"$unset": {"access_expired_at": ""}})
        
        if result.modified_count > 0:
            await update.message.reply_text(f"✅ Akses user `{target_id}` berhasil dicabut.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ User `{target_id}` tidak ditemukan atau tidak memiliki akses aktif.", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("⚠️ Format salah! Gunakan: `/delete_akses <id>`", parse_mode="Markdown")

async def admin_list_harga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    pricing_col = context.bot_data["pricing_col"]
    
    packages = await pricing_col.find({}).to_list(length=None)
    if not packages:
        return await update.message.reply_text("⚠️ Tidak ada daftar harga yang diset.")
        
    text = "📋 **DAFTAR HARGA AKSES**\n\n"
    for pkg in packages:
        pkg_days = pkg.get('days', 30) 
        hari = "LIFETIME" if pkg_days == "lifetime" else f"{pkg_days} Hari"
        text += f"• **{hari}** : `Rp {pkg.get('price', 0):,}`\n"
        
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_delete_listakses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    pricing_col = context.bot_data["pricing_col"]
    
    packages = await pricing_col.find({}).to_list(length=None)
    if not packages:
        return await update.message.reply_text("⚠️ Tidak ada paket akses untuk dihapus.")
        
    keyboard = []
    for pkg in packages:
        pkg_days = pkg.get('days', 30) 
        hari = "LIFETIME" if pkg_days == "lifetime" else f"{pkg_days} Hari"
        keyboard.append([InlineKeyboardButton(f"❌ Hapus {hari}", callback_data=f"delpkg_{pkg['_id']}")])
        
    await update.message.reply_text("Pilih paket yang ingin dihapus:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_delete_listakses_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update, context): return await query.answer("Akses ditolak.", show_alert=True)
    
    from bson.objectid import ObjectId
    pricing_col = context.bot_data["pricing_col"]
    pkg_id = query.data.replace("delpkg_", "")
    
    await pricing_col.delete_one({"_id": ObjectId(pkg_id)})
    await query.answer("Paket berhasil dihapus!", show_alert=True)
    try: await query.message.delete()
    except: pass

async def admin_perbarui_memberlifetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return
    await update.message.reply_text("📂 Silakan kirimkan file `.txt` berisi ID pengguna (satu baris untuk satu ID) untuk memberikan akses Lifetime.", parse_mode="Markdown")
    return 99 # WAITING_LIFETIME_FILE State

async def handle_lifetime_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update, context): return ConversationHandler.END
    
    if update.message.document and update.message.document.file_name.endswith('.txt'):
        users_col = context.bot_data["users_col"]
        file = await update.message.document.get_file()
        byte_array = await file.download_as_bytearray()
        content = byte_array.decode('utf-8').splitlines()
        
        sukses = 0
        for line in content:
            uid = line.strip()
            if uid.isdigit():
                await users_col.update_one({"user_id": int(uid)}, {"$set": {"access_expired_at": "lifetime"}}, upsert=True)
                sukses += 1
                
        await update.message.reply_text(f"✅ Berhasil memperbarui {sukses} ID menjadi member LIFETIME.", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ File harus berformat `.txt`.")
    return ConversationHandler.END

async def run_broadcast_task(bot, admin_chat_id, target_msg_id, text, reply_to, users):
    sukses, gagal = 0, 0
    total = len(users)
    for u in users:
        try:
            if reply_to: await bot.copy_message(chat_id=u["user_id"], from_chat_id=admin_chat_id, message_id=reply_to.message_id)
            else: await bot.send_message(chat_id=u["user_id"], text=text)
            sukses += 1
        except Exception: gagal += 1
        await asyncio.sleep(0.05) 
        
    try:
        await bot.edit_message_text(chat_id=admin_chat_id, message_id=target_msg_id, text=f"✅ *Broadcast Selesai!*\n👥 Total: {total} | ✅ Sukses: {sukses} | ❌ Gagal: {gagal}", parse_mode="Markdown")
    except: pass

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, target_type: str):
    if not is_admin(update, context): return
    reply_to = update.message.reply_to_message
    text = " ".join(context.args) if context.args else None
    
    cmd_used = f"/{update.message.text.split()[0][1:]}"
    if not reply_to and not text:
        return await update.message.reply_text(f"⚠️ Gunakan `{cmd_used} <teks>` atau balas (reply) pesan.", parse_mode="Markdown")
        
    users_col = context.bot_data["users_col"]
    
    # Filter Query Berdasarkan Tipe Broadcast
    if target_type == "lifetime":
        query = {"access_expired_at": "lifetime"}
        target_name = "Lifetime"
    elif target_type == "hari":
        query = {"access_expired_at": {"$exists": True, "$ne": "lifetime"}}
        target_name = "Harian"
    else:
        query = {}
        target_name = "Keseluruhan"

    users = await users_col.find(query).to_list(length=None)
    if not users:
        return await update.message.reply_text(f"⚠️ Tidak ada member {target_name} yang ditemukan.", parse_mode="Markdown")

    msg = await update.message.reply_text(f"⏳ *Memulai Broadcast ke {len(users)} member {target_name}...*", parse_mode="Markdown")
    asyncio.create_task(run_broadcast_task(context.bot, update.effective_chat.id, msg.message_id, text, reply_to, users))

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_broadcast(update, context, "all")

async def admin_broadcast_hari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_broadcast(update, context, "hari")

async def admin_broadcast_lifetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_broadcast(update, context, "lifetime")