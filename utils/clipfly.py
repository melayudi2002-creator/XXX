import random
import io
import mimetypes
import asyncio
import aiohttp
from PIL import Image

# ==========================================
# HELPER: RANDOM IP & HEADERS
# ==========================================
def get_random_ip():
    """Menghasilkan IP acak untuk disisipkan ke header."""
    return f"{random.randint(1, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

def get_headers(token=None):
    """Menghasilkan header dengan Random IP untuk bypass/stabilitas."""
    ip = get_random_ip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15; 25062RN2DY Build/AQ3A.250226.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.138 Mobile Safari/537.36",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "x-app-id": "app-fotor-web",
        "origin": "https://www.clipfly.ai",
        "referer": "https://www.clipfly.ai/",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "x-forwarded-for": ip,  # Random IP disisipkan di sini
        "x-real-ip": ip         # Random IP disisipkan di sini
    }
    if token:
        # Clipfly biasanya menggunakan Bearer token
        if not token.startswith("Bearer "):
            headers["authorization"] = f"Bearer {token}"
        else:
            headers["authorization"] = token
    return headers

# ==========================================
# LOGIN LOGIC (PENGGANTI AUTO REGISTER)
# ==========================================
async def login_clipfly(email, password):
    """Melakukan login ke Clipfly dan mengembalikan token."""
    url = "https://www.clipfly.ai/api/v1/account/login"
    payload = {
        "account": email, 
        "password": password
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=get_headers(), timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 0 and "data" in data and "token" in data["data"]:
                        return data["data"]["token"], None
                    return None, data.get("message", "Login gagal dari server Clipfly.")
                return None, f"HTTP Error {resp.status}"
    except Exception as e:
        return None, str(e)

# ==========================================
# CLIPFLY GENERATOR CORE LOGIC
# ==========================================
def compress_image_sync(file_bytes, original_filename, max_mb=4.8):
    max_bytes = int(max_mb * 1024 * 1024)
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
        final_w, final_h = img.width, img.height
        
        if len(file_bytes) <= max_bytes:
            filename_without_ext = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
            return file_bytes, f"{filename_without_ext}.jpg", final_w, final_h

        while True:
            out.seek(0)
            out.truncate()
            temp_img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS) if scale < 1.0 else img
            final_w, final_h = temp_img.width, temp_img.height
            temp_img.save(out, format="JPEG", optimize=True, quality=quality)
            if out.tell() <= max_bytes: break
            if quality > 30: quality -= 15  
            else: scale *= 0.75; quality = 70   
            
        filename_without_ext = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
        new_filename = f"{filename_without_ext}_compressed.jpg"
        return out.getvalue(), new_filename, final_w, final_h
    except Exception as e:
        print(f"Compress Error: {e}")
        return file_bytes, original_filename, 1024, 1024

async def upload_to_clipfly(b64, name, token):
    url = "https://www.clipfly.ai/api/v1/common/upload/base64"
    mime_type, _ = mimetypes.guess_type(name)
    payload = {
        "content": f"data:{mime_type or 'image/jpeg'};base64,{b64}",
        "name": name, "file_type": "image", "is_original_name": 0, "prefix_path": "/uploads"
    }
    timeout = aiohttp.ClientTimeout(total=45)
    last_err = "Timeout saat unggah gambar (gagal 10x)."
    for _ in range(10): 
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=get_headers(token)) as resp:
                    res = await resp.json()
                    if res.get("code") == 0:
                        return res["data"]["storage_path"], None
                    else:
                        return None, res.get("message", "Gagal unggah gambar (Respons API error).")
        except Exception as e:
            last_err = f"Koneksi terputus: {e}"
            await asyncio.sleep(2)
    return None, last_err

