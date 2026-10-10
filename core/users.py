# -*- coding: utf-8 -*-
"""
จัดการผู้ใช้: สมัคร (รออนุมัติ) / อนุมัติ / แก้ไข / สิทธิ์มองเห็นโปรแกรม /
OTP ยืนยันอีเมล / อายุ session (เตะออกเมื่อหมดเวลา)
"""
import re
import json
import random
import smtplib
import datetime
from email.mime.text import MIMEText

from werkzeug.security import generate_password_hash, check_password_hash

import config
from core.db import get_conn, get_setting

# แผนกที่เลือกได้ตอนสมัคร
DEPARTMENTS = ["Hr", "Acc", "Sale", "DCC", "FDA", "Maintenance",
               "Purchasing", "Store", "QC", "Factory", "Stock"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ============================================================
#  ผู้ใช้ทั่วไป
# ============================================================
def get_user(username):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users():
    conn = get_conn()
    rows = conn.execute(
        "SELECT username, display_name, email, department, role, status, "
        "session_timeout_min, email_verified, created_at, approved_by, approved_at "
        "FROM users ORDER BY (status='pending') DESC, role='admin' DESC, username").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def validate_registration(username, password, confirm, email, department):
    if not username or not re.match(r"^[A-Za-z0-9._-]{3,32}$", username):
        return "ชื่อผู้ใช้ต้องเป็น a-z, 0-9, . _ - ยาว 3-32 ตัว"
    if get_user(username):
        return "มีชื่อผู้ใช้นี้แล้ว"
    if not email or not _EMAIL_RE.match(email):
        return "อีเมลไม่ถูกต้อง"
    if get_user_by_email(email):
        return "อีเมลนี้ถูกใช้สมัครแล้ว"
    if department not in DEPARTMENTS:
        return "กรุณาเลือกแผนกให้ถูกต้อง"
    if len(password or "") < 4:
        return "รหัสผ่านต้องยาวอย่างน้อย 4 ตัว"
    if password != confirm:
        return "รหัสผ่านสองช่องไม่ตรงกัน"
    return None


def create_user(username, password, display_name, email="", department="", role="user",
                status="active", email_verified=0, session_timeout_min=None, approved_by=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO users(username,password_hash,display_name,role,email,department,status,"
        "session_timeout_min,email_verified,created_at,approved_by,approved_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (username, generate_password_hash(password), display_name or username, role,
         email, department, status, session_timeout_min, 1 if email_verified else 0,
         _now(), approved_by, _now() if approved_by else None))
    conn.commit()
    conn.close()


def register_pending(username, password, display_name, email, department, email_verified=0):
    """สมัครจากภายนอก -> สถานะ pending รอแอดมินอนุมัติ"""
    create_user(username, password, display_name or username, email=email,
                department=department, role="user", status="pending",
                email_verified=email_verified)


def set_status(username, status, approver=None):
    conn = get_conn()
    if status == "active":
        conn.execute("UPDATE users SET status=?, approved_by=?, approved_at=? WHERE username=?",
                     (status, approver, _now(), username))
    else:
        conn.execute("UPDATE users SET status=? WHERE username=?", (status, username))
    conn.commit()
    conn.close()


def update_user(username, display_name=None, email=None, department=None, role=None,
                status=None, session_timeout_min="__keep__"):
    sets, vals = [], []
    if display_name is not None: sets.append("display_name=?"); vals.append(display_name)
    if email is not None:        sets.append("email=?");        vals.append(email)
    if department is not None:    sets.append("department=?");   vals.append(department)
    if role is not None:          sets.append("role=?");         vals.append(role)
    if status is not None:        sets.append("status=?");       vals.append(status)
    if session_timeout_min != "__keep__":
        sets.append("session_timeout_min=?"); vals.append(session_timeout_min)
    if not sets:
        return
    vals.append(username)
    conn = get_conn()
    conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE username=?", vals)
    conn.commit()
    conn.close()


def set_password(username, new_password):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                 (generate_password_hash(new_password), username))
    if username == config.DEFAULT_ADMIN_USER:
        conn.execute("INSERT INTO settings(key,value) VALUES ('admin_default_pw','0') "
                     "ON CONFLICT(key) DO UPDATE SET value='0'")
    conn.commit()
    conn.close()


def set_display_name(username, display_name):
    conn = get_conn()
    conn.execute("UPDATE users SET display_name=? WHERE username=?", (display_name, username))
    conn.commit()
    conn.close()


# ============================================================
#  รูปโปรไฟล์ (avatar) — เก็บเป็น BLOB ใน hub.db
# ============================================================
def set_avatar(username, mime, data):
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_avatars(username,mime,data,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET mime=excluded.mime, data=excluded.data, "
        "updated_at=excluded.updated_at",
        (username, mime, data, _now()))
    conn.commit()
    conn.close()


