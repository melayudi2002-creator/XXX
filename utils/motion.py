import asyncio
import aiohttp
import time
import random
import string
import io
import json
import uuid
import secrets
from fake_useragent import UserAgent
from PIL import Image
from mimesis import Hardware

ua = UserAgent()

# ==========================================
# AIOHTTP ADAPTER (PENGGANTI REQUESTS)
# ==========================================
class AiohttpResponseAdapter:
    def __init__(self, status_code, text_data, json_data):
        self.status_code = status_code
        self.text = text_data
        self._json = json_data

    def json(self):
        return self._json

async def async_post(url, **kwargs):
    timeout = kwargs.pop('timeout', 20)
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.post(url, **kwargs) as response:
            text_data = await response.text()
            try:
                json_data = await response.json(content_type=None)
            except:
                json_data = {}
            return AiohttpResponseAdapter(response.status, text_data, json_data)

async def async_get(url, **kwargs):
    timeout = kwargs.pop('timeout', 20)
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url, **kwargs) as response:
            text_data = await response.text()
            try:
                json_data = await response.json(content_type=None)
            except:
                json_data = {}
            return AiohttpResponseAdapter(response.status, text_data, json_data)

# ==========================================
# GENERATOR UTILS
# ==========================================
def generate_device_id():
    timestamp_hex = hex(int(time.time() * 1000))[2:]
    random_hex = ''.join(secrets.choice('0123456789abcdef') for _ in range(16 - len(timestamp_hex)))
    return timestamp_hex + random_hex

def generate_random_token():
    return uuid.uuid4().hex

def get_random_device():
    hw = Hardware()
    brand = hw.manufacturer()
    model_name = hw.phone_model()
    device_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
    info = f"{brand} {device_code}"
    system = f"{brand.upper()},{device_code},{brand} {model_name},{device_code}"
    
    return {
        "info": info,
        "system": system
    }

def get_current_timestamp():
    return str(int(time.time() * 1000))

# ==========================================
# UPLOAD IMAGE LOGIC (CRUSHNOW API)
# ==========================================
def get_sheader(key):
    try:
        with open('sheader.json', 'r') as f:
            data = json.load(f)
            return data.get(key)
    except Exception as e:
        print(f"[-] Gagal membaca sheader.json: {e}")
        return ""

def get_crushnow_headers(api_type):
    return {
        "user-agent": "Dart/3.10 (dart:io)",
        "sheader": get_sheader(api_type),
        "spixai": "spixai",
        "developer": "dart",
        "type": "AI",
        "accept-encoding": "gzip",
        "host": "crushnow.xyz"
    }

async def upload_image_crushnow(img_bytes, filename="image.jpg"):
    print(f"[!] Memulai upload gambar ke Crushnow: {filename}")
    url_up = "https://crushnow.xyz//controller/v//upload/_diversification/upload"
    headers = get_crushnow_headers("upload")
    
    form = aiohttp.FormData()
    form.add_field('file', img_bytes, filename=filename, content_type='image/jpeg')
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url_up, headers=headers, data=form) as res_up:
                if res_up.status == 200:
                    res_json = await res_up.json()
                    img_url = res_json.get("data")
                    if img_url:
                        print(f"[+] Berhasil upload ke Crushnow: {img_url}")
                        return img_url
    except Exception as e:
        print(f"[-] Crushnow Upload Gagal: {e}")

    print("[-] Upload gambar gagal.")
    return None

# ==========================================
# COMPRESS LOGIC (Dijalankan di Threadpool)
# ==========================================
def _compress_image_sync_worker(file_bytes, original_filename, max_mb=4.8):
    max_bytes = int(max_mb * 1024 * 1024)
    if len(file_bytes) <= max_bytes: return file_bytes, original_filename
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        max_dimension = 3840 
        if max(img.width, img.height) > max_dimension:
            ratio = max_dimension / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        quality = 85
        scale = 1.0
        while True:
            out.seek(0)
            out.truncate()
            temp_img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS) if scale < 1.0 else img
            temp_img.save(out, format="JPEG", optimize=True, quality=quality)
            if out.tell() <= max_bytes: break
            if quality > 30: quality -= 15  
            else: scale *= 0.75; quality = 70   
        new_filename = "photo_compressed.jpg"
        return out.getvalue(), new_filename
    except Exception:
        return file_bytes, original_filename