async def create_material(path, name, token, width, height):
    url = "https://www.clipfly.ai/api/v1/user/materials/create"
    payload = {
        "is_ai": -1,
        "urls": {"thumb": path, "url": path},
        "name": str(random.randint(10000, 99999)) + ".png",
        "type": "image",
        "attrs": {"width": width, "height": height}
    }
    timeout = aiohttp.ClientTimeout(total=30)
    last_err = "Gagal membuat material id (Timeout 10x)."
    for _ in range(10):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=get_headers(token)) as resp:
                    r = await resp.json()
                    if r.get("code") == 0:
                        return str(r["data"]["id"]), None
                    else:
                        return None, r.get("message", "Gagal membuat material (Respons API error).")
        except Exception as e:
            last_err = f"Koneksi terputus: {e}"
            await asyncio.sleep(2)
    return None, last_err

async def submit_text_to_video_task(p, t, aud, m, r):
    url = "https://www.clipfly.ai/api/v1/user/ai-task-queues"
    voice = aud
    is_scale = 0
    
    if m in ["seedance", "seedance_2", "google_veo", "xai_grok"]:
        model_id = "25"; duration = "5"
    elif m == "lumen":
        model_id = "17"; duration = "10"; voice = False
    elif m in ["pixverse_v6", "wan_2_7", "kling_o1"]:
        model_id = "29"; duration = "10"
    else: 
        model_id = "29"; duration = "10"

    payload = {
        "type": 16, 
        "attrs": [{
            "camera_control": "auto", "is_scale": is_scale, "prompt": p, 
            "enhance": True, "style": "general", "negative_prompt": "", 
            "ratio": r, "from": "text", "voice": voice, "model_id": model_id, 
            "camerafixed": False, "duration": str(duration), "audio_type": 0, "biz_type": 16
        }]
    }
    timeout = aiohttp.ClientTimeout(total=30)
    last_err = "Gagal terhubung Server AI Video (10x)."
    for _ in range(10): 
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=get_headers(t)) as resp:
                    res = await resp.json()
                    if res.get("code") == 0: 
                        return res["data"]["id"], None
                    else:
                        return None, res.get("message", "Error dari API Text to Video.")
        except Exception as e:
            last_err = f"Koneksi terputus: {e}"
            await asyncio.sleep(2)
    return None, last_err

async def submit_video_task(path, p, t, aud, m, mat_id):
    url = "https://www.clipfly.ai/api/v1/user/ai-task-queues"
    is_scale = 0
    voice = aud
    
    if m in ["seedance", "seedance_2", "google_veo", "xai_grok"]:
        model_id = "25"; duration = "10"
    elif m == "lumen":
        model_id = "17"; duration = "10"; voice = False
    elif m in ["pixverse_v6", "wan_2_7", "kling_o1"]:
        model_id = "29"; duration = "10"
    else: 
        model_id = "29"; duration = "10"

    payload = {
        "type": 17, 
        "attrs": [{
            "maskImage": None, "prompt": p, "camera_control": "auto", 
            "source_image": path, "imageFrom": "upload", "img_style_id": "111", 
            "materialId": str(mat_id), "is_scale": is_scale, "negative_prompt": "", 
            "from": "image", "urls": { "url": path }, "voice": voice, 
            "model_id": model_id, "camerafixed": False, "duration": str(duration), 
            "audio_type": 0, "biz_type": 17
        }]
    }
    
    timeout = aiohttp.ClientTimeout(total=30)
    last_err = "Server Request berulang (10x)."
    for _ in range(10): 
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=get_headers(t)) as resp:
                    res = await resp.json()
                    if res.get("code") == 0: 
                        return res["data"]["id"], None
                    else:
                        return None, res.get("message", "Error dari API Image to Video.")
        except Exception as e:
            last_err = f"Koneksi terputus: {e}"
            await asyncio.sleep(2)
    return None, last_err

async def poll_clipfly_task(q_id, t):
    url = f"https://www.clipfly.ai/api/v1/user/ai-tasks/list?queue_id={q_id}"
    timeout = aiohttp.ClientTimeout(total=15)
    for _ in range(60): 
        await asyncio.sleep(5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=get_headers(t)) as resp:
                    res = await resp.json()
                    if res.get("code") == 0 and res.get("data"):
                        info = res["data"][0]
                        if info["status"] == 2: return "success", "https://www.clipfly.ai" + info["ext"]["output_path"]
                        elif info["status"] == 3: return "failed", info.get("fail_reason")
        except Exception: 
            pass
    return "timeout", None