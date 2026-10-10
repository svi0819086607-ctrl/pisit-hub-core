# -*- coding: utf-8 -*-
"""
ศูนย์รวมโปรแกรมภายใน — บริษัทตัวอย่าง   (ระบบปลั๊กอิน)
รันแล้วเปิด:  http://<ไอพีเครื่องเซิร์ฟเวอร์>:8080

** เพิ่มโปรแกรมใหม่ = แค่โยนโฟลเดอร์ลงใน programs/  ระบบอ่านเอง ไม่ต้องแก้ไฟล์นี้ **
  - โปรแกรม HTML : โฟลเดอร์มีไฟล์ .html (เช่น index.html)  + program.json (ไม่บังคับ)
  - โปรแกรม Python: โฟลเดอร์มีไฟล์ tool.py ที่ประกาศ bp = Blueprint(..., url_prefix="/p/<ชื่อโฟลเดอร์>")

[ฉบับเผยแพร่ ต.ค. 2569] คัดจากระบบที่ใช้งานจริง แล้วตัดส่วนแชท คลังไฟล์ ข้อมูลกลาง (Data API) โลโก้
หน้าหลังบ้าน และส่วนงานรายแผนกออก · ชื่อองค์กรเป็นชื่อสมมุติ · จุดที่ตัดมีคำว่า "[ตัดออก]" กำกับ
ไฟล์นี้จึงรันตามลำพังไม่ได้ (ไม่มี templates/ static/ และโมดูลที่ตัดออก)
"""
import gzip
import uuid
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort, Response