# ==========================================
# TIKTOK DOWNLOADER (3-LAYER SUPER FALLBACK)
# ==========================================
async def get_video_url_ssstik(tiktok_url):
    print(f"[!] Memulai ekstraksi video untuk: {tiktok_url}")
    
    print("[1] Mencoba Server Utama (TikWM)...")
    try:
        post_url = "https://www.tikwm.com/api/"
        payload = {"url": tiktok_url, "hd": 1}
        response = await async_post(post_url, data=payload, timeout=12)
        data = response.json()
        if data.get("code") == 0:
            play_url = data.get("data", {}).get("play")
            if play_url:
                if play_url.startswith("/"):
                    play_url = "https://www.tikwm.com" + play_url
                print("[+] Berhasil ekstrak dari TikWM!")
                return play_url
    except Exception as e:
        print(f"[-] TikWM Gagal: {e}")

    print("[2] Mencoba Server Cadangan 1 (LoveTik)...")
    try:
        post_url = "https://lovetik.com/api/ajax/search"
        headers = {
            "User-Agent": ua.random,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://lovetik.com",
            "Referer": "https://lovetik.com/"
        }
        payload = {"query": tiktok_url}
        res_lovetik = await async_post(post_url, headers=headers, data=payload, timeout=12)
        data_lt = res_lovetik.json()
        
        if data_lt.get("status") == "ok":
            links = data_lt.get("links", [])
            for link in links:
                deskripsi = link.get("s", "").lower()
                if "without watermark" in deskripsi or "no watermark" in deskripsi:
                    print("[+] Berhasil ekstrak dari LoveTik!")
                    return link.get("a")
            if links and "a" in links[0]:
                print("[+] Berhasil ekstrak dari LoveTik (Default)!")
                return links[0]["a"]
    except Exception as e:
        print(f"[-] LoveTik Gagal: {e}")

    print("[3] Mencoba Server Cadangan 2 (TiklyDown)...")
    try:
        fallback_url = f"https://api.tiklydown.eu.org/api/download?url={tiktok_url}"
        res_tikly = await async_get(fallback_url, timeout=12)
        data_td = res_tikly.json()
        if "video" in data_td and "noWatermark" in data_td["video"]:
            print("[+] Berhasil ekstrak dari TiklyDown!")
            return data_td["video"]["noWatermark"]
    except Exception as e:
        print(f"[-] TiklyDown Gagal: {e}")
        
    print("[-] Semua API Down / Video bersifat Private.")
    return None

# ==========================================
# DREAMFACE LOGIC
# ==========================================
async def setup_dreamface_user():
    for attempt in range(10):
        device_profile = get_random_device()
        device_id = generate_device_id()
        current_ts = get_current_timestamp()
        
        print(f"[!] Mencoba setup Dreamface User (Percobaan {attempt + 1}/10) | UID: {device_id}")
        
        url_auth = "https://log.dreamfaceapp.com/spider/api/v1/access_token"
        headers_auth = {"host": "log.dreamfaceapp.com", "content-type": "application/json; charset=utf-8", "user-agent": "okhttp/4.12.0"}
        payload_auth = {"app_key": "d3f0c431a8", "app_version": "6.25.6", "device_id": device_id, "device_info": device_profile["info"], "log_version": "1.2.1", "platform": "android", "platform_version": "15"}
        
        try:
            resp = await async_post(url_auth, headers=headers_auth, json=payload_auth, timeout=15)
            if resp.json().get("code") != 0:
                print(f"[-] Auth ditolak server. Retrying...")
                await asyncio.sleep(2)
                continue
        except Exception as e:
            print(f"[-] Error Auth: {e}. Retrying...")
            await asyncio.sleep(2)
            continue
            
        uid = device_id
        
        url_login = "https://dreamfaceapp.com/df-server/user/save_user_login"
        headers_login = {"User-Id": uid, "Platform-Type": "ANDROID", "App-Version": "6.25.6", "System-Version": "15", "App-Type": "dreamface_free", "Language": "id", "x-signature": "7F3CDCA50A93309DD4C5EAA1C31116BB", "Content-Type": "application/json;charset=UTF-8", "User-Agent": "okhttp/4.12.0"}
        payload_login = {"app_package_name": "com.dreamapp.dubhe", "app_version": "6.25.6", "device_name": "ANDROID", "device_system": device_profile["system"], "system_version": "android15", "user_id": uid, "country_code": "id", "time_zone": 7, "appInfo": "AN=com.myhexin.reface.HXApplication&AI=2131689472&PN=com.dreamapp.dubhe&AL=DreamFace&PN2=com.dreamapp.dubhe&VN=6.25.6&VC=62506&SN=713705F2E91BA04B75F006E6618E4F37", "sig": "IyAhniSpIBK3PSEIFj2enXOhYbwqGdBZaJ3Wdn/edoPgaA2/wbzqTxdUYkmPP5vYEx7+jiNwkSLIXOyzZ8fFLoYLKzzhEjmDAnapvOadKrcci2ep/QfO5Cik194IrmTc165H+hs22i7CH4eZ/9dNai6mPktF527mb3aIP7I3Lc1L4fc9t4Vb1vbrEBis48SYnGZcGQK8RMl/UoLX4m9FmmU+J2aKDFKUIjngn4FpMFkwMBm4mjuD6XQNoKDcB7TplzeXSxn9kqZouBLtSzDADOMHRjAaxzEF/kPmuidEHh52PO+wBMYKIa298NVFHHpVt35or1Ti4gT0ih2lmn/PEw==", "timestamp": current_ts, "token": generate_random_token(), "platform_type": "ANDROID", "app_type": "dreamface_free", "language": "id"}
        
        try: 
            await async_post(url_login, headers=headers_login, json=payload_login, timeout=15)
        except Exception as e:
            print(f"[-] Error Login: {e}. Retrying...")
            await asyncio.sleep(2)
            continue

        url_quota = "https://cloudf.dreamfaceapp.com/df-server/animate/free_quota/get"
        headers_quota = {"user-id": uid, "platform-type": "ANDROID", "app-version": "6.25.6", "system-version": "15", "app-type": "dreamface_free", "language": "id", "x-signature": "1E83B34BCD4D52D61E52BC7207AC024A", "content-type": "application/json;charset=UTF-8", "user-agent": "okhttp/4.12.0"}
        payload_quota = {"user_id": uid, "timestamp": current_ts, "token": generate_random_token(), "platform_type": "ANDROID", "app_version": "6.25.6", "app_type": "dreamface_free", "language": "id"}
        
        try: 
            await async_post(url_quota, headers=headers_quota, json=payload_quota, timeout=15)
        except Exception as e:
            print(f"[-] Error Quota: {e}. Retrying...")
            await asyncio.sleep(2)
            continue

        print(f"[+] Berhasil membuat dan mengikat User ID: {uid}")
        return uid

    print("[-] Setup Dreamface User GAGAL setelah 10 kali percobaan.")
    return None