def get_avatar(username):
    conn = get_conn()
    r = conn.execute("SELECT mime, data, updated_at FROM user_avatars WHERE username=?",
                     (username,)).fetchone()
    conn.close()
    return dict(r) if r else None


def delete_avatar(username):
    conn = get_conn()
    conn.execute("DELETE FROM user_avatars WHERE username=?", (username,))
    conn.commit()
    conn.close()


def users_with_avatar():
    """เซ็ตของ username ที่มีรูปโปรไฟล์ (ใช้ตัดสินใจว่าจะโชว์รูปหรือตัวอักษรย่อ)"""
    conn = get_conn()
    rows = conn.execute("SELECT username FROM user_avatars").fetchall()
    conn.close()
    return {r["username"] for r in rows}


# [ตัดออก] รูปโปรไฟล์กลุ่มแชท (4 ฟังก์ชัน) และโควตาคลังไฟล์รายคน — ไม่อยู่ในฉบับเผยแพร่


def delete_user(username):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE username=?", (username,))
    conn.execute("DELETE FROM user_programs WHERE username=?", (username,))
    conn.commit()
    conn.close()


def count_admins():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND status='active'").fetchone()["c"]
    conn.close()
    return n


# ============================================================
#  สิทธิ์มองเห็นโปรแกรม
# ============================================================
def get_user_programs(username):
    """คืน dict {slug: visible(bool)} เท่าที่มีบันทึกไว้"""
    conn = get_conn()
    rows = conn.execute("SELECT slug, visible FROM user_programs WHERE username=?", (username,)).fetchall()
    conn.close()
    return {r["slug"]: bool(r["visible"]) for r in rows}


def set_user_programs(username, allowed_slugs, all_slugs):
    """เขียนสิทธิ์แบบชัดเจน: ทุกโปรแกรมจะมีแถว visible 1/0"""
    allowed = set(allowed_slugs)
    conn = get_conn()
    conn.execute("DELETE FROM user_programs WHERE username=?", (username,))
    for slug in all_slugs:
        conn.execute("INSERT INTO user_programs(username,slug,visible) VALUES (?,?,?)",
                     (username, slug, 1 if slug in allowed else 0))
    conn.commit()
    conn.close()


def visible_slugs(username, role, all_slugs):
    """โปรแกรมที่ผู้ใช้คนนี้เห็น/เข้าได้ (แอดมินเห็นทั้งหมด, ถ้ายังไม่เคยตั้ง = เห็นทั้งหมด)"""
    if role == "admin":
        return set(all_slugs)
    perms = get_user_programs(username)
    if not perms:
        return set(all_slugs)
    return {s for s, v in perms.items() if v} & set(all_slugs)


# ============================================================
#  อายุ session (เตะออกเมื่อหมดเวลา)
# ============================================================
def effective_timeout_min(username):
    """คืนจำนวนนาทีก่อนเตะออก — None = ไม่จำกัด (-1), ไม่ได้ตั้ง = ค่ากลาง"""
    u = get_user(username) or {}
    t = u.get("session_timeout_min")
    if t == -1:
        return None                     # ไม่จำกัด
    if t:
        return int(t)
    try:
        return int(get_setting("default_session_min", "480"))
    except Exception:
        return 480


def verify_password(username, password):
    """ตรวจรหัสผ่านปัจจุบัน (ใช้ตอนเปลี่ยนรหัส)"""
    u = get_user(username)
    return bool(u and check_password_hash(u["password_hash"], password))


# ============================================================
#  กันเดารหัส (brute-force throttle) — นับการล็อกอินผิดต่อ key (IP)
# ============================================================
def throttle_status(key):
    """คืน (locked, secs_left) — ถ้ายังถูกล็อกอยู่"""
    if not key:
        return False, 0
    conn = get_conn()
    r = conn.execute("SELECT locked_until FROM login_throttle WHERE key=?", (key,)).fetchone()
    conn.close()
    if r and r["locked_until"]:
        try:
            lu = datetime.datetime.fromisoformat(r["locked_until"])
            now = datetime.datetime.now()
            if lu > now:
                return True, int((lu - now).total_seconds())
        except Exception:
            pass
    return False, 0