import config
from core.db import init_db, get_setting, get_conn
from core import auth, access, users
from core import registry
from core import backup
from core import eventlog
# [ตัดออก] โมดูลที่ไม่อยู่ในฉบับเผยแพร่: admin (หน้าหลังบ้าน) · hub (แชท/คลังไฟล์/ข้อมูลกลาง) · tunnel · และส่วนงานรายแผนก


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
    # ความปลอดภัยคุกกี้เซสชัน (ใช้ได้ทั้ง http ในวง LAN/Tailscale)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=7)

    init_db()

    # ---- โหมดการเข้าถึง: 'lan' (HTTP ในวง) หรือ 'public' (HTTPS ผ่าน tunnel) ----
    # เปลี่ยนโหมดที่หน้า "ตั้งค่าระบบ" แล้วรีสตาร์ทเพื่อให้มีผล
    access_mode = get_setting("access_mode", "lan")
    app.config["ACCESS_MODE"] = access_mode
    if access_mode == "public":
        # อยู่หลัง reverse proxy / tunnel (Cloudflare, Tailscale Funnel): อ่านค่า X-Forwarded-* ที่เชื่อถือได้
        # -> remote_addr = IP จริงของผู้ใช้ (ทำให้ filter วง LAN ใช้ได้จริง) และ is_secure สะท้อน HTTPS
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        # คุกกี้ Secure "ตาม request จริง" (แทนค่าตายตัว True):
        #   - เข้าผ่าน HTTPS (tunnel ที่ส่ง X-Forwarded-Proto เช่น cloudflared) -> ติดธง Secure
        #   - เข้าผ่าน http://<ไอพีวง LAN>:8080 หรือ Tailscale Funnel (ไม่ส่ง X-Forwarded-*)
        #     -> ไม่ติดธง (คุกกี้ธรรมดาใช้ได้ทั้ง http และ https)
        #   เหตุผล: เดิม SESSION_COOKIE_SECURE=True ตายตัว ทำให้คนเข้าผ่าน http ในวง LAN
        #   ล็อกอินไม่เคยติด (เบราว์เซอร์ทิ้งคุกกี้ Secure บน http) — โหมด public เลยใช้ใน LAN ไม่ได้
        from flask.sessions import SecureCookieSessionInterface

        class _DynSecureSession(SecureCookieSessionInterface):
            def get_cookie_secure(self, app):
                try:
                    return bool(request.is_secure)
                except Exception:
                    return False
        app.session_interface = _DynSecureSession()
        app.config["PREFERRED_URL_SCHEME"] = "https"
        print("  โหมดการเข้าถึง: PUBLIC (HTTPS ผ่าน tunnel, proxy-aware, secure cookie ตาม request)")
    else:
        print("  โหมดการเข้าถึง: LAN (HTTP ในวง, เชื่อ IP ตรงจากเครื่องผู้ใช้)")

    # ผูกเฉพาะ localhost ตอนโหมด public (ให้เข้าผ่าน tunnel เท่านั้น กันคนในวง LAN ปลอม IP)
    _bind_local = (access_mode == "public" and get_setting("bind_localhost", "0") == "1")
    app.config["BIND_HOST"] = "127.0.0.1" if _bind_local else config.HOST
    if _bind_local:
        print("  ผูกเฉพาะ 127.0.0.1 — เข้าถึงได้ผ่าน tunnel เท่านั้น")

    print("=" * 56)
    print("  กำลังสแกนโปรแกรมในโฟลเดอร์ programs/ ...")
    PROGRAMS = registry.discover(app)
    app.register_blueprint(registry.build_html_blueprint())
    # [ตัดออก] การลงทะเบียน blueprint ของแชท/คลังไฟล์/ข้อมูลกลาง และส่วนงานรายแผนก
    print(f"  พบทั้งหมด {len(PROGRAMS)} โปรแกรม")
    print("=" * 56)

    SLUGS = {p["slug"] for p in PROGRAMS}
    NAMES = {p["slug"]: p["name"] for p in PROGRAMS}

    def add_program(meta):
        """เพิ่มโปรแกรมใหม่เข้าระบบสด ๆ (เรียกตอนแอดมินอัปโหลด HTML)"""
        PROGRAMS.append(meta)
        SLUGS.add(meta["slug"])
        NAMES[meta["slug"]] = meta["name"]

    @app.context_processor
    def inject_globals():
        return {"ORG_NAME": config.ORG_NAME, "ORG_SUBTITLE": config.ORG_SUBTITLE,
                "user": auth.current_user(),
                "ACCESS_MODE": get_setting("access_mode", "lan"),     # ใช้ค่าที่บันทึกจริง (ไม่ค้างตั้งแต่บูต)
                "ALLOW_REGISTER": get_setting("allow_register", "1") == "1"}

    _EXEMPT = {"/login", "/login-otp", "/resend-login-otp", "/logout", "/register",
               "/verify-otp", "/resend-otp"}

    # ---- ด่านตรวจ: อุปกรณ์ / หมดเวลา session / วง LAN / สิทธิ์โปรแกรม + บันทึกการใช้งาน ----
    @app.before_request
    def _gate():
        # device id (cookie ฝังถาวร)
        did = request.cookies.get("dev_id")
        if not did:
            did = uuid.uuid4().hex
            g.new_dev_id = did
        g.dev_id = did

        p = request.path
        if p.startswith("/static") or p in _EXEMPT:
            return
        u = auth.current_user()
        if not u:
            return                      # login_required จะ redirect เอง

        # 1) หมดเวลา session -> เตะออก
        exp = session.get("expires_at")
        if exp:
            try:
                if datetime.datetime.now().timestamp() > float(exp):
                    eventlog.log("denied", "หมดเวลา session — เตะออกจากระบบ", level="warn")
                    session.clear()
                    flash("หมดเวลาใช้งานแล้ว กรุณาเข้าสู่ระบบอีกครั้ง", "error")
                    return redirect(url_for("login", next=p))
            except Exception:
                pass

        is_admin = (u.get("role") == "admin")

        # 0) แอดมินเริ่มต้นยังใช้รหัสเริ่มต้น -> บังคับไปหน้าเปลี่ยนรหัสก่อนทำอย่างอื่น
        if (is_admin and u.get("username") == config.DEFAULT_ADMIN_USER
                and get_setting("admin_default_pw", "0") == "1"):
            if p not in ("/account", "/logout") and not p.startswith("/avatar"):
                return redirect(url_for("account"))

        # 2) วง LAN ที่อนุญาต (ยกเว้นแอดมิน กันล็อกตัวเอง)
        if not is_admin and not access.ip_allowed(request.remote_addr):
            eventlog.log("denied", "บล็อก IP นอกวงที่อนุญาต: %s" % (request.remote_addr or "?"), level="warn")
            session.clear()
            flash("เครื่องนี้ไม่ได้อยู่ในเครือข่าย (วง LAN) ที่อนุญาตให้ใช้งาน", "error")
            return redirect(url_for("login"))

        # 3) บันทึกอุปกรณ์ + บล็อกอุปกรณ์ที่ถูกระงับ
        try:
            dstatus = access.touch_device(g.dev_id, request.remote_addr, u.get("username"),
                                          request.headers.get("User-Agent", ""))
        except Exception:
            dstatus = "allowed"
        if not is_admin and dstatus == "blocked":
            eventlog.log("denied", "บล็อกอุปกรณ์ที่ถูกระงับ (device %s)" % (g.dev_id[:8] if getattr(g, "dev_id", "") else "?"), level="warn")
            session.clear()
            flash("อุปกรณ์นี้ถูกระงับการใช้งานโดยผู้ดูแลระบบ", "error")
            return redirect(url_for("login"))

        # 4) สิทธิ์มองเห็นโปรแกรม + บันทึกการเปิดโปรแกรม
        if p.startswith("/p/"):
            parts = p.split("/")
            slug = parts[2] if len(parts) > 2 else ""
            if slug not in SLUGS:
                return
            if not is_admin and slug not in users.visible_slugs(u.get("username"), u.get("role"), SLUGS):
                abort(403)
            is_page = (p.rstrip("/") == f"/p/{slug}")
            try:
                access.log_access(request.remote_addr, u.get("username"), slug, p, is_page, device_id=g.dev_id)
            except Exception:
                pass

    # ---- บีบอัด response (gzip) ----
    # เหตุผล: ไฟล์โปรแกรม HTML ตัวละ 1-2.5 MB และข้อมูล JSON ก้อนใหญ่ ถูกส่งดิบ ๆ ทุกครั้งที่เปิดหน้า
    # -> วัดจริงในระบบที่ใช้งาน: JSON 30.9 MB บีบเหลือ 0.35 MB (88 เท่า, 84 ms)
    _GZ_TYPES = {"application/json", "application/javascript", "application/xml",
                 "text/html", "text/css", "text/plain", "text/javascript", "text/xml",
                 "image/svg+xml"}
    _GZ_MIN = 1024                      # เล็กกว่านี้ไม่คุ้ม (หัว gzip ~20 ไบต์ + ค่า CPU)
    _GZ_MAX = 96 * 1024 * 1024          # กันเคสหลุดมาผิดประเภท ไม่ให้ดูดของยักษ์เข้าแรม
    _GZ_LEVEL = 5                       # วัดแล้วได้อัตราส่วนเกือบเท่า 6 แต่คุมเวลาได้ดีกว่า

    # ลงทะเบียน "ก่อน" _set_dev_cookie/_audit เพราะ Flask รัน after_request แบบย้อนลำดับ
    # -> ตัวนี้จึงทำงาน "หลังสุด" คือหลังทุกคนเซ็ตหัวข้อมูลเสร็จแล้ว ค่อยบีบเนื้อทีเดียว
    @app.after_request
    def _gzip(resp):
        try:
            # ไฟล์ที่ส่งแบบสตรีม (send_file/send_from_directory) ห้ามแตะ — get_data() จะดูดทั้งไฟล์เข้าแรม
            # (ไฟล์สำรอง .zip ขนาดหลายร้อย MB วิ่งทางนี้)
            if resp.direct_passthrough or resp.headers.get("Content-Encoding"):
                return resp
            if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
                return resp
            if not (200 <= resp.status_code < 300):
                return resp
            if (resp.content_type or "").split(";")[0].strip().lower() not in _GZ_TYPES:
                return resp
            raw = resp.get_data()
            if not (_GZ_MIN <= len(raw) <= _GZ_MAX):
                return resp
            packed = gzip.compress(raw, _GZ_LEVEL)
            if len(packed) >= len(raw):          # บีบแล้วไม่เล็กลง (ของที่บีบมาแล้ว) -> ส่งของเดิม
                return resp
            resp.set_data(packed)
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"] = str(len(packed))
            # Vary: บอกแคช/พร็อกซีว่าคำตอบต่างกันตาม Accept-Encoding (ต่อท้ายของเดิม ไม่ทับ)
            vary = resp.headers.get("Vary", "")
            if "accept-encoding" not in vary.lower():
                resp.headers["Vary"] = (vary + ", " if vary else "") + "Accept-Encoding"
        except Exception:
            pass          # บีบไม่ได้ = ส่งของเดิม ห้ามทำให้ request พัง
        return resp

    @app.after_request
    def _set_dev_cookie(resp):
        public = (app.config.get("ACCESS_MODE") == "public")
        did = getattr(g, "new_dev_id", None)
        if did:
            resp.set_cookie("dev_id", did, max_age=60 * 60 * 24 * 365 * 3,
                            samesite="Lax", secure=(public and request.is_secure))
        # security headers (ใส่ทุกโหมด) + HSTS เฉพาะโหมด public (HTTPS)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if public:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    _AUDIT_SKIP_EXACT = {"/favicon.ico", "/admin/logs/data"}
    _AUDIT_SKIP_PREFIX = ("/static", "/avatar")
    _AUDIT_ASSET_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
                        ".css", ".js", ".map", ".woff", ".woff2", ".ttf")

    @app.after_request
    def _audit(resp):
        """บันทึก request ที่มีความหมายลง event log (ตัด noise: asset / แคช 304 / โพล background)"""
        try:
            p, m, sc = request.path, request.method, resp.status_code
            if p in _AUDIT_SKIP_EXACT or p.startswith(_AUDIT_SKIP_PREFIX):
                return resp
            if sc == 304:
                return resp                                   # แคช (ไม่เปลี่ยน) — ไม่บันทึก
            if m == "GET" and sc < 400:
                if p.lower().endswith(_AUDIT_ASSET_EXT):      # ไฟล์ static เช่นไอคอนโปรแกรม
                    return resp
            # โหมด "เฉพาะสำคัญ": เก็บเฉพาะ error/denial ของ request (เหตุการณ์สำคัญอื่น log แยกอยู่แล้ว)
            if sc < 400 and get_setting("log_mode", "all") == "important":
                return resp
            lvl = "error" if sc >= 500 else ("warn" if sc >= 400 else "info")
            eventlog.log("request", "%s %s" % (m, p), level=lvl, status=sc)
        except Exception:
            pass
        return resp

    @app.errorhandler(403)
    def _forbidden(e):
        eventlog.log("denied", "ปฏิเสธการเข้าถึง (403): %s" % request.path, level="warn", status=403)
        return render_template("forbidden.html"), 403

    @app.route("/")
    @auth.login_required
    def portal():
        u = auth.current_user()
        vis = users.visible_slugs(u["username"], u.get("role"), SLUGS)
        tools = [p for p in PROGRAMS if p["slug"] in vis]
        conn = get_conn()
        announces = [dict(r) for r in conn.execute(
            "SELECT id,date,body,created_by FROM announcements ORDER BY date DESC, id DESC LIMIT 50").fetchall()]
        conn.close()
        return render_template("portal.html", tools=tools, announces=announces,
                               today=datetime.date.today().isoformat())

    def _complete_login(u, nxt=None):
        users.throttle_clear(request.remote_addr or "?")     # สำเร็จ -> ล้างตัวนับกันเดารหัส
        session.permanent = True
        session["user"] = u
        eventlog.log("auth", "เข้าสู่ระบบสำเร็จ: %s" % u.get("username"), level="info")
        if (u.get("username") == config.DEFAULT_ADMIN_USER
                and get_setting("admin_default_pw", "0") == "1"):
            flash("⚠️ กรุณาเปลี่ยนรหัสผ่านผู้ดูแลระบบเริ่มต้นก่อนเริ่มใช้งาน", "error")
        mins = users.effective_timeout_min(u["username"])
        if mins is None:
            session.pop("expires_at", None)        # ไม่จำกัด
        else:
            session["expires_at"] = str(
                (datetime.datetime.now() + datetime.timedelta(minutes=mins)).timestamp())
        return redirect(nxt or url_for("portal"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if auth.current_user():
            return redirect(url_for("portal"))
        nxt = request.args.get("next")
        if request.method == "POST":
            tkey = request.remote_addr or "?"
            throttle_on = (get_setting("login_throttle", "1") == "1")
            if throttle_on:
                locked, secs = users.throttle_status(tkey)
                if locked:
                    eventlog.log("denied", "ปฏิเสธล็อกอิน — ถูกล็อกชั่วคราว (IP %s)" % tkey, level="warn")
                    flash("พยายามเข้าสู่ระบบบ่อยเกินไป — ลองใหม่ในอีกประมาณ %d นาที" % (secs // 60 + 1), "error")
                    return render_template("login.html")

            def _fail():
                if throttle_on:
                    users.throttle_fail(tkey,
                                        int(get_setting("login_max_fails", "10") or 10),
                                        int(get_setting("login_lock_min", "15") or 15))

            email = request.form.get("email", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            u = auth.try_login(username, password)
            if not u:
                _fail()
                eventlog.log("auth", "ล็อกอินล้มเหลว: %s (ชื่อผู้ใช้/รหัสผ่านไม่ถูกต้อง)" % (username or "?"), level="warn")
                flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "error")
                return render_template("login.html")
            # อีเมลต้องตรงกับบัญชีนี้
            if (u.get("email") or "").lower() != email.lower():
                _fail()
                eventlog.log("auth", "ล็อกอินล้มเหลว: %s (อีเมลไม่ตรงกับบัญชี)" % (username or "?"), level="warn")
                flash("อีเมลกับชื่อผู้ใช้ไม่ตรงกัน", "error")
                return render_template("login.html")
            st = u.get("status", "active")
            if st == "pending":
                flash("บัญชีนี้กำลังรอผู้ดูแลระบบอนุมัติ", "error")
                return render_template("login.html")
            if st == "disabled":
                flash("บัญชีนี้ถูกระงับการใช้งาน — ติดต่อผู้ดูแลระบบ", "error")
                return render_template("login.html")
            # ต้องทำ OTP ตอน login ไหม
            need_otp = (get_setting("login_otp", "1") == "1") and bool(u.get("email"))
            if need_otp and users.device_trusted(u["username"], g.dev_id):
                need_otp = False           # อุปกรณ์นี้ผ่าน OTP แล้ว ไม่ต้องถามซ้ำ
            if not need_otp:
                return _complete_login(u, nxt)
            # ส่ง OTP เพื่อยืนยันตัวตน (2FA)
            session["pending_login"] = {"u": u, "next": nxt}
            # กัน submit ซ้ำสองรอบติดกัน (กดปุ่มรัว/เน็ตช้า) -> ถ้าเพิ่งส่ง OTP ไปไม่กี่วินาที ใช้รหัสเดิม ไม่ส่งใหม่
            if users.recent_otp_exists(u["email"], "login", 25):
                flash("ส่งรหัส OTP ไปที่อีเมลของคุณแล้ว — กรุณาตรวจกล่องจดหมาย (รวมโฟลเดอร์สแปม)", "ok")
                return redirect(url_for("login_otp"))
            code = users.gen_otp(u["email"], "login")
            sent, info = users.send_otp_email(u["email"], code)
            cat, msg = users.otp_delivery_result(u["email"], code, sent, info)
            flash(msg, cat)
            return redirect(url_for("login_otp"))
        return render_template("login.html")

    @app.route("/login-otp", methods=["GET", "POST"])
    def login_otp():
        pend = session.get("pending_login")
        if not pend:
            return redirect(url_for("login"))
        u = pend["u"]
        if request.method == "POST":
            code = request.form.get("code", "").strip()
            if users.verify_otp(u["email"], code, "login"):
                users.trust_device(u["username"], g.dev_id)
                session.pop("pending_login", None)
                return _complete_login(u, pend.get("next"))
            if get_setting("login_throttle", "1") == "1":
                users.throttle_fail(request.remote_addr or "?",
                                    int(get_setting("login_max_fails", "10") or 10),
                                    int(get_setting("login_lock_min", "15") or 15))
            eventlog.log("auth", "OTP ไม่ถูกต้อง/หมดอายุ: %s" % u.get("username"), level="warn")
            flash("รหัส OTP ไม่ถูกต้องหรือหมดอายุ", "error")
        return render_template("login_otp.html", email=u.get("email", ""))

    @app.route("/resend-login-otp", methods=["POST"])
    def resend_login_otp():
        pend = session.get("pending_login")
        if pend:
            u = pend["u"]
            code = users.gen_otp(u["email"], "login")
            sent, info = users.send_otp_email(u["email"], code)
            cat, msg = users.otp_delivery_result(u["email"], code, sent, info)
            flash(msg, cat)
        return redirect(url_for("login_otp"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # [ตัดออก] เส้นทาง /logo และ /favicon.ico (โลโก้องค์กร)

    @app.route("/avatar/<username>")
    @auth.login_required
    def avatar(username):
        av = users.get_avatar(username)
        if not av or not av.get("data"):
            abort(404)
        resp = Response(bytes(av["data"]), mimetype=av.get("mime") or "image/png")
        resp.headers["Cache-Control"] = "max-age=60"     # แคชสั้น ๆ พอให้รูปอัปเดตเร็ว แต่ไม่ยิงซ้ำถี่
        return resp

    @app.route("/account", methods=["GET", "POST"])
    @auth.login_required
    def account():
        me = auth.current_user()["username"]
        if request.method == "POST":
            action = request.form.get("action", "password")
            if action == "profile":
                # 1) ลบรูป (ถ้าเลือก)
                if request.form.get("remove_avatar"):
                    users.delete_avatar(me)
                    flash("ลบรูปโปรไฟล์แล้ว", "ok")
                else:
                    # 2) อัปโหลดรูปใหม่ (ถ้ามี)
                    f = request.files.get("avatar")
                    if f and f.filename:
                        data = f.read()
                        if not (f.mimetype or "").startswith("image/"):
                            flash("ไฟล์รูปต้องเป็นรูปภาพ (jpg / png / webp …)", "error")
                        elif len(data) > 3 * 1024 * 1024:
                            flash("รูปต้องมีขนาดไม่เกิน 3 MB", "error")
                        else:
                            users.set_avatar(me, f.mimetype, data)
                            flash("อัปเดตรูปโปรไฟล์แล้ว", "ok")
                # 3) แก้ชื่อที่แสดง
                new_name = request.form.get("display_name", "").strip()
                if new_name and new_name != (auth.current_user().get("display_name") or ""):
                    if len(new_name) > 40:
                        flash("ชื่อที่แสดงยาวเกินไป (ไม่เกิน 40 ตัว)", "error")
                    else:
                        users.set_display_name(me, new_name)
                        session["user"]["display_name"] = new_name
                        session.modified = True
                        flash("อัปเดตชื่อที่แสดงแล้ว", "ok")
                return redirect(url_for("account"))
            # ---- เปลี่ยนรหัสผ่าน (ค่าเริ่มต้น) ----
            old = request.form.get("old", "")
            p1, p2 = request.form.get("p1", ""), request.form.get("p2", "")
            if not users.verify_password(me, old):
                flash("รหัสผ่านเดิมไม่ถูกต้อง", "error")
            elif len(p1) < 4:
                flash("รหัสผ่านใหม่ต้องยาวอย่างน้อย 4 ตัว", "error")
            elif p1 != p2:
                flash("รหัสผ่านใหม่สองช่องไม่ตรงกัน", "error")
            elif p1 == old:
                flash("รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสเดิม", "error")
            else:
                auth.change_password(me, p1)
                flash("เปลี่ยนรหัสผ่านเรียบร้อย", "ok")
        full = users.get_user(me) or {}
        av = users.get_avatar(me)
        my_devices = access.devices_for_user(me)
        cur_dev = getattr(g, "dev_id", None)
        return render_template("account.html", full=full, my_devices=my_devices, cur_dev=cur_dev,
                               has_avatar=bool(av), avatar_ver=(av["updated_at"] if av else ""))

    @app.route("/admin/usage")
    @auth.admin_required
    def usage():
        return render_template("usage.html",
                               active=access.active_sessions(NAMES),
                               log=access.recent_log(NAMES),
                               window=config.ACTIVE_WINDOW_MIN,
                               programs=PROGRAMS)

    # [ตัดออก] หน้าหลังบ้าน (core/admin.py): สมัคร/ยืนยัน OTP ตอนสมัคร + จัดการผู้ใช้/อุปกรณ์/ตั้งค่า/อัปโหลดโปรแกรม
    #          ในระบบจริงเรียก admin.init_app(app, PROGRAMS, SLUGS, NAMES, add_program) ตรงนี้

    # ---- ตัวจับเวลาสำรองข้อมูลอัตโนมัติ (รันเบื้องหลัง) ----
    backup.start_scheduler()

    # [ตัดออก] เปิด Cloudflare Tunnel อัตโนมัติ (core/tunnel.py)

    return app


app = create_app()

if __name__ == "__main__":
    # โหมดใช้งานจริง: รันผ่าน  start_server.bat  หรือ  py run_server.py  (ใช้ waitress, ไม่มี debug)
    # การรัน app.py ตรง ๆ จะปิด debug ไว้เพื่อความปลอดภัย — ถ้าต้องการ debug ตอนพัฒนา ตั้ง env HUB_DEBUG=1
    import os
    _dbg = os.environ.get("HUB_DEBUG") == "1"
    if _dbg:
        app.run(host=config.HOST, port=config.PORT, debug=True)
    else:
        try:
            from waitress import serve
            print("เปิดเซิร์ฟเวอร์ (production/waitress) ที่พอร์ต %s — Ctrl+C เพื่อหยุด" % config.PORT)
            serve(app, host=app.config.get("BIND_HOST", config.HOST), port=config.PORT, threads=8)
        except ImportError:
            app.run(host=app.config.get("BIND_HOST", config.HOST), port=config.PORT, debug=False)
