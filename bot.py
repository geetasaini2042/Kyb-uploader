import os
import time
import math
import asyncio
import threading
import logging
import html
import re
import subprocess
import shutil
import base64
import json
import gc
import socket
from urllib.parse import urlparse, parse_qs, unquote
import aiohttp
import aiofiles
from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
import yt_dlp
from dotenv import load_dotenv

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv()

# --- USER CONFIGURATION (FROM .ENV) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_ALIAS = os.getenv("BOT_ALIAS", "bot_default")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")

# Parse AUTH_USERS from comma-separated string to list of integers
auth_users_str = os.getenv("AUTH_USERS", "")
AUTH_USERS = [int(x) for x in auth_users_str.split(",") if x.strip().isdigit()]

if not BOT_TOKEN or not API_ID:
    print("CRITICAL: Check your .env file. BOT_TOKEN or API_ID missing.")
    exit()

# --------------------------

# --- PATHS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, f"downloads_{BOT_ALIAS}")
LOG_FILE = os.path.join(SCRIPT_DIR, f"process_log_{BOT_ALIAS}.json")
CACHE_FILE = os.path.join(SCRIPT_DIR, "file_id_map.json")

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# --- CRYPTO ---
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except ImportError:
    print("CRITICAL: pip install pycryptodome")
    exit()

if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

# --- INIT JSON ---
def init_json():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            json.dump({}, f)
    if not os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'w') as f:
            json.dump({}, f)
init_json()

# --- GLOBALS ---
task_queue = asyncio.Queue()
stopped_contexts = set()
progress_map = {}
waiting_for_txt_users = {}

# --- UNIVERSAL THREAD HELPERS ---
def get_thread_id(message):
    return getattr(message, "message_thread_id", None)

def get_context_id(chat_id, topic_id):
    tid = topic_id if topic_id else 0
    return f"{chat_id}_{tid}"

# --- SMART SENDERS (TOPIC & CHANNEL FIX) ---
async def safe_send_message(client, chat_id, text, topic_id=None, reply_to_id=None, **kwargs):
    try:
        if topic_id:
            return await client.send_message(chat_id, text, message_thread_id=topic_id, **kwargs)
    except Exception: pass 
    try:
        if reply_to_id:
            return await client.send_message(chat_id, text, reply_to_message_id=reply_to_id, **kwargs)
    except Exception: pass
    return await client.send_message(chat_id, text, **kwargs)

async def safe_send_video(client, chat_id, video, caption, topic_id=None, reply_to_id=None, **kwargs):
    try:
        if topic_id:
            return await client.send_video(chat_id, video=video, caption=caption, message_thread_id=topic_id, **kwargs)
    except Exception: pass
    try:
        if reply_to_id:
            return await client.send_video(chat_id, video=video, caption=caption, reply_to_message_id=reply_to_id, **kwargs)
    except Exception: pass
    return await client.send_video(chat_id, video=video, caption=caption, **kwargs)

async def safe_send_document(client, chat_id, document, caption, topic_id=None, reply_to_id=None, **kwargs):
    try:
        if topic_id:
            return await client.send_document(chat_id, document=document, caption=caption, message_thread_id=topic_id, **kwargs)
    except Exception: pass
    try:
        if reply_to_id:
            return await client.send_document(chat_id, document=document, caption=caption, reply_to_message_id=reply_to_id, **kwargs)
    except Exception: pass
    return await client.send_document(chat_id, document=document, caption=caption, **kwargs)

# --- JSON HELPERS ---
def load_json(filename):
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f: return json.load(f)
    except: pass
    return {}

def save_json(filename, data):
    try:
        temp = filename + ".tmp"
        with open(temp, 'w') as f: json.dump(data, f, indent=4)
        os.replace(temp, filename)
    except: pass

def get_cached_file_id(url):
    return load_json(CACHE_FILE).get(url)

def save_file_id_to_cache(url, file_id):
    d = load_json(CACHE_FILE); d[url] = file_id; save_json(CACHE_FILE, d)

def is_processed(url):
    return load_json(LOG_FILE).get(url, False)