async def get_work_detail(work_id, share_link):
    url = "https://cloudf.dreamfaceapp.com/df-server/work/get_work_detail"
    headers = {
        "user-agent": ua.random,
        "content-type": "application/json",
        "referer": share_link
    }
    payload = {"work_id": work_id}
    
    try:
        response = await async_post(url, headers=headers, json=payload, timeout=15)
        res_json = response.json()
        if res_json.get("status_code") == "THS12140000000":
            return res_json.get("data", {}).get("no_wm_work_video_path")
    except Exception:
        pass
    return None

async def submit_motion_task(image_bytes, image_name, tiktok_url, replace_background=True):
    # Set default replace_background=True agar selalu mode "Tukar Avatar"
    compressed_bytes, filename = await asyncio.to_thread(_compress_image_sync_worker, image_bytes, image_name)
    
    final_image_url = await upload_image_crushnow(compressed_bytes, image_name)
    if not final_image_url:
        print("[-] Gagal upload gambar")
        return None, None

    final_video_url = await get_video_url_ssstik(tiktok_url)
    if not final_video_url: 
        print("[-] Gagal memproses URL TikTok")
        return None, None

    uid = await setup_dreamface_user()
    if not uid: 
        print("[-] Gagal Setup User Dreamface")
        return None, None

    url_animate = "https://cloudf.dreamfaceapp.com/df-server/face_v5/animate_image_v5"
    headers_animate = {
        "user-id": uid, "platform-type": "ANDROID", "app-version": "6.25.6", 
        "system-version": "15", "app-type": "dreamface_free", "language": "en", 
        "x-signature": "6A7E02BE106D6D2A197684F680299FE2", 
        "content-type": "application/json;charset=UTF-8", "user-agent": "okhttp/4.12.0"
    }
    
    current_time_ms = int(time.time() * 1000)
    session_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    
    payload_animate = {
        "ctime": current_time_ms,
        "ext": {"animate_channel": "dreamact", "router_url": "aiActing.html", "sing_title": "Acting Avatar", "track_info": json.dumps({"groupId": "", "model": "", "playTypeId": "9", "recInfo": "", "session_id": session_id, "source": "", "startTime": current_time_ms - 300})},
        "merge_by_server": False, "no_water_mark": 0,
        "photo_info_list": [{"face_nums": 0, "five_lands": [[[1, 1], [1, 1], [1, 1]]], "height": 0, "origin_face_locations": [{"down_high": 1, "left_upper_x": 1, "left_upper_y": 1, "right_width": 1}], "photo_path": final_image_url, "square_face_locations": [{"down_high": 1, "left_upper_x": 1, "left_upper_y": 1, "right_width": 1}], "width": 0}],
        "play_types": ["REPLACE_DANCE"],
        "pt_infos": [{"audio_id": "6887320364fb640007b55405", "audio_url": "", "character_id": "", "context": "text", "lan": "en", "video_img_urls": [final_image_url], "video_url": final_video_url, "voice_engine_id": "6887320364fb640007b55405", "voice_name": ""}],
        "reface_flag": False, "task_id": task_id, "template_id": "REPLACE_DANCE", "timestamp": str(current_time_ms), "token": generate_random_token(), "trace_id": "", "user_id": uid,
        "ext_params": {"resolution": 480, "replace_background": replace_background, "ignoreFaceDetection": True, "redo_info": ""},
        "platform_type": "ANDROID", "app_version": "6.25.6", "app_type": "dreamface_free", "language": "en"
    }

    try:
        response = await async_post(url_animate, headers=headers_animate, json=payload_animate, timeout=20)
        res_animate = response.json()
        if res_animate.get("status_code") == "THS12140000000": return uid, task_id
        return None, None
    except Exception:
        return None, None

