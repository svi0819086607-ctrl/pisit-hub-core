# -*- coding: utf-8 -*-
"""
ระบบสำรองข้อมูล (Backup) — รวมทุกอย่างที่จำเป็นไว้ในไฟล์ ZIP เดียว:
  - ฐานข้อมูล  data/hub.db        (ผู้ใช้ / โปรแกรม / ข้อมูลกลาง / แชท / รายการไฟล์)
  - ไฟล์อัปโหลด data/hub_files/    (ไฟล์จริงทั้งหมดบนเซิร์ฟเวอร์)
  - คีย์ลับ     data/secret.key     (ต้องมีเพื่อให้เซสชันเดิมใช้ได้หลังกู้)
  - โปรแกรม    programs/           (โปรแกรม HTML ที่อัปโหลดไว้)
  - เมล็ดข้อมูล data/seed/

ตั้งเวลาสำรองอัตโนมัติได้ (รายวัน / รายสัปดาห์ / รายเดือน) + เก็บย้อนหลังตามจำนวนที่ตั้ง
ค่าตั้งทั้งหมดเก็บในตาราง settings ผ่าน get_setting / set_setting
"""
import os
import json
import time
import shutil
import zipfile
import threading
import datetime

import config
from core.db import get_setting, set_setting, get_conn

BACKUP_DIR = os.path.join(config.DATA_DIR, "backups")


def _now():
    return datetime.datetime.now()


def ensure_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _add_tree(zf, src, arc_base):
    """ใส่ทั้งโฟลเดอร์ลง zip (ข้าม __pycache__/.pyc)"""
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, src).replace("\\", "/")
            zf.write(full, arc_base + "/" + rel)


def _copy_over(src, dst):
    """คัดลอกไฟล์จาก src ทับลง dst ทีละไฟล์ — *ไม่ลบของเดิมทิ้งก่อน*
       (ปลอดภัยบน Windows/OneDrive: ถ้าไฟล์ไหนถูกล็อก/ก๊อปไม่ได้ ก็ข้ามเฉพาะไฟล์นั้น
        โดยไม่ทำให้ข้อมูลที่เหลือหาย) คืนจำนวน (ก๊อปสำเร็จ, พลาด)"""
    ok = err = 0
    if not os.path.isdir(src):
        return ok, err
    os.makedirs(dst, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for fn in files:
            s = os.path.join(root, fn)
            d = os.path.join(target, fn)
            try:
                shutil.copy2(s, d)
                ok += 1
            except Exception:
                try:
                    shutil.copyfile(s, d)
                    ok += 1
                except Exception:
                    err += 1
    return ok, err


def _cleanup_tmp(tmp):
    try:
        shutil.rmtree(tmp)
    except OSError:
        pass


# ============================================================
#  สร้าง / จัดการไฟล์สำรอง
# ============================================================
def create_backup(reason="manual"):
    """สร้างไฟล์สำรอง .zip — คืน (True, ชื่อไฟล์) หรือ (False, ข้อความ error)"""
    ensure_dir()
    ts = _now().strftime("%Y%m%d_%H%M%S")
    name = "backup_%s_%s.zip" % (ts, reason)
    path = os.path.join(BACKUP_DIR, name)
    tmp = path + ".tmp"
    try:
        # รวม WAL กลับเข้า hub.db ก่อน เพื่อให้ไฟล์สำรองมีข้อมูลล่าสุดครบ
        try:
            conn = get_conn()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()
        except Exception:
            pass
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(config.DB_PATH):
                zf.write(config.DB_PATH, "data/hub.db")
            sk = os.path.join(config.DATA_DIR, "secret.key")
            if os.path.exists(sk):
                zf.write(sk, "data/secret.key")
            if os.path.isdir(config.HUB_FILES_DIR):
                _add_tree(zf, config.HUB_FILES_DIR, "data/hub_files")
            if os.path.isdir(config.SEED_DIR):
                _add_tree(zf, config.SEED_DIR, "data/seed")
            if os.path.isdir(config.PROGRAMS_DIR):
                _add_tree(zf, config.PROGRAMS_DIR, "programs")
            man = {"created": _now().isoformat(timespec="seconds"),
                   "reason": reason, "app": "phisit-hub", "version": 1,
                   "entries": len(zf.namelist())}
            zf.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=2))
        os.replace(tmp, path)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, str(e)
    prune()
    return True, name


