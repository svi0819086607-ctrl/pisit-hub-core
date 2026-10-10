# -*- coding: utf-8 -*-
"""
ตัวอ่านโปรแกรมอัตโนมัติ (plugin registry)
- สแกนโฟลเดอร์ programs/  ทุกโฟลเดอร์ = หนึ่งโปรแกรม  ไม่ต้องลงทะเบียนในโค้ด
- รองรับ 2 แบบ:
    * Python : โฟลเดอร์มีไฟล์ tool.py  ที่ประกาศ  bp = Blueprint(...)  (url_prefix = /p/<ชื่อโฟลเดอร์>)
    * HTML   : โฟลเดอร์มีไฟล์ .html (เช่น index.html)  -> ระบบเสิร์ฟให้เอง ไม่ต้องเขียน Python
- ใส่ไฟล์ program.json (ไม่บังคับ) เพื่อกำหนดชื่อ/ไอคอน/สี/คำอธิบาย:
    { "name":"...", "desc":"...", "icon":"📊", "color":"#0d6e63", "entry":"index.html" }
"""
import os
import json
import importlib
import glob

import re as _re
from flask import Blueprint, Response, send_from_directory, url_for, abort
from core.auth import login_required, current_user
import config

# slug -> {"folder":..., "entry":...}  สำหรับโปรแกรมแบบ HTML
HTML_PROGRAMS = {}

_BACK_BAR = """<div style="position:sticky;top:0;z-index:60;background:#11211f;color:#fff;
 padding:8px 16px;font-family:'IBM Plex Sans Thai',sans-serif;font-size:14px;display:flex;gap:14px;
 align-items:center;border-bottom:3px solid #0d6e63">
 <a href="/" style="color:#fff;text-decoration:none;font-weight:600">&larr; หน้ารวมโปรแกรม</a>
 <span style="color:#9fb3b0">{name}</span></div>"""