async def poll_motion_task(uid, task_id, start_time):
    url_poll = "https://cloudf.dreamfaceapp.com/df-server/reface/animate_image_list_poll"
    headers_poll = {
        "user-id": uid, "platform-type": "ANDROID", "app-version": "6.25.6", 
        "system-version": "15", "app-type": "dreamface_free", "language": "en", 
        "x-signature": "79D70A2E35AEE2688FA21A1587FB1534", 
        "content-type": "application/json;charset=UTF-8", "user-agent": "okhttp/4.12.0"
    }

    # BUKA SATU SESI GLOBAL UNTUK TASK INI (Mencegah Socket Exhaustion)
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            if time.time() - start_time > 1200: 
                return "failed", "Waktu tunggu habis (Maksimal 20 Menit). Sistem server AI sedang sibuk."

            current_time_ms = str(int(time.time() * 1000))
            payload_poll = {
                "animate_id_list": [task_id], "user_id": uid, "timestamp": current_time_ms, 
                "token": generate_random_token(), "platform_type": "ANDROID", 
                "app_version": "6.25.6", "app_type": "dreamface_free", "language": "en"
            }
            
            try:
                # REQUEST LANGSUNG DARI SESI YANG SUDAH TERBUKA
                async with session.post(url_poll, headers=headers_poll, json=payload_poll) as response:
                    res = await response.json(content_type=None)
                    
                    if res.get("status_code") == "THS12140000000":
                        anim_list = res.get("data", {}).get("animate_image_list", [])
                        
                        if anim_list:
                            state = anim_list[0].get("state", "unknown")
                            
                            if state in ["success", "completed", "finish"]:
                                url_work = "https://cloudf.dreamfaceapp.com/df-server/work_v5/get_work_list"
                                headers_work = {
                                    "user-id": uid, "platform-type": "ANDROID", "app-version": "6.25.6", 
                                    "system-version": "15", "app-type": "dreamface_free", "language": "en", 
                                    "x-signature": "E858F8169F3AD2386A2C8F7F1CC883B0", 
                                    "content-type": "application/json;charset=UTF-8", "user-agent": "okhttp/4.12.0"
                                }
                                payload_work = {
                                    "last_work_id": "", "page_size": 60, "user_id": uid, 
                                    "timestamp": str(int(time.time() * 1000)), "token": generate_random_token(), 
                                    "platform_type": "ANDROID", "app_version": "6.25.6", 
                                    "app_type": "dreamface_free", "language": "en"
                                }

                                # AMBIL DATA FINAL MENGGUNAKAN SESI YANG SAMA
                                async with session.post(url_work, headers=headers_work, json=payload_work) as resp_work:
                                    res_work = await resp_work.json(content_type=None)
                                    
                                    if res_work.get("status_code") == "THS12140000000":
                                        work_list = res_work.get("data", {}).get("list", [])
                                        if work_list:
                                            share_url = work_list[0].get("share_url")
                                            work_id = work_list[0].get("id")
                                            if share_url and work_id:
                                                no_wm_path = await get_work_detail(work_id, share_url)
                                                return "success", no_wm_path if no_wm_path else share_url
                                                
                                    return "failed", "Gagal mendapatkan URL hasil render akhir dari server."
                                
                            elif state not in ["queue", "processing", "timeout"]:
                                return "failed", f"Task gagal. Ditolak oleh server dengan status: {state}"
                        
            except Exception: 
                pass
            
            # Istirahat 8 detik sebelum polling lagi untuk menghemat resource
            await asyncio.sleep(8)