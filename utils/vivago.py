import uuid
import random
import string
import re
import time
import requests
import asyncio
from bs4 import BeautifulSoup

# Global session khusus untuk generator email
email_session = requests.Session()

def get_headers(device_id, role="0"):
    return {
        "x-accept-language": "id",
        "x-system-language": "id",
        "x-client-version": "2.18.1",
        "x-client-platform": "android",
        "x-source": "android",
        "x-app-source": "nature",
        "x-client-deviceid": device_id,
        "x-account-role": role,
        "x-country-code": "ID",
        "x-country-source": "1",
        "x-time-zone": "Asia/Jakarta",
        "x-os-version": "15",
        "x-simulator": "false",
        "x-network-type": "WIFI",
        "x-carrier": "TELKOMSEL",
        "content-type": "application/json;charset=utf-8",
        "accept-encoding": "gzip",
        "user-agent": "okhttp/4.12.0"
    }

def _get_email():
    try:
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "user-agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/137.0.0.0 Mobile Safari/537.36"
        }
        cookies = {"surl": "gmailot.com"}

        response = email_session.get("https://generator.email", headers=headers, cookies=cookies, timeout=30)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            email_tag = soup.find("span", id="email_ch_text")
            
            if email_tag:
                email = email_tag.get_text(strip=True)
                username = email.split("@")[0]
                return email, username
    except Exception as e:
        print(f"[ERROR] Get Email: {e}")
    return None, None

def _send_captcha(session, email, device_id):
    url = "https://vivago.ai/prod-api/user/captcha"
    try:
        r = session.post(url, headers=get_headers(device_id, "0"), json={"method": "email", "email": email}, timeout=30)
        return r.json().get("code") == 0
    except Exception as e:
        print(f"[ERROR] Send Captcha: {e}")
    return False

async def _get_otp_async(username, timeout=300):
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "user-agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/137.0.0.0 Mobile Safari/537.36"
    }
    cookies = {"surl": f"gmailot.com/{username}"}
    
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = await asyncio.to_thread(
                email_session.get, 
                "https://generator.email/inbox3/", 
                headers=headers, 
                cookies=cookies, 
                timeout=30
            )

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                otp_tag = soup.find("p", style=lambda s: s and "font-size: 200%" in s)
                
                if otp_tag:
                    otp_match = re.search(r"\b\d{4,8}\b", otp_tag.get_text(strip=True))
                    if otp_match:
                        return otp_match.group(0)
        except Exception:
            pass
            
        await asyncio.sleep(2)
        
    print("[FAILED] Timeout 5 menit, OTP tidak ditemukan")
    return None

def _login_with_captcha(session, email, device_id, captcha):
    url = "https://vivago.ai/prod-api/user/login/email/captcha"
    payload = {"email": email, "captcha": captcha}
    try:
        r = session.post(url, headers=get_headers(device_id, "0"), json=payload, timeout=30)
        data = r.json()
        if data.get("code") == 0:
            result = data.get("result", {})
            return {
                "email": result.get("email"),
                "refresh_token": result.get("refresh_token"),
                "ticket": session.cookies.get("ticket", "")
            }
    except Exception as e:
        print(f"[ERROR] Login: {e}")
    return None

def _get_gcs_token(session, device_id):
    url = "https://vivago.ai/prod-api/user/gcs/token"
    try:
        r = session.get(url, headers=get_headers(device_id, "1"), timeout=30)
        data = r.json()
        if data.get("code") == 0:
            return data.get("result")
    except Exception as e:
        print(f"[ERROR] GCS Token: {e}")
    return None

def _invited(session, device_id, invitation_id):
    url = "https://vivago.ai/prod-api/trade/v1/account/invited"
    payload = {"invitation_id": invitation_id, "role": "general", "version": "v1"}
    try:
        r = session.post(url, headers=get_headers(device_id, "1"), json=payload, timeout=30)
        data = r.json()
        if data.get("code") == 0 and data.get("message") == "success":
            return True
    except Exception as e:
        print(f"[ERROR] Invite: {e}")
    return False

def _get_userId(device_id, ticket):
    url = "https://vivago.ai/prod-api/trade/v1/account?role=general"
    headers = get_headers(device_id, "1")
    cookies = {"ticket": ticket}
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=30)
        data = r.json()
        if data.get("msg") == "success":
            return data.get("data", {}).get("userId")
    except Exception as e:
        print(f"[ERROR] get_userId: {e}")
    return None

def _upload_foto(file_bytes, gcs_token, device_id):
    uid = str(uuid.uuid4())
    name_id = f"j_{uid}"
    url = f"https://storage.googleapis.com/upload/storage/v1/b/hidreamai-image/o?uploadType=media&name={name_id}"
    
    headers = get_headers(device_id, "1")
    headers["authorization"] = f"Bearer {gcs_token}"
    headers["content-type"] = "image/png"
    
    try:
        r = requests.post(url, headers=headers, data=file_bytes, timeout=30)
        if r.status_code in [200, 201]:
            return name_id
    except Exception as e:
        print(f"[ERROR] upload_foto: {e}")
    return None

