# -*- coding: utf-8 -*-
"""
ชั้นฐานข้อมูล (SQLite)
- ใช้ SQLite เพราะรองรับหลายคนเขียนพร้อมกันได้ (ล็อกให้อัตโนมัติ) ต่างจากไฟล์ JSON
  ที่ถ้าหลายคนเซฟพร้อมกันจะทับกัน -> ข้อมูลหาย
- ตารางทั้งหมดอยู่ในไฟล์เดียว: data/hub.db  (สำรองง่าย แค่ก๊อปไฟล์นี้)

[ฉบับเผยแพร่ ต.ค. 2569] ตัดตารางและค่าตั้งของแชท คลังไฟล์ ข้อมูลกลาง (Data API) สิทธิ์ข้อมูลกลาง
และ tunnel ออก · อีเมลผู้ส่ง OTP และชื่อองค์กรเป็นค่าสมมุติ · จุดที่ตัดมีคำว่า "[ตัดออก]" กำกับ
"""
import os
import json
import sqlite3
import datetime
from werkzeug.security import generate_password_hash, check_password_hash

import config


def get_conn():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")      # ให้หลายคนอ่าน/เขียนพร้อมกันลื่นขึ้น
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT DEFAULT 'user'        -- 'admin' | 'user'
);

-- บันทึกการใช้งาน: ใคร/เครื่องไหน(IP) เปิดโปรแกรมตัวไหน เมื่อไหร่
CREATE TABLE IF NOT EXISTS access_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,                 -- เวลา ISO
    ip        TEXT,
    username  TEXT,
    program   TEXT,                          -- slug ของโปรแกรม
    path      TEXT,
    is_page   INTEGER DEFAULT 1              -- 1 = เปิดหน้าโปรแกรม, 0 = เรียก api เบื้องหลัง
);
CREATE INDEX IF NOT EXISTS idx_acc_ts  ON access_log(ts);
CREATE INDEX IF NOT EXISTS idx_acc_prog ON access_log(program);

-- โปรแกรมที่ผู้ใช้แต่ละคน "มองเห็น/เข้าได้"
CREATE TABLE IF NOT EXISTS user_programs (
    username TEXT NOT NULL,
    slug     TEXT NOT NULL,
    visible  INTEGER DEFAULT 1,
    PRIMARY KEY (username, slug)
);

-- รหัส OTP สำหรับยืนยันอีเมลตอนสมัคร
CREATE TABLE IF NOT EXISTS otp_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    code       TEXT NOT NULL,
    purpose    TEXT DEFAULT 'register',
    expires_at TEXT,
    used       INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email);

-- คลังอุปกรณ์: เครื่องที่เคยเข้ามาใช้งาน (ระบุด้วย device_id ที่ฝังใน cookie)
CREATE TABLE IF NOT EXISTS devices (
    device_id      TEXT PRIMARY KEY,
    label          TEXT,
    device_type    TEXT DEFAULT '',          -- เช่น PC / Notebook / Tablet / Phone
    status         TEXT DEFAULT 'allowed',   -- 'allowed' | 'blocked'
    max_distance_m INTEGER,                   -- ระยะใช้งานที่กำหนด (อ้างอิง/บันทึกไว้)
    note           TEXT DEFAULT '',
    last_ip        TEXT,
    last_host      TEXT,
    last_user      TEXT,
    user_agent     TEXT,
    first_seen     TEXT,
    last_seen      TEXT
);

-- ตั้งค่าระบบ (key/value) — วง LAN ที่อนุญาต, เวลา session, OTP, SMTP ฯลฯ
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- อุปกรณ์ที่ผ่าน OTP ตอน login แล้ว (จำไว้ไม่ต้องถาม OTP ซ้ำ)
CREATE TABLE IF NOT EXISTS otp_trust (
    username    TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    expires_at  TEXT,                 -- NULL = ไม่หมดอายุ (ไม่ต้องขออีก)
    created_at  TEXT,
    PRIMARY KEY (username, device_id)
);

-- [ตัดออก] ตารางของแชท: messages · chat_groups · chat_group_members · chat_pins · chat_reads

