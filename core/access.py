# -*- coding: utf-8 -*-
"""บันทึก/อ่านการใช้งาน — ใครเครื่องไหน(IP) ใช้โปรแกรมตัวไหนอยู่ + คลังอุปกรณ์ + คุม IP/วง LAN"""
import json
import socket
import datetime
import ipaddress
from core.db import get_conn, get_setting
import config

_HOST_CACHE = {}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def log_access(ip, username, program, path, is_page, device_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO access_log(ts,ip,username,program,path,is_page,device_id) VALUES (?,?,?,?,?,?,?)",
        (_now(), ip or "", username or "", program or "", path or "",
         1 if is_page else 0, device_id or ""))
    conn.commit()
    conn.close()


# ============================================================
#  คุมการเข้าถึงด้วย IP / วง LAN ที่อนุญาต
# ============================================================
def network_enforced():
    return get_setting("enforce_network", "1") == "1"


def allowed_networks():
    try:
        return json.loads(get_setting("allowed_networks", "[]") or "[]")
    except Exception:
        return []


def ip_allowed(ip):
    """True ถ้า IP อยู่ในวง LAN ที่อนุญาต (หรือปิดการบังคับไว้)"""
    if not network_enforced():
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in allowed_networks():
        try:
            if addr in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


# ============================================================
#  คลังอุปกรณ์ (devices)
# ============================================================
def touch_device(device_id, ip, username, user_agent):
    """บันทึก/อัปเดตอุปกรณ์ที่เข้ามาใช้งาน คืนสถานะ ('allowed'|'blocked')"""
    if not device_id:
        return "allowed"
    conn = get_conn()
    row = conn.execute("SELECT device_id,status,first_seen FROM devices WHERE device_id=?",
                       (device_id,)).fetchone()
    host = hostname_for(ip)
    if row:
        conn.execute("UPDATE devices SET last_ip=?, last_host=?, last_user=?, user_agent=?, last_seen=? "
                     "WHERE device_id=?", (ip or "", host, username or "", user_agent or "", _now(), device_id))
        status = row["status"] or "allowed"
    else:
        conn.execute("INSERT INTO devices(device_id,label,device_type,status,last_ip,last_host,"
                     "last_user,user_agent,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (device_id, "", "", "allowed", ip or "", host, username or "",
                      user_agent or "", _now(), _now()))
        status = "allowed"
    conn.commit()
    conn.close()
    return status


def device_status(device_id):
    if not device_id:
        return None
    conn = get_conn()
    row = conn.execute("SELECT status FROM devices WHERE device_id=?", (device_id,)).fetchone()
    conn.close()
    return (row["status"] if row else None)


def list_devices(prog_names=None):
    prog_names = prog_names or {}
    conn = get_conn()
    devs = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    out = []
    for d in devs:
        users = conn.execute(
            "SELECT DISTINCT username FROM access_log WHERE device_id=? AND username<>'' ",
            (d["device_id"],)).fetchall()
        progs = conn.execute(
            "SELECT DISTINCT program FROM access_log WHERE device_id=? AND program<>'' ",
            (d["device_id"],)).fetchall()
        hits = conn.execute("SELECT COUNT(*) c FROM access_log WHERE device_id=?",
                            (d["device_id"],)).fetchone()["c"]
        out.append({**dict(d),
                    "users": [u["username"] for u in users],
                    "programs": [prog_names.get(p["program"], p["program"]) for p in progs],
                    "hits": hits})
    conn.close()
    return out


def get_device(device_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_device(device_id, label=None, device_type=None, status=None,
                  usage_scope=None, note=None):
    sets, vals = [], []
    if label is not None:        sets.append("label=?"); vals.append(label)
    if device_type is not None:  sets.append("device_type=?"); vals.append(device_type)
    if status is not None:       sets.append("status=?"); vals.append(status)
    if note is not None:         sets.append("note=?"); vals.append(note)
    if usage_scope is not None:  sets.append("usage_scope=?"); vals.append(usage_scope)
    if not sets:
        return
    vals.append(device_id)
    conn = get_conn()
    conn.execute(f"UPDATE devices SET {', '.join(sets)} WHERE device_id=?", vals)
    conn.commit()
    conn.close()


def add_device_manual(device_id, label, device_type, status="allowed", usage_scope="", note=""):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO devices(device_id,label,device_type,status,usage_scope,note,"
                 "first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?)",
                 (device_id, label, device_type, status, usage_scope, note, _now(), _now()))
    conn.commit()
    conn.close()


def devices_for_user(username):
    """อุปกรณ์ที่ผู้ใช้คนนี้เคยใช้ login (อ้างจาก access_log) — สำหรับหน้าบัญชีของฉัน"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.device_id, MIN(a.ts) first_ts, MAX(a.ts) last_ts, COUNT(*) hits, "
        "d.label, d.device_type, d.status, d.last_ip, d.last_host, d.user_agent "
        "FROM access_log a LEFT JOIN devices d ON d.device_id=a.device_id "
        "WHERE a.username=? AND a.device_id<>'' "
        "GROUP BY a.device_id ORDER BY last_ts DESC", (username,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_device(device_id):
    conn = get_conn()
    conn.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
    conn.commit()
    conn.close()


def hostname_for(ip):
    """หาชื่อเครื่องจาก IP (best-effort, แคชไว้ไม่ให้ช้า)"""
    if not ip:
        return ""
    if ip in _HOST_CACHE:
        return _HOST_CACHE[ip]
    name = ""
    try:
        socket.setdefaulttimeout(0.4)
        name = socket.gethostbyaddr(ip)[0]
    except Exception:
        name = ""
    finally:
        socket.setdefaulttimeout(None)
    _HOST_CACHE[ip] = name
    return name


def active_sessions(prog_names):
    """ใครกำลังใช้โปรแกรมไหนอยู่ (ภายในกี่นาทีล่าสุดตาม config)"""
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(minutes=config.ACTIVE_WINDOW_MIN)).isoformat(timespec="seconds")
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.ip, a.username, a.program, a.device_id, d.label dev_label, "
        "MAX(a.ts) last_ts, COUNT(*) hits "
        "FROM access_log a LEFT JOIN devices d ON d.device_id=a.device_id "
        "WHERE a.ts>=? GROUP BY a.ip, a.program, a.username, a.device_id "
        "ORDER BY last_ts DESC", (cutoff,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({"ip": r["ip"], "host": hostname_for(r["ip"]),
                    "dev_label": r["dev_label"] or "",
                    "username": r["username"], "program": r["program"],
                    "program_name": prog_names.get(r["program"], r["program"]),
                    "last_ts": r["last_ts"], "hits": r["hits"]})
    return out


def recent_log(prog_names, limit=120):
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.ts, a.ip, a.username, a.program, a.path, d.label dev_label "
        "FROM access_log a LEFT JOIN devices d ON d.device_id=a.device_id "
        "WHERE a.is_page=1 ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({"ts": r["ts"], "ip": r["ip"], "host": hostname_for(r["ip"]),
                    "dev_label": r["dev_label"] or "",
                    "username": r["username"], "program": r["program"],
                    "program_name": prog_names.get(r["program"], r["program"]),
                    "path": r["path"]})
    return out
