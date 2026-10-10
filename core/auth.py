# -*- coding: utf-8 -*-
"""ระบบล็อกอินง่าย ๆ (ใช้เซสชันของ Flask) — เหมาะกับใช้ในวง LAN ออฟฟิศ"""
from functools import wraps
from flask import session, redirect, url_for, request, flash
from werkzeug.security import check_password_hash, generate_password_hash

import config
from core.db import get_conn


def current_user():
    return session.get("user")


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login", next=request.path))
        if u.get("role") != "admin":
            flash("เฉพาะผู้ดูแลระบบเท่านั้น", "error")
            return redirect(url_for("portal"))
        return view(*a, **kw)
    return wrapped


def try_login(username, password):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        keys = row.keys()
        return {"username": row["username"], "display_name": row["display_name"],
                "role": row["role"],
                "status": (row["status"] if "status" in keys else "active") or "active",
                "email": (row["email"] if "email" in keys else "") or "",
                "department": (row["department"] if "department" in keys else "") or ""}
    return None


def change_password(username, new_password):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                 (generate_password_hash(new_password), username))
    if username == config.DEFAULT_ADMIN_USER:
        conn.execute("INSERT INTO settings(key,value) VALUES ('admin_default_pw','0') "
                     "ON CONFLICT(key) DO UPDATE SET value='0'")
    conn.commit()
    conn.close()