def throttle_fail(key, max_fails=10, lock_min=15):
    """บันทึกการล็อกอินผิด 1 ครั้ง — ถ้าครบ max_fails จะล็อก lock_min นาที"""
    if not key:
        return
    now = datetime.datetime.now()
    conn = get_conn()
    r = conn.execute("SELECT fails, first_fail, locked_until FROM login_throttle WHERE key=?", (key,)).fetchone()
    if not r:
        conn.execute("INSERT INTO login_throttle(key,fails,first_fail,locked_until) VALUES (?,?,?,?)",
                     (key, 1, now.isoformat(timespec="seconds"), None))
    else:
        fails = (r["fails"] or 0)
        ff = r["first_fail"]
        # รีเซ็ตหน้าต่างนับใหม่ถ้าครั้งแรกเกิน lock_min นาทีมาแล้ว
        reset = True
        try:
            if ff and (now - datetime.datetime.fromisoformat(ff)).total_seconds() <= lock_min * 60:
                reset = False
        except Exception:
            reset = True
        if reset:
            fails, ff, lu = 1, now.isoformat(timespec="seconds"), None
        else:
            fails, lu = fails + 1, r["locked_until"]
        if fails >= max_fails:
            lu = (now + datetime.timedelta(minutes=lock_min)).isoformat(timespec="seconds")
            fails, ff = 0, now.isoformat(timespec="seconds")
        conn.execute("UPDATE login_throttle SET fails=?, first_fail=?, locked_until=? WHERE key=?",
                     (fails, ff, lu, key))
    conn.commit()
    conn.close()


def throttle_clear(key):
    """ล็อกอินสำเร็จ -> ล้างตัวนับของ key นี้"""
    if not key:
        return
    conn = get_conn()
    conn.execute("DELETE FROM login_throttle WHERE key=?", (key,))
    conn.commit()
    conn.close()


# ---- จำอุปกรณ์ที่ผ่าน OTP ตอน login แล้ว ----
def device_trusted(username, device_id):
    if not username or not device_id:
        return False
    conn = get_conn()
    row = conn.execute("SELECT expires_at FROM otp_trust WHERE username=? AND device_id=?",
                       (username, device_id)).fetchone()
    conn.close()
    if not row:
        return False
    exp = row["expires_at"]
    if not exp:                          # NULL = ไม่หมดอายุ
        return True
    return exp >= _now()


def trust_device(username, device_id):
    """บันทึกว่าอุปกรณ์นี้ผ่าน OTP แล้ว ตามโหมดที่ตั้ง (duration/forever)"""
    if not username or not device_id:
        return
    mode = get_setting("otp_remember_mode", "duration")
    if mode == "forever":
        expires = None
    else:
        days = int(get_setting("otp_remember_days", "30") or 30)
        expires = (datetime.datetime.now() + datetime.timedelta(days=days)).isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute("INSERT INTO otp_trust(username,device_id,expires_at,created_at) VALUES (?,?,?,?) "
                 "ON CONFLICT(username,device_id) DO UPDATE SET expires_at=excluded.expires_at",
                 (username, device_id, expires, _now()))
    conn.commit()
    conn.close()


def clear_trust(username, device_id=None):
    conn = get_conn()
    if device_id:
        conn.execute("DELETE FROM otp_trust WHERE username=? AND device_id=?", (username, device_id))
    else:
        conn.execute("DELETE FROM otp_trust WHERE username=?", (username,))
    conn.commit()
    conn.close()


def clear_all_trust():
    """ล้างอุปกรณ์ที่จำไว้ทั้งหมด -> ทุกบัญชีต้องกรอก OTP ใหม่ตอน login ครั้งถัดไป"""
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM otp_trust").fetchone()["c"]
    conn.execute("DELETE FROM otp_trust")
    conn.commit()
    conn.close()
    return n
    conn.close()


# ============================================================
#  OTP ยืนยันอีเมล
# ============================================================
def gen_otp(email, purpose="register"):
    code = "%06d" % random.randint(0, 999999)
    ttl = int(get_setting("otp_ttl_min", "10") or 10)
    expires = (datetime.datetime.now() + datetime.timedelta(minutes=ttl)).isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute("INSERT INTO otp_codes(email,code,purpose,expires_at,used,created_at) "
                 "VALUES (?,?,?,?,0,?)", (email, code, purpose, expires, _now()))
    conn.commit()
    conn.close()
    return code


def recent_otp_exists(email, purpose="login", within_seconds=25):
    """เพิ่งสร้าง/ส่ง OTP ให้ email+purpose นี้ภายใน N วินาทีที่ผ่านมาไหม
       — ใช้กันส่งอีเมลซ้ำเมื่อฟอร์มถูก submit สองรอบติดกัน (กดปุ่มรัว/เน็ตช้า)"""
    conn = get_conn()
    row = conn.execute("SELECT created_at FROM otp_codes WHERE email=? AND purpose=? "
                       "ORDER BY id DESC LIMIT 1", (email, purpose)).fetchone()
    conn.close()
    if not row or not row["created_at"]:
        return False
    try:
        t = datetime.datetime.fromisoformat(row["created_at"])
    except Exception:
        return False
    return (datetime.datetime.now() - t).total_seconds() < within_seconds


