import os
import json
import requests
import datetime
import pytz
from fastapi import FastAPI, Request, HTTPException
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

app = FastAPI()
TZ = pytz.timezone("America/Argentina/Buenos_Aires")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

def google_clients():
    sa_info = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets

def drive_upload(drive, content: bytes, content_type: str, filename: str) -> str:
    folder_id = os.environ["DRIVE_FOLDER_ID"]
    media = MediaInMemoryUpload(content, mimetype=content_type, resumable=False)
    file_metadata = {"name": filename, "parents": [folder_id]}
    created = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return created["webViewLink"]

def sheets_append_row(sheets, values):
    sheet_id = os.environ["SHEET_ID"]
    sheet_range = os.environ.get("SHEET_RANGE", "Ingresos!A:E")
    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=sheet_range,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()

def telegram_get_file_path(file_id: str) -> str:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getFile failed: {data}")
    return data["result"]["file_path"]

def telegram_download_file(file_path: str) -> tuple[bytes, str]:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    content_type = r.headers.get("Content-Type", "application/octet-stream")
    return r.content, content_type

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    payload = await request.json()

    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = (msg.get("chat") or {}).get("id")
    caption = (msg.get("caption") or "").strip()

    # Foto: Telegram manda array "photo" con varios tamaños. Tomamos el más grande.
    photos = msg.get("photo") or []
    if not photos:
        # Si querés admitir "document" también (PDF/JPG enviado como archivo), avísame y lo agrego.
        return {"ok": True}

    file_id = photos[-1]["file_id"]

    drive, sheets = google_clients()

    file_path = telegram_get_file_path(file_id)
    content, content_type = telegram_download_file(file_path)

    now = datetime.datetime.now(TZ)
    proveedor = caption  # mismo criterio: caption = proveedor

    # extensión desde file_path (si existe)
    ext = "jpg"
    if "." in file_path:
        ext = file_path.split(".")[-1].lower()

    filename = f"remito_{now.strftime('%Y%m%d_%H%M%S')}_{chat_id}.{ext}"
    drive_link = drive_upload(drive, content, content_type, filename)

    row = [
        drive_link,
        now.strftime("%Y-%m-%d %H:%M"),
        proveedor,
        False,
        "",
    ]
    sheets_append_row(sheets, row)

    return {"ok": True}