def list_backups():
    """รายการไฟล์สำรองทั้งหมด (ใหม่สุดก่อน)"""
    ensure_dir()
    out = []
    for fn in os.listdir(BACKUP_DIR):
        if not fn.endswith(".zip"):
            continue
        full = os.path.join(BACKUP_DIR, fn)
        try:
            stt = os.stat(full)
        except OSError:
            continue
        reason = "manual"
        for r in ("auto", "manual", "upload", "prerestore"):
            if fn.endswith("_%s.zip" % r):
                reason = r
                break
        out.append({"name": fn, "size": stt.st_size,
                    "ts": datetime.datetime.fromtimestamp(stt.st_mtime).isoformat(timespec="seconds"),
                    "reason": reason})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def total_size():
    return sum(b["size"] for b in list_backups())


def backup_path(name):
    """คืน path เต็มของไฟล์สำรอง (กัน path traversal) หรือ None"""
    name = os.path.basename(name or "")
    if not name.endswith(".zip"):
        return None
    p = os.path.join(BACKUP_DIR, name)
    return p if os.path.exists(p) else None


def delete_backup(name):
    p = backup_path(name)
    if not p:
        return False
    try:
        os.remove(p)
        return True
    except OSError:
        return False


def prune():
    """เก็บไฟล์สำรองย้อนหลังตามจำนวน backup_keep (0 = ไม่จำกัด ไม่ลบ)"""
    try:
        keep = int(get_setting("backup_keep", "14") or 14)
    except ValueError:
        keep = 14
    if keep <= 0:
        return
    items = list_backups()
    for it in items[keep:]:
        delete_backup(it["name"])


def save_uploaded(fileobj):
    """รับไฟล์ .zip ที่ผู้ใช้อัปโหลดเข้ามาเก็บเป็นไฟล์สำรอง — คืน (ok, ชื่อ|error)"""
    ensure_dir()
    src_name = os.path.basename(fileobj.filename or "")
    if not src_name.lower().endswith(".zip"):
        return False, "ต้องเป็นไฟล์ .zip"
    ts = _now().strftime("%Y%m%d_%H%M%S")
    save_name = "backup_%s_upload.zip" % ts
    path = os.path.join(BACKUP_DIR, save_name)
    try:
        fileobj.save(path)
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        if "data/hub.db" not in names:
            os.remove(path)
            return False, "ไฟล์สำรองไม่ถูกต้อง (ไม่พบ data/hub.db ข้างใน)"
    except zipfile.BadZipFile:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, "ไฟล์ ZIP เสียหรืออ่านไม่ได้"
    except Exception as e:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, str(e)
    return True, save_name


def restore_backup(name):
    """กู้คืนข้อมูลจากไฟล์สำรอง — เขียนทับ data/ + programs/
       (สำรองสถานะปัจจุบันให้อัตโนมัติก่อนกู้ และควรรีสตาร์ทเซิร์ฟเวอร์หลังกู้)
       คืน (ok, 'ok'|error)"""
    p = backup_path(name)
    if not p:
        return False, "ไม่พบไฟล์สำรอง"
    # ตรวจไฟล์ก่อน
    try:
        with zipfile.ZipFile(p) as zf:
            if "data/hub.db" not in zf.namelist():
                return False, "ไฟล์สำรองไม่สมบูรณ์ (ไม่พบ data/hub.db)"
    except Exception:
        return False, "ไฟล์ ZIP เสียหรืออ่านไม่ได้"
    # สำรองสถานะปัจจุบันก่อนกู้ (กันพลาด)
    safe_ok, safe = create_backup("prerestore")
    tmp = os.path.join(config.DATA_DIR, "_restore_tmp")
    _cleanup_tmp(tmp)
    try:
        os.makedirs(tmp, exist_ok=True)
        with zipfile.ZipFile(p) as zf:
            zf.extractall(tmp)
    except Exception as e:
        _cleanup_tmp(tmp)
        return False, "แตกไฟล์สำรองไม่สำเร็จ: %s" % e
    warns = []
    # 1) ฐานข้อมูล hub.db (สำคัญสุด) — ลอง 3 ครั้งกันไฟล์ถูกล็อกชั่วคราว
    src_db = os.path.join(tmp, "data", "hub.db")
    if os.path.exists(src_db):
        for ext in ("-wal", "-shm"):
            try:
                os.remove(config.DB_PATH + ext)
            except OSError:
                pass
        db_ok = False
        for _i in range(3):
            try:
                shutil.copy2(src_db, config.DB_PATH)
                db_ok = True
                break
            except Exception:
                time.sleep(0.6)
        if not db_ok:
            _cleanup_tmp(tmp)
            return False, "เขียนฐานข้อมูลไม่สำเร็จ (ไฟล์อาจถูกใช้งานอยู่ ลองอีกครั้ง)%s" % (
                " — มีไฟล์สำรองก่อนกู้: %s" % safe if safe_ok else "")
    # 2) secret.key
    src_sk = os.path.join(tmp, "data", "secret.key")
    if os.path.exists(src_sk):
        try:
            shutil.copy2(src_sk, os.path.join(config.DATA_DIR, "secret.key"))
        except Exception as e:
            warns.append("secret.key: %s" % e)
    # 3) ไฟล์อัปโหลด hub_files — คัดลอกทับแบบปลอดภัย (ไม่ลบของเดิมก่อน)
    okc, errc = _copy_over(os.path.join(tmp, "data", "hub_files"), config.HUB_FILES_DIR)
    if errc:
        warns.append("ไฟล์อัปโหลด %d ไฟล์คัดลอกไม่สำเร็จ" % errc)
    # 4) โปรแกรม programs — คัดลอกทับแบบปลอดภัย
    okp, errp = _copy_over(os.path.join(tmp, "programs"), config.PROGRAMS_DIR)
    if errp:
        warns.append("ไฟล์โปรแกรม %d ไฟล์คัดลอกไม่สำเร็จ" % errp)
    _cleanup_tmp(tmp)
    if warns:
        return True, "กู้คืนแล้ว แต่มีบางส่วนพลาด: " + " · ".join(warns)
    return True, "ok"