-- รูปโปรไฟล์ผู้ใช้ (เก็บเป็น BLOB ใน hub.db -> ระบบสำรองข้อมูลคลุมอัตโนมัติ)
CREATE TABLE IF NOT EXISTS user_avatars (
    username   TEXT PRIMARY KEY,
    mime       TEXT,
    data       BLOB,
    updated_at TEXT
);

-- กันเดารหัส (brute-force): นับครั้งที่ล็อกอินผิดต่อ key (IP) + เวลาที่ถูกล็อก
CREATE TABLE IF NOT EXISTS login_throttle (
    key          TEXT PRIMARY KEY,
    fails        INTEGER DEFAULT 0,
    first_fail   TEXT,
    locked_until TEXT
);

-- บันทึกเหตุการณ์ทั้งระบบ (audit/event log) — โชว์ในแท็บ Log แบบ terminal
CREATE TABLE IF NOT EXISTS event_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    level    TEXT DEFAULT 'info',     -- info | warn | error
    category TEXT DEFAULT 'system',   -- request | auth | denied | admin | backup | tunnel | program | system
    actor    TEXT DEFAULT '-',        -- ชื่อผู้ใช้ (ถ้ามี)
    ip       TEXT DEFAULT '',
    method   TEXT DEFAULT '',
    path     TEXT DEFAULT '',
    status   INTEGER,
    message  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_event_id ON event_log(id);

-- ประกาศ/อัปเดตโปรแกรม (แสดงท้ายหน้ารวม) — วันที่ + เนื้อหา
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT,
    body       TEXT,
    created_by TEXT,
    created_at TEXT
);