def _submit_task(image_names, prompt, device_id, ticket, ratio):
    url = "https://vivago.ai/api/gw/v3/image/image_gen_pro/async"
    headers = get_headers(device_id, "1")
    
    payload = {
        "ad_channel": None,
        "audios": [],
        "en_negative_prompt": "",
        "en_prompt": "",
        "images": image_names,
        "magic_prompt": "",
        "mask": [],
        "module": "image_gen_pro",
        "negative_prompt": "",
        "params": {
            "batch_size": 1,
            "custom_params": {"wh_ratio": ratio},
            "height": 512,
            "mode": "1k",
            "reserved_str": "",
            "seed": -1,
            "style": "Default",
            "wh_ratio": ratio,
            "width": 512
        },
        "prompt": prompt,
        "request_id": str(uuid.uuid4()),
        "template_id": "",
        "upstream_id": "",
        "version": "v1",
        "videos": []
    }
    
    cookies = {"ticket": ticket}
    try:
        r = requests.post(url, headers=headers, json=payload, cookies=cookies, timeout=30)
        data = r.json()
        # Perubahan: Memastikan mengambil pesan error dari server
        if data.get("code") == 0:
            return data["result"]["history_id"], None
        else:
            # Jika gagal (contoh code 1050), ambil nilai 'message'
            print(f"[!] Server Vivago menolak task: {data}")
            error_msg = data.get("message", "Task ditolak oleh server tanpa pesan detail.")
            return None, error_msg
    except Exception as e:
        print(f"[ERROR] submit_task: {e}")
        return None, str(e)

async def _wait_for_content_async(history_id, device_id, ticket):
    url = "https://vivago.ai/capi/content/app/assets/batch"
    headers = get_headers(device_id, "1")
    payload = {"history_ids": [str(history_id)], "is_favorite": False}
    cookies = {"ticket": ticket}
    
    start_time = time.time()
    while time.time() - start_time < 480: 
        try:
            r = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, cookies=cookies, timeout=30)
            data = r.json()
            if data.get("code") == 0 and data.get("data"):
                sub_assets = data["data"][0].get("sub_assets", [])
                if sub_assets:
                    content_id = sub_assets[0].get("content_id")
                    if content_id:
                        return content_id
        except Exception:
            pass
        await asyncio.sleep(10)
    return None

def _get_result(userId, content_id, device_id, ticket):
    url = "https://vivago.ai/capi/content/v1list"
    headers = get_headers(device_id, "1")
    payload = {"contentIds": [content_id], "userId": userId}
    cookies = {"ticket": ticket}
    
    try:
        r = requests.post(url, headers=headers, json=payload, cookies=cookies, timeout=30)
        data = r.json()
        if data.get("code") == 0 and data.get("data"):
            media_set = data["data"][0].get("display", {}).get("media_set", {})
            return media_set.get("detail", {}).get("main_url")
    except Exception as e:
        print(f"[ERROR] get_result: {e}")
    return None


async def run_vivago_pipeline(prompt, images, context, chat_id, status_msg_id, task_id, ratio="9:16"):
    async def update_status(text):
        try: 
            await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg_id, text=f"⏳ *[Task {task_id}]*\n_{text}_", parse_mode="Markdown")
        except: 
            pass

    try:
        await update_status("Mendaftar akun sementara (Generator Email)...")
        session = requests.Session()
        device_id = uuid.uuid4().hex[:17]
        invitation_code = "JQS2X4203"

        email, username = await asyncio.to_thread(_get_email)
        if not email: return "failed", "Gagal mendapatkan email sementara."

        if not await asyncio.to_thread(_send_captcha, session, email, device_id):
            return "failed", "Gagal mengirim OTP ke email."

        await update_status("Menunggu OTP masuk (Maks 5 menit)...")
        captcha_code = await _get_otp_async(username)
        if not captcha_code: return "failed", "Waktu tunggu OTP habis."

        await update_status("Sedang login & submit kode undangan...")
        login_data = await asyncio.to_thread(_login_with_captcha, session, email, device_id, captcha_code)
        if not login_data or not login_data.get("ticket"):
            return "failed", "Gagal Login ke API."
            
        ticket = login_data["ticket"]
        gcs_token = await asyncio.to_thread(_get_gcs_token, session, device_id)
        
        await asyncio.to_thread(_invited, session, device_id, invitation_code)
        
        user_id = await asyncio.to_thread(_get_userId, device_id, ticket)
        if not gcs_token or not user_id: 
            return "failed", "Gagal mengambil kredensial sesi."
        
        image_names = []
        if images and len(images) > 0:
            for idx, img in enumerate(images):
                await update_status(f"Mengunggah foto {idx+1} dari {len(images)}...")
                img_bytes = img.get('bytes')
                if img_bytes:
                    name = await asyncio.to_thread(_upload_foto, img_bytes, gcs_token, device_id)
                    if name: 
                        image_names.append(name)
                await asyncio.sleep(1.5)

            if not image_names:
                return "failed", "Gagal mengunggah foto referensi ke server."

        await update_status("Submitting prompt ke server AI...")
        # MENERIMA PESAN ERROR DARI FUNGSI
        history_id, err_msg = await asyncio.to_thread(_submit_task, image_names, prompt, device_id, ticket, ratio)
        
        # Perubahan: Jika gagal, err_msg berisi message dari server (misal: "Please try again after changing the image")
        if not history_id: 
            return "failed", err_msg

        await asyncio.to_thread(requests.post, "https://vivago.ai/api/gw/v3/candidate/speed_up_new", headers=get_headers(device_id, "1"), json={"history_id": history_id}, cookies={"ticket": ticket}, timeout=30)

        content_id = await _wait_for_content_async(history_id, device_id, ticket)
        if not content_id: 
            return "failed", "Proses render timeout (Melewati 8 menit)."

        result_url = await asyncio.to_thread(_get_result, user_id, content_id, device_id, ticket)
        if result_url: 
            return "success", result_url
        else: 
            return "failed", "Berhasil diproses, namun gagal mengambil URL gambar."

    except Exception as e:
        return "failed", f"System Error: {str(e)}"