def mark_as_processed(url):
    d = load_json(LOG_FILE); d[url] = True; save_json(LOG_FILE, d)

def clear_process_log():
    save_json(LOG_FILE, {})

def clean_error_msg(txt):
    return re.sub(r'\x1b\[[0-9;]*m', '', str(txt))[:1000]

# --- NETWORK FIX ---
def create_session():
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    timeout = aiohttp.ClientTimeout(total=1800, connect=30, sock_read=60)
    return aiohttp.ClientSession(connector=connector, timeout=timeout)

# --- FLASK ---
web_app = Flask(__name__)
@web_app.route('/')
def home(): return f"{BOT_ALIAS} Running", 200

def start_keep_alive():
    port = int(os.environ.get("PORT", 8080))
    t = threading.Thread(target=lambda: web_app.run(host='0.0.0.0', port=port), daemon=True)
    t.start()

app = Client(f"my_bot_{BOT_ALIAS}", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- DECRYPT ---
def decrypt_appx(enc_str):
    try:
        d = enc_str.split(':')
        cipher = AES.new(b'638udh3829162018', AES.MODE_CBC, base64.b64decode(d[1]))
        return unpad(cipher.decrypt(base64.b64decode(d[0])), AES.block_size).decode('utf-8')
    except: return None

async def get_appx_stream_url(url):
    try:
        # Example URL: https://appx.co.in/appx/rozgarapinew.teachx.in/399.0.0.221335.m3u8?userid=184
        p = urlparse(url)
        path_parts = p.path.strip("/").split("/")
        
        # 1. EXTRACT VIDEO ID
        vid = None
        # Usually the last part contains the ID: 399.0.0.221335.m3u8
        last_seg = path_parts[-1]
        for x in reversed(last_seg.split('.')):
            if x.isdigit() and len(x) > 4: 
                vid = x; break
        
        # 2. EXTRACT API HOST DYNAMICALLY
        # Logic: Find 'appx' in path, the next element is the API host
        api_host = "rozgarapinew.teachx.in" # Fallback
        if "appx" in path_parts:
            try:
                idx = path_parts.index("appx")
                if len(path_parts) > idx + 1:
                    api_host = path_parts[idx+1]
            except: pass

        qs = parse_qs(p.query)
        uid = qs.get('userid',[''])[0]; pdf = qs.get('pdf',[''])[0]
        
        if not vid or not uid: return None, None

        # Construct Dynamic API
        api = f"https://{api_host}/get/fetchVideoDetailsById?course_id={uid}&video_id={vid}&ytflag=0&folder_wise_course=0"
        
        h = {'Auth-Key':'appxapi','User-ID':'12374033','Authorization':'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjEyMzc0MDMzIiwiZW1haWwiOiJHZWV0YXNhaW5pQGdhbWlsLmNvbSIsInRpbWVzdGFtcCI6MTc2MzM2NDk4OCwidGVuYW50VHlwZSI6InVzZXIiLCJ0ZW5hbnROYW1lIjoicm96Z2FyX2RiIiwidGVuYW50SWQiOiIiLCJkaXNwb3NhYmxlIjpmYWxzZX0.V5y8uL-mqzOqwb5Um7mbe4SnjwSymkN51cTifGgMykA','source':'website','Client-Service':'Appx','User-Agent':'Mozilla/5.0'}
        
        async with create_session() as s:
            async with s.get(api, headers=h) as r:
                if r.status!=200: return None, None
                d = await r.json()
        
        if d.get('status')==200 and 'data' in d:
            enc = None
            if pdf=='1': enc = d['data'].get('pdf_link')
            elif pdf=='2': enc = d['data'].get('pdf_link2')
            else:
                links = d['data'].get('download_links',[])
                enc = next((i['path'] for i in links if i['quality']=='720p'), links[0]['path'] if links else None)
            
            if enc: return decrypt_appx(enc), {"Referer": "https://appx-play.akamai.net.in/"}
    except: pass
    return None, None

# --- DOWNLOADER ---
def clean_filename(n):
    if not n: return "Untitled"
    if "||" in n: n = n.split("||")[-1]
    return re.sub(r'[\\/*?:"<>|]', '', n).replace("\n", " ").strip() or "Untitled"

async def check_perm(c, m):
    if m.chat.type == enums.ChatType.PRIVATE and m.from_user.id not in AUTH_USERS:
        await m.reply_text("❌ Unauthorized"); return False
    return m.from_user.id in AUTH_USERS

async def get_thumb(vid_path, thumb_path):
    try:
        subprocess.run(["ffmpeg","-ss","5","-i",vid_path,"-vframes","1","-q:v","2",thumb_path,"-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(thumb_path): return thumb_path
    except: pass
    return None

async def dl_thumb(url, path):
    try:
        async with create_session() as s:
            async with s.get(url) as r:
                if r.status==200: 
                    with open(path, 'wb') as f: f.write(await r.read())
    except: pass

async def prog_bar(cur, tot, msg, act, fname):
    try:
        now = time.time()
        if msg.id not in progress_map: progress_map[msg.id] = {'t': now, 's': 0}
        last = progress_map[msg.id]
        
        if (now - last['t']) < 10 and cur != tot: return

        spd = (cur - last['s']) / (now - last['t']) if now > last['t'] else 0
        progress_map[msg.id] = {'t': now, 's': cur}
        
        pct = (cur * 100 / tot) if tot > 0 else 0
        def hb(s):
            for u in ['','Ki','Mi','Gi']:
                if s < 1024: return f"{s:.2f}{u}B"
                s /= 1024
            return f"{s:.2f}TiB"

        bar = "●" * int(pct/10) + "○" * (10 - int(pct/10))
        try:
            await msg.edit(f"📂 <b>{html.escape(fname)}</b>\n{act}...\n[{bar}] {pct:.2f}%\n💾 {hb(cur)} / {hb(tot)}\n🚀 {hb(spd)}/s", parse_mode=enums.ParseMode.HTML)
        except FloodWait: pass
        except MessageNotModified: pass
    except: pass

# --- PROCESS TASK ---
async def process_task(client, task):
    logger.info(f"Processing Index {task['index']}")
    chat_id = task['chat_id']
    topic_id = task['topic_id']
    original_msg_id = task['msg_id']
    
    ctx = get_context_id(chat_id, topic_id)
    url_d = task['data']
    idx = task['index']
    
    if ctx in stopped_contexts: return

    raw_url = url_d['url']
    name = url_d.get('name', 'Unknown')
    fname = clean_filename(name)
    caption = f"INDEX - {idx}\n\nFILE NAME :- {html.escape(name)}"

    if is_processed(raw_url): return

    try:
        status = await safe_send_message(
            client, chat_id, 
            text=f"⏳ <b>Init:</b>\n{html.escape(name)}", 
            topic_id=topic_id, 
            reply_to_id=original_msg_id,
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Cannot send init msg: {e}")
        return

    cid = get_cached_file_id(raw_url)
    if cid:
        try:
            try: await status.edit(f"🚀 <b>Fast Send:</b> {idx}", parse_mode=enums.ParseMode.HTML)
            except: pass
            
            try: 
                await safe_send_video(client, chat_id, video=cid, caption=caption, topic_id=topic_id, reply_to_id=original_msg_id, parse_mode=enums.ParseMode.HTML)
            except: 
                await safe_send_document(client, chat_id, document=cid, caption=caption, topic_id=topic_id, reply_to_id=original_msg_id, parse_mode=enums.ParseMode.HTML)
            
            await status.delete()
            mark_as_processed(raw_url)
            await asyncio.sleep(15)
            return
        except: pass

    ts = int(time.time())
    headers = {'Referer': url_d.get('referer')} if url_d.get('referer') else {}
    final_url = raw_url
    success = False
    last_err = ""

    for attempt in range(1, 4):
        if ctx in stopped_contexts: 
            try: await status.edit("🛑 Stopped.")
            except: pass
            return
        
        fpath = None; tpath = None
        try:
            is_pdf = False
            # Check for Appx pattern (url contains appx or similar)
            if "appx" in raw_url:
                if attempt > 1: 
                    try: await status.edit(f"🔄 Retry {attempt} Resolving...")
                    except: pass
                d_url, head = await get_appx_stream_url(raw_url)
                if d_url: 
                    final_url = d_url; headers.update(head)
                    if d_url.split('?')[0].endswith('.pdf'): is_pdf = True
                else: 
                    if 'pdf' in parse_qs(urlparse(raw_url).query): is_pdf = True
            
            if is_pdf and not fname.endswith('.pdf'): fname += ".pdf"
            
            tid = f"{fname[:10]}_{ts}_{attempt}"
            fpath = os.path.join(DOWNLOAD_DIR, tid + (".pdf" if is_pdf else ""))
            tpath = os.path.join(DOWNLOAD_DIR, tid + "_thumb.jpg")

            if url_d.get('thumb_url') and not is_pdf: await dl_thumb(url_d['thumb_url'], tpath)

            progress_map[status.id] = {'t': time.time(), 's': 0}
            
            # DOWNLOAD
            if (not is_pdf) and (".m3u8" in final_url or "youtu" in final_url):
                try: await status.edit(f"⬇️ Try {attempt}: DL Video...")
                except: pass
                fpath += ".mp4"
                opts = {
                    'format':'best','outtmpl':fpath,'quiet':True,'nocheckcertificate':True,'http_headers':headers,
                    'socket_timeout':15,'retries':5, 'force_ipv4': True, 'nopart': True
                }
                def hook(d):
                    if ctx in stopped_contexts: raise Exception("Stop")
                    if d['status']=='downloading':
                        asyncio.run_coroutine_threadsafe(prog_bar(d.get('downloaded_bytes',0), d.get('total_bytes') or d.get('total_bytes_estimate') or 0, status, f"⬇️ Try {attempt}", fname), client.loop)
                opts['progress_hooks'] = [hook]
                await asyncio.get_event_loop().run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).download([final_url]))
            else:
                try: await status.edit(f"⬇️ Try {attempt}: DL File...")
                except: pass
                async with create_session() as s:
                    async with s.get(final_url, headers=headers) as r:
                        if r.status!=200: raise Exception("HTTP Error")
                        tot = int(r.headers.get("Content-Length",0)); cur = 0
                        async with aiofiles.open(fpath, "wb") as f:
                            async for chunk in r.content.iter_chunked(1024*1024):
                                if ctx in stopped_contexts: raise Exception("Stop")
                                await f.write(chunk); cur += len(chunk)
                                await prog_bar(cur, tot, status, f"⬇️ Try {attempt}", fname)

            if not os.path.exists(fpath) or os.path.getsize(fpath)==0: raise Exception("DL Failed")

            # UPLOAD
            try: await status.edit(f"⬆️ Try {attempt}: Uploading...")
            except: pass
            if not is_pdf and not os.path.exists(tpath): await get_thumb(fpath, tpath)

            async def up_prog(c, t):
                if ctx in stopped_contexts: client.stop_transmission()
                await prog_bar(c, t, status, f"⬆️ Try {attempt}", fname)

            upload_task = None
            if not is_pdf and fpath.endswith(('.mp4','.mkv')):
                args = {"chat_id":chat_id,"video":fpath,"caption":caption,"supports_streaming":True,"progress":up_prog,"topic_id":topic_id,"reply_to_id":original_msg_id, "parse_mode":enums.ParseMode.HTML}
                if os.path.exists(tpath): args["thumb"] = tpath
                upload_task = safe_send_video(client, **args)
            else:
                args = {"chat_id":chat_id,"document":fpath,"caption":caption,"progress":up_prog,"topic_id":topic_id,"reply_to_id":original_msg_id, "parse_mode":enums.ParseMode.HTML}
                if os.path.exists(tpath): args["thumb"] = tpath
                upload_task = safe_send_document(client, **args)

            try:
                sent = await asyncio.wait_for(upload_task, timeout=600)
                if sent:
                    fid = sent.video.file_id if sent.video else sent.document.file_id
                    save_file_id_to_cache(raw_url, fid)
                    mark_as_processed(raw_url)
                    success = True
                    await status.delete()
                    await asyncio.sleep(2)
                    break
            except asyncio.TimeoutError:
                raise Exception("Upload Timeout")
            except FloodWait as f:
                if f.value > 300: raise Exception("FloodWait > 5min")
                await asyncio.sleep(f.value + 2)
                continue

        except Exception as e:
            last_err = str(e)
            if "Stop" in str(e): 
                try: await status.edit("🛑 Stopped.")
                except: pass
                break
            if attempt < 3: await asyncio.sleep(3)
        finally:
            if fpath and os.path.exists(fpath): os.remove(fpath)
            if tpath and os.path.exists(tpath): os.remove(tpath)
            gc.collect()

    if not success and ctx not in stopped_contexts:
        try:
            await status.edit(f"Index - {idx}\n\n❌ <b>FAILED PERMANENTLY</b>\nFile: {html.escape(fname)}\nReason: {html.escape(clean_error_msg(last_err))}", parse_mode=enums.ParseMode.HTML)
        except: pass

# --- WORKER ---
async def worker():
    logger.info(f"{BOT_ALIAS} WORKER STARTED")
    while True:
        task = await task_queue.get()
        ctx = get_context_id(task['chat_id'], task['topic_id'])
        
        if ctx in stopped_contexts: 
            task_queue.task_done(); continue

        try:
            await asyncio.wait_for(process_task(app, task), timeout=2700)
        except Exception as e:
            logger.error(f"WORKER ERROR: {e}")
        
        task_queue.task_done()
        gc.collect()

# --- COMMANDS ---
def parse_line(text):
    d = {'url': text.strip(), 'name': None, 'referer': None, 'thumb_url': None}
    if "singodiya" in text:
        try:
            q = parse_qs(urlparse(text).query)
            d.update({'url': q.get('url',[text])[0], 'name': q.get('title',[None])[0], 'thumb_url': q.get('thumbnail',[None])[0]})
        except: pass
    elif ":" in text and "http" in text:
        p = text.split(":", 1)
        if "http" not in p[0]: d.update({'name': p[0].strip(), 'url': p[1].strip()})
    return d

@app.on_message(filters.command("txt"))
async def txt_cmd(c, m):
    if not await check_perm(c, m): return
    if len(m.command)>1 and m.command[1]==BOT_ALIAS:
        tid = get_thread_id(m)
        ctx = get_context_id(m.chat.id, tid)
        if ctx in stopped_contexts: stopped_contexts.remove(ctx)
        waiting_for_txt_users[m.chat.id] = m.from_user.id
        await safe_send_message(c, m.chat.id, f"🤖 <b>{BOT_ALIAS} Ready!</b> Send TXT.", topic_id=tid, reply_to_id=m.id, parse_mode=enums.ParseMode.HTML)

@app.on_message(filters.command("stop"))
async def stop_cmd(c, m):
    if await check_perm(c, m):
        tid = get_thread_id(m)
        ctx = get_context_id(m.chat.id, tid)
        stopped_contexts.add(ctx)
        await safe_send_message(c, m.chat.id, "🛑 Stopping Topic...", topic_id=tid, reply_to_id=m.id)

@app.on_message(filters.document)
async def doc_handler(c, m):
    if not await check_perm(c, m) or not m.document.file_name.endswith(".txt"): return
    if waiting_for_txt_users.get(m.chat.id) != m.from_user.id: return
    del waiting_for_txt_users[m.chat.id]

    origin_tid = get_thread_id(m)
    msg = await safe_send_message(c, m.chat.id, "📥 Reading...", topic_id=origin_tid, reply_to_id=m.id)
    path = await m.download()
    
    if task_queue.empty(): clear_process_log()
    
    cnt = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if "http" in line:
                cnt += 1
                await task_queue.put({
                    'chat_id': m.chat.id, 
                    'topic_id': origin_tid, 
                    'msg_id': m.id, 
                    'data': parse_line(line.strip()), 
                    'index': cnt
                })
    
    os.remove(path)
    await msg.edit(f"✅ <b>{BOT_ALIAS}: Queued {cnt}.</b>")

if __name__ == "__main__":
    start_keep_alive()
    loop = asyncio.get_event_loop()
    loop.create_task(worker())
    app.run()