-- [ตัดออก] ตารางของข้อมูลกลาง (app_data · app_files) · สิทธิพิเศษรายโปรแกรม (priv_access)
--          · คลังไฟล์ (hub_files) · บันทึกสิทธิ์ข้อมูลกลาง (acl_log)
"""


# ฟิลด์ที่เพิ่มภายหลังในตาราง users / access_log (migration แบบ ADD COLUMN)
_USER_COLS = [
    ("email", "TEXT"),
    ("department", "TEXT"),
    ("status", "TEXT DEFAULT 'active'"),       # 'active' | 'pending' | 'disabled'
    ("session_timeout_min", "INTEGER"),        # NULL = ใช้ค่ากลางจาก settings
    ("email_verified", "INTEGER DEFAULT 0"),
    ("created_at", "TEXT"),
    ("approved_by", "TEXT"),
    ("approved_at", "TEXT"),
]
_DEFAULT_SETTINGS = {
    "allowed_networks": json.dumps(
        ["127.0.0.1/32", "::1/128", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]),
    "enforce_network": "1",          # บังคับให้เข้าจากวง LAN ที่อนุญาตเท่านั้น
    "default_session_min": "480",    # อายุ session หลัง login (นาที) ค่าเริ่มต้น
    "otp_required": "1",             # ต้องยืนยัน OTP ตอนสมัครไหม
    "otp_ttl_min": "10",             # อายุรหัส OTP (นาที)
    # SMTP — ตั้งค่าเริ่มต้นเป็น Gmail (เหลือใส่ App Password)
    "smtp_host": "smtp.gmail.com", "smtp_port": "587",
    "smtp_user": "otp-sender@example.com", "smtp_pass": "",
    "smtp_from": "otp-sender@example.com", "smtp_tls": "1",
    # OTP ตอน login (2FA)
    "login_otp": "1",                # ต้องกรอก OTP ตอน login ไหม
    "otp_remember_mode": "duration", # 'duration' = จำตามเวลาที่ตั้ง | 'forever' = ไม่ต้องขออีก
    "otp_remember_days": "30",       # ถ้า duration: จำอุปกรณ์กี่วัน
    # Template อีเมล OTP (ใช้ {code} และ {ttl} เป็นตัวแทน)
    "otp_subject": "รหัสยืนยัน (OTP) — ระบบงานภายในบริษัทตัวอย่าง",
    "otp_body": ("เรียนผู้ใช้งาน\n\nรหัสยืนยัน (OTP) ของคุณคือ: {code}\n"
                 "รหัสนี้จะหมดอายุภายใน {ttl} นาที\n\n"
                 "หากคุณไม่ได้เป็นผู้ร้องขอ กรุณาเพิกเฉยต่ออีเมลฉบับนี้\n"
                 "— ระบบงานภายใน (Internal Tools)"),
    # โหมดการเข้าถึง: 'lan' = ใช้ในวง LAN (HTTP, เชื่อ IP ตรง) | 'public' = เปิดระยะไกลผ่าน HTTPS tunnel (proxy-aware)
    "access_mode": "lan",
    # ความปลอดภัยการเข้าสู่ระบบ
    "login_throttle": "1",        # กันเดารหัส: ล็อกชั่วคราวเมื่อพยายามผิดบ่อย
    "login_max_fails": "10",      # ผิดกี่ครั้งจึงล็อก
    "login_lock_min": "15",       # ล็อกนานกี่นาที
    "allow_register": "1",        # อนุญาตให้สมัครสมาชิกเอง (0 = แอดมินสร้างเท่านั้น)
    "bind_localhost": "0",        # โหมด public: ผูกเฉพาะ 127.0.0.1 (เข้าผ่าน tunnel เท่านั้น)
    # Log
    "log_mode": "all",            # all = เก็บทุก request | important = เฉพาะสำคัญ (auth/denied/admin/error)
    "log_keep": "8000",           # เก็บล่าสุดกี่แถว (วนทับของเก่า)
    # [ตัดออก] ค่าตั้งของ Cloudflare Tunnel · คลังไฟล์ · สิทธิ์ข้อมูลกลาง
}


def _table_cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn):
    """เพิ่มคอลัมน์/ตั้งค่าเริ่มต้นที่เพิ่มภายหลัง — ปลอดภัยกับ hub.db เดิม"""
    ucols = _table_cols(conn, "users")
    for name, decl in _USER_COLS:
        if name not in ucols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
    if "device_id" not in _table_cols(conn, "access_log"):
        conn.execute("ALTER TABLE access_log ADD COLUMN device_id TEXT")
    if "usage_scope" not in _table_cols(conn, "devices"):
        conn.execute("ALTER TABLE devices ADD COLUMN usage_scope TEXT")
    # [ตัดออก] การย้ายสคีมาของ priv_access และ app_data (เลขรุ่น rev + ทริกเกอร์ compare-and-swap)
    # ผู้ใช้เดิมทั้งหมดถือว่า active + อีเมลยืนยันแล้ว (กันโดนล็อกเอาต์หลังอัปเกรด)
    conn.execute("UPDATE users SET status='active' WHERE status IS NULL OR status=''")
    conn.execute("UPDATE users SET email_verified=1 WHERE email_verified IS NULL")
    # ค่าตั้งต้นของ settings (ใส่เฉพาะที่ยังไม่มี)
    for k, v in _DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
    # ครอบคลุม DB เดิม: ถ้าแอดมินเริ่มต้นยังใช้รหัสเริ่มต้นอยู่จริง -> ตั้ง flag บังคับเปลี่ยน
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE username=?",
                           (config.DEFAULT_ADMIN_USER,)).fetchone()
        if row and check_password_hash(row["password_hash"], config.DEFAULT_ADMIN_PASS):
            conn.execute("INSERT INTO settings(key,value) VALUES ('admin_default_pw','1') "
                         "ON CONFLICT(key) DO UPDATE SET value='1'")
    except Exception:
        pass
    conn.commit()


def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT INTO settings(key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def init_db():
    """สร้างตาราง + ใส่ข้อมูลตั้งต้น (รันซ้ำได้ ไม่พัง)"""
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)

    # --- ผู้ดูแลเริ่มต้น ---
    cur = conn.execute("SELECT COUNT(*) c FROM users")
    if cur.fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO users(username, password_hash, display_name, role) VALUES (?,?,?,?)",
            (config.DEFAULT_ADMIN_USER,
             generate_password_hash(config.DEFAULT_ADMIN_PASS),
             "ผู้ดูแลระบบ", "admin"),
        )
        # ยังเป็นรหัสเริ่มต้น -> บังคับเปลี่ยนก่อนใช้งาน (ล้าง flag เมื่อเปลี่ยนรหัสสำเร็จ)
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('admin_default_pw','1')")
        conn.commit()

    conn.close()