def verify_otp(email, code, purpose="register"):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM otp_codes WHERE email=? AND code=? AND purpose=? AND used=0 "
        "ORDER BY id DESC LIMIT 1", (email, (code or "").strip(), purpose)).fetchone()
    ok = False
    if row:
        if row["expires_at"] and row["expires_at"] >= _now():
            conn.execute("UPDATE otp_codes SET used=1 WHERE id=?", (row["id"],))
            conn.execute("UPDATE users SET email_verified=1 WHERE lower(email)=lower(?)", (email,))
            conn.commit()
            ok = True
    conn.close()
    return ok


def smtp_configured():
    """ต้องมี host + user + pass ครบจึงถือว่าพร้อมส่งจริง (ไม่งั้นอยู่โหมดทดสอบ)"""
    return all((get_setting(k, "") or "").strip() for k in ("smtp_host", "smtp_user", "smtp_pass"))


def render_otp_template(code):
    ttl = get_setting("otp_ttl_min", "10")
    subject = get_setting("otp_subject", "") or "รหัสยืนยัน (OTP)"
    body = get_setting("otp_body", "") or "รหัส OTP ของคุณคือ: {code} (หมดอายุใน {ttl} นาที)"
    repl = {"code": code, "ttl": ttl}
    for k, v in repl.items():
        subject = subject.replace("{%s}" % k, str(v))
        body = body.replace("{%s}" % k, str(v))
    return subject, body


def send_otp_email(email, code):
    """ส่ง OTP ทางอีเมล ถ้ายังไม่ตั้ง SMTP ครบจะคืน (False, ข้อความ) = โหมดทดสอบ"""
    if not smtp_configured():
        return False, "ยังไม่ได้ตั้งค่าอีเมล (SMTP) ให้ครบ — โหมดทดสอบ แสดงรหัสบนหน้าจอ"
    host = get_setting("smtp_host", "")
    port = int(get_setting("smtp_port", "587") or 587)
    user = get_setting("smtp_user", "")
    pw = (get_setting("smtp_pass", "") or "").replace(" ", "")   # Gmail App Password ใช้ค่า 16 ตัวไม่มีช่องว่าง
    sender = get_setting("smtp_from", "") or user
    use_tls = (get_setting("smtp_tls", "1") == "1")
    subject, body = render_otp_template(code)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = email
    try:
        server = smtplib.SMTP(host, port, timeout=10)
        if use_tls:
            server.starttls()
        if user:
            server.login(user, pw)
        server.sendmail(sender, [email], msg.as_string())
        server.quit()
        return True, "ส่งรหัสไปที่อีเมลแล้ว"
    except Exception as e:
        return False, "ส่งอีเมลไม่สำเร็จ: %s" % e


def otp_delivery_result(email, code, sent, info):
    """แปลงผลการส่ง OTP -> (category, message) สำหรับ flash + บันทึก log เมื่อส่งไม่สำเร็จ
       - ส่งสำเร็จ            : แจ้งให้ไปดูอีเมล
       - ยังไม่ตั้ง SMTP       : โหมดทดสอบจริง ๆ -> โชว์รหัสบนจอ (ตั้งใจ)
       - ตั้ง SMTP แล้วส่งล้ม  : แจ้ง error จริง (+log ให้แอดมินเห็น) แต่ยังให้รหัสชั่วคราว กันล็อกเอาต์ตัวเอง
       เดิมทุกกรณีที่ส่งไม่ผ่านถูกเหมารวมว่า “โหมดทดสอบ” ทำให้เข้าใจผิดว่าตั้งค่าไม่ครบ ทั้งที่จริงส่งไม่สำเร็จ"""
    if sent:
        return "ok", "ส่งรหัส OTP ไปที่อีเมลของคุณแล้ว — กรุณาตรวจกล่องจดหมาย (รวมโฟลเดอร์สแปม)"
    if not smtp_configured():
        return "ok", "โหมดทดสอบ (ยังไม่ตั้งค่าอีเมล SMTP): รหัส OTP คือ %s" % code
    try:
        from core import eventlog
        eventlog.log("auth", "ส่งอีเมล OTP ไม่สำเร็จ (%s): %s" % (email, info), level="error")
    except Exception:
        pass
    return "error", ("ส่งอีเมล OTP ไม่สำเร็จ — โปรดให้ผู้ดูแลตรวจการตั้งค่าอีเมล (SMTP). "
                     "รหัสชั่วคราวสำหรับเข้าใช้งานตอนนี้: %s" % code)
