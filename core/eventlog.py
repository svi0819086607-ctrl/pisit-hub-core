# -*- coding: utf-8 -*-
"""
Event Log กลาง — บันทึกทุกเหตุการณ์ในระบบ (request / auth / denied / admin / backup / tunnel ...)
แล้วโชว์ในแท็บ Log แบบ terminal

ใช้:  from core import eventlog ; eventlog.log("auth", "เข้าสู่ระบบสำเร็จ: admin", level="info")
ดึงค่า user/ip/method/path จาก request context ให้อัตโนมัติ
"""
import datetime
from flask import request, has_request_context

from core.db import get_conn, get_setting

_KEEP = 8000          # ค่าเริ่มต้น เก็บล่าสุดกี่แถว (กันไฟล์โต) — ปรับได้ที่ setting log_keep
_n = 0                # ตัวนับ insert ภายใน ใช้ตัดสินใจ prune เป็นระยะ


def log(category, message, level="info", status=None):
    """บันทึก 1 เหตุการณ์ (ปลอดภัย: ไม่ทำให้ request พังถ้า log ล้มเหลว)"""
    global _n
    try:
        actor, ip, method, path = "-", "", "", ""
        if has_request_context():
            try:
                from core import auth
                u = auth.current_user()
                actor = (u.get("username") if u else "") or "-"
            except Exception:
                actor = "-"
            ip = request.remote_addr or ""
            method = request.method
            path = request.path
        conn = get_conn()
        conn.execute(
            "INSERT INTO event_log(ts,level,category,actor,ip,method,path,status,message) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.datetime.now().isoformat(timespec="seconds"),
             level, category, actor, ip, method, path, status, str(message)[:600]))
        conn.commit()
        conn.close()
        _n += 1
        if _n % 500 == 0:
            prune()
    except Exception:
        pass


def fetch(after_id=0, limit=600):
    """ดึงเหตุการณ์ — ถ้ามี after_id คืนเฉพาะที่ใหม่กว่า (สำหรับ live tail)"""
    conn = get_conn()
    if after_id:
        rows = conn.execute("SELECT * FROM event_log WHERE id>? ORDER BY id LIMIT 2000",
                            (after_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        rows = list(rows)[::-1]
    conn.close()
    return [dict(r) for r in rows]


def stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM event_log").fetchone()["c"]
    bylvl = {r["level"]: r["c"] for r in
             conn.execute("SELECT level, COUNT(*) c FROM event_log GROUP BY level").fetchall()}
    conn.close()
    return {"total": total, "by_level": bylvl}


def clear():
    conn = get_conn()
    conn.execute("DELETE FROM event_log")
    conn.commit()
    conn.close()


def prune(keep=None):
    try:
        if keep is None:
            try:
                keep = int(get_setting("log_keep", str(_KEEP)) or _KEEP)
            except (ValueError, TypeError):
                keep = _KEEP
        keep = max(200, keep)
        conn = get_conn()
        conn.execute("DELETE FROM event_log WHERE id <= "
                     "(SELECT MAX(id) FROM event_log) - ?", (keep,))
        conn.commit()
        conn.close()
    except Exception:
        pass