# ============================================================
#  ตัวจับเวลาสำรองอัตโนมัติ (background thread)
# ============================================================
def schedule_summary():
    """ข้อความสรุปตารางเวลาแบบอ่านง่าย (ใช้โชว์บนหน้าเว็บ)"""
    if get_setting("backup_enabled", "0") != "1":
        return "ปิดอยู่ (ไม่สำรองอัตโนมัติ)"
    freq = get_setting("backup_freq", "daily")
    t = get_setting("backup_time", "02:00") or "02:00"
    if freq == "weekly":
        days = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
        try:
            d = days[int(get_setting("backup_dow", "0") or 0) % 7]
        except (ValueError, IndexError):
            d = "จันทร์"
        return "ทุกสัปดาห์ วัน%s เวลา %s น." % (d, t)
    if freq == "monthly":
        dom = get_setting("backup_dom", "1") or "1"
        return "ทุกเดือน วันที่ %s เวลา %s น." % (dom, t)
    return "ทุกวัน เวลา %s น." % t


def _due(now):
    if get_setting("backup_enabled", "0") != "1":
        return False
    t = get_setting("backup_time", "02:00") or "02:00"
    try:
        hh, mm = [int(x) for x in t.split(":")[:2]]
    except Exception:
        hh, mm = 2, 0
    if (now.hour, now.minute) < (hh, mm):
        return False                      # ยังไม่ถึงเวลาวันนี้
    freq = get_setting("backup_freq", "daily")
    if freq == "weekly":
        try:
            dow = int(get_setting("backup_dow", "0") or 0)
        except ValueError:
            dow = 0
        if now.weekday() != dow:
            return False
    elif freq == "monthly":
        try:
            dom = int(get_setting("backup_dom", "1") or 1)
        except ValueError:
            dom = 1
        if now.day != dom:
            return False
    # ทำไปแล้ววันนี้หรือยัง (กันสำรองซ้ำ)
    if get_setting("backup_last", "") == now.strftime("%Y-%m-%d"):
        return False
    return True


def _loop():
    # หน่วงเล็กน้อยตอนเริ่ม ให้ระบบพร้อมก่อน
    time.sleep(20)
    while True:
        try:
            now = _now()
            if _due(now):
                ok, info = create_backup("auto")
                if ok:
                    set_setting("backup_last", now.strftime("%Y-%m-%d"))
        except Exception:
            pass
        time.sleep(60)


_started = False


def start_scheduler():
    """เริ่มตัวจับเวลาสำรองอัตโนมัติ (เรียกครั้งเดียวตอนสตาร์ทแอป)"""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="backup-scheduler", daemon=True).start()