def _read_manifest(folder):
    p = os.path.join(folder, "program.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def discover(app):
    """สแกน + ลงทะเบียนทุกโปรแกรม คืน list ของ meta สำหรับหน้ารวม"""
    metas = []
    base = config.PROGRAMS_DIR
    if not os.path.isdir(base):
        return metas

    for slug in sorted(os.listdir(base)):
        folder = os.path.join(base, slug)
        if not os.path.isdir(folder) or slug.startswith(("_", ".")):
            continue

        manifest = _read_manifest(folder)
        has_py = os.path.exists(os.path.join(folder, "tool.py"))
        html_files = [os.path.basename(f) for f in glob.glob(os.path.join(folder, "*.html"))]

        meta = {"slug": slug, "url": f"/p/{slug}/",
                "name": manifest.get("name", slug),
                "desc": manifest.get("desc", ""),
                "icon": manifest.get("icon", "🧩"),
                "icon_img": manifest.get("icon_img"),
                "kind": manifest.get("kind", ""),
                "color": manifest.get("color", "#0d6e63"),
                "export_ask": bool(manifest.get("export_ask", False)),
                "type": None}

        if has_py:
            # ทำให้เป็นแพ็กเกจถ้ายังไม่ใช่ (เผื่อโยนโฟลเดอร์มาดิบ ๆ)
            initf = os.path.join(folder, "__init__.py")
            if not os.path.exists(initf):
                open(initf, "w").close()
            try:
                mod = importlib.import_module(f"programs.{slug}.tool")
                app.register_blueprint(mod.bp)
                # ใช้ URL จริงจาก url_prefix ของ Blueprint (อาจไม่ตรงกับชื่อโฟลเดอร์)
                if mod.bp.url_prefix:
                    meta["url"] = mod.bp.url_prefix.rstrip("/") + "/"
                tmeta = getattr(mod, "TOOL_META", {})
                for k in ("name", "desc", "icon", "color"):
                    if tmeta.get(k):
                        meta[k] = tmeta[k]
                meta["type"] = "python"
                metas.append(meta)
                print(f"  [โหลด] โปรแกรม Python: {slug}")
                continue
            except Exception as e:
                print(f"  [ข้าม] โหลด {slug} ไม่ได้: {e}")
                continue

        if html_files:
            entry = manifest.get("entry") or ("index.html" if "index.html" in html_files
                                              else html_files[0])
            HTML_PROGRAMS[slug] = {"folder": folder, "entry": entry, "manifest": manifest}
            if meta["name"] == slug:
                meta["name"] = entry.rsplit(".", 1)[0]
            meta["type"] = "html"
            meta["icon"] = manifest.get("icon", "🌐")
            metas.append(meta)
            print(f"  [โหลด] โปรแกรม HTML: {slug}  ({entry})")

    return metas


def register_html(slug, folder, entry, manifest):
    """ลงทะเบียนโปรแกรม HTML แบบสด ๆ (ไม่ต้องรีสตาร์ท) — คืน meta สำหรับหน้ารวม"""
    HTML_PROGRAMS[slug] = {"folder": folder, "entry": entry, "manifest": manifest}
    return {"slug": slug, "url": f"/p/{slug}/",
            "name": manifest.get("name", slug), "desc": manifest.get("desc", ""),
            "icon": manifest.get("icon", "🌐"), "icon_img": manifest.get("icon_img"),
            "kind": manifest.get("kind", ""),
            "export_ask": bool(manifest.get("export_ask", False)),
            "color": manifest.get("color", "#0d6e63"), "type": "html"}


_IDENT_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _static_ver(src):
    """เติม ?v=<mtime> ให้สคริปต์ /static/* เพื่อบังคับเบราว์เซอร์โหลดใหม่เมื่อไฟล์เปลี่ยน (กัน cache ค้าง)"""
    if not isinstance(src, str) or not src.startswith("/static/"):
        return src
    rel = src[len("/static/"):].split("?", 1)[0]
    fp = os.path.join(config.BASE_DIR, "static", rel)
    try:
        return "%s?v=%d" % (src, int(os.path.getmtime(fp)))
    except OSError:
        return src


def _build_injection(slug, info):
    """สร้างบล็อกสคริปต์ที่ inject ก่อน </body> ตัวสุดท้าย:
       - แถบ 'กลับหน้ารวม' (ใส่ผ่าน JS ที่ต้น body — กันชนกับสตริง <body> ที่ฝังในไลบรารี)
       - ตัวแปร window.* + สคริปต์เสริม ตาม program.json -> inject
       - icons.js (ต้องมาก่อน hub.js) + สคริปต์เสริม + hub.js
    """
    manifest = (info or {}).get("manifest", {}) or {}
    inj = manifest.get("inject", {}) or {}
    bar = _BACK_BAR.format(name=slug)

    head = ['<script>']
    head.append('try{document.body.insertAdjacentHTML("afterbegin",%s);}catch(e){}' % json.dumps(bar))
    if inj.get("user"):
        u = current_user() or {}
        head.append('window.PISIT_USER=%s;' % json.dumps(u.get("username", "")))
    for k, v in (inj.get("vars") or {}).items():
        if _IDENT_RE.match(str(k)):
            head.append('window.%s=%s;' % (k, json.dumps(v)))
    head.append('</script>')

    scripts = ['<script src="%s"></script>' % _static_ver("/static/icons.js")]
    for src in (inj.get("scripts") or []):
        if isinstance(src, str) and src.startswith("/static/"):   # อนุญาตเฉพาะสคริปต์ในระบบ
            scripts.append('<script src="%s"></script>' % _static_ver(src))
    scripts.append('<script src="%s"></script>' % _static_ver("/static/hub.js"))
    # จองพื้นที่ scrollbar ถาวร -> สลับแท็บ (เนื้อหาเตี้ย/สูง) หัวเรื่องไม่ขยับซ้าย-ขวา
    style = "<style>html{scrollbar-gutter:stable}</style>"
    return style + "".join(head) + "".join(scripts)


def build_html_blueprint():
    """บลูพรินต์เดียวที่เสิร์ฟโปรแกรมแบบ HTML ทุกตัว (/p/<slug>/...)"""
    bp = Blueprint("htmlprog", __name__)

    @bp.route("/p/<slug>/")
    @login_required
    def index(slug):
        info = HTML_PROGRAMS.get(slug)
        if not info:
            abort(404)
        with open(os.path.join(info["folder"], info["entry"]), encoding="utf-8") as f:
            html = f.read()
        inj = _build_injection(slug, info)
        # inject ก่อน </body> ตัวสุดท้าย (ตัวจริงของเอกสาร) — กันไฟล์ที่ฝัง lib มีสตริง </body> ปน
        if "</body>" in html:
            head, sep, tail = html.rpartition("</body>")
            html = head + inj + sep + tail
        else:
            html = html + inj
        return Response(html, mimetype="text/html")

    @bp.route("/p/<slug>/<path:fname>")
    @login_required
    def asset(slug, fname):
        info = HTML_PROGRAMS.get(slug)
        if not info:
            abort(404)
        return send_from_directory(info["folder"], fname)

    return bp
