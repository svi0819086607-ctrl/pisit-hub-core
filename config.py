# -*- coding: utf-8 -*-
"""ค่าตั้งกลางของระบบ — แก้ที่นี่ที่เดียว

[ฉบับเผยแพร่ ต.ค. 2569] ชื่อองค์กรและรหัสผู้ดูแลเริ่มต้นเป็นค่าสมมุติ
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# data/ = ข้อมูลรันไทม์ (ฐานข้อมูล / ไฟล์ / คีย์ลับ / สำรองข้อมูล)
# ตั้ง env HUB_DATA_DIR เพื่อย้ายออกนอก OneDrive หรือโฟลเดอร์ที่ sync
#   เหตุผล: OneDrive จับไฟล์ระหว่างเขียน -> SQLite (WAL) เสี่ยงพัง และ secret.key/hub.db
#           จะถูก sync ขึ้น cloud (คีย์เซ็น session + password hash รั่วได้)
#   วิธีใช้: ปิดเซิร์ฟเวอร์ -> ย้ายโฟลเดอร์ data เดิมไปที่ใหม่ (เช่น C:\HubData)
#           -> ตั้ง  setx HUB_DATA_DIR "C:\HubData"  แล้วเปิดใหม่
DATA_DIR = (os.environ.get("HUB_DATA_DIR") or "").strip() or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "hub.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
SEED_DIR = os.path.join(BASE_DIR, "data", "seed")     # เมล็ดข้อมูลตั้งต้น — มากับโค้ด ไม่ย้ายตาม HUB_DATA_DIR
HUB_FILES_DIR = os.path.join(DATA_DIR, "hub_files")   # ไฟล์แชท/Storage/Export ที่เก็บบนเซิร์ฟเวอร์

# โฟลเดอร์ที่ "โยนโปรแกรมลงไปแล้วระบบอ่านเอง"
PROGRAMS_DIR = os.path.join(BASE_DIR, "programs")

HOST = "0.0.0.0"
PORT = 8080

def _load_secret():
    """คีย์เซสชัน: ใช้ env HUB_SECRET ถ้ามี, ไม่งั้นสุ่มคีย์แรง ๆ เก็บถาวรใน data/secret.key
       (กันการปลอมเซสชัน — ดีกว่าค่า default ที่เดาได้ โดยเฉพาะเมื่อเปิดเข้าจากนอกวง)"""
    env = os.environ.get("HUB_SECRET")
    if env:
        return env
    path = os.path.join(DATA_DIR, "secret.key")
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                k = f.read().strip()
            if k:
                return k
        os.makedirs(DATA_DIR, exist_ok=True)
        import secrets
        k = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(k)
        return k
    except Exception as e:
        # fail-closed: ห้ามใช้คีย์ค่าคงที่ที่เดาได้ (ปลอม session admin ได้) — หยุดทำงานแทน
        raise RuntimeError(
            "อ่าน/สร้างคีย์ลับ (secret.key) ไม่สำเร็จ: %s\n"
            "แก้โดยตั้ง environment variable HUB_SECRET เป็นคีย์สุ่มยาว ๆ แล้วรันใหม่ เช่น:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"' % e
        )


SECRET_KEY = _load_secret()

ORG_NAME = "บริษัทตัวอย่าง"
ORG_SUBTITLE = "ระบบงานภายใน (Internal Tools)"

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "change-me"   # ค่าสมมุติ — ระบบบังคับให้เปลี่ยนก่อนใช้งาน

# ถือว่า "กำลังใช้งานอยู่" ถ้ามีการเรียกภายในกี่นาทีล่าสุด
ACTIVE_WINDOW_MIN = 15
