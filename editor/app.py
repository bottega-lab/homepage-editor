"""Homepage Editor v1.0.0 - a small web UI to edit gethomepage.dev config files.

SPDX-License-Identifier: MIT
Unofficial tool, not part of the Homepage project.

Every change first saves a dated backup of ALL config files, so any change can
be rolled back from the Backups tab.
"""
import difflib
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import threading
import secrets
import time
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml
from flask import Flask, Response, jsonify, redirect, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
EDITOR_USER = os.environ.get("EDITOR_USER", "admin")
EDITOR_PASSWORD = os.environ.get("EDITOR_PASSWORD", "")
VERSION = "1.0.0"


# "login" (default): login page. "off": no login at all; only when something else protects the
# editor (Authelia, Authentik, an NPM access list, a VPN...). Chosen in setup.sh.
AUTH_MODE = "off" if os.environ.get("AUTH_MODE", "login").strip().lower() == "off" else "login"

# Project links shown in the app (Feedback, Help, Support us, Discord...). Empty = hidden.
PROJECT = {
    "github": "https://github.com/bottega-lab/homepage-editor",
    "discord": "",   # a Discord invite that never expires, e.g. https://discord.gg/xxxx
    "sponsor": "",   # e.g. https://github.com/sponsors/<you>
    "docs": "",      # external documentation; empty = README on GitHub (if github is set)
}

DATA_DIR = os.environ.get("DATA_DIR", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "editor-settings.json")
THEMES = ("system", "light", "dark")


def clean_host(host, port=""):
    host = re.sub(r"^[a-zA-Z]+://", "", (host or "").strip()).split("/")[0]
    if port and host.count(":") == 1:  # "host:port" typed in the host field: the port field wins
        host = host.split(":")[0]
    return host


def build_url(proto, host, port):
    proto = (proto or "http").strip().rstrip(":/") or "http"
    port = str(port or "").strip()
    host = clean_host(host, port)
    if not host:
        return ""
    default = (proto == "http" and port == "80") or (proto == "https" and port == "443")
    return "%s://%s%s" % (proto, host, ":" + port if port and not default else "")


def env_homepage():
    """Homepage address as set by setup.sh (.env)."""
    return {"protocol": (os.environ.get("HOMEPAGE_PROTOCOL", "").strip() or "http").rstrip(":/"),
            "host": os.environ.get("HOMEPAGE_HOST", "").strip(),
            "port": os.environ.get("HOMEPAGE_PORT", "").strip()}


def env_keep():
    try:
        return int(os.environ.get("BACKUP_KEEP", "100") or 0)
    except ValueError:
        return 100


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- editor settings (Configure)
# Saved in DATA_DIR/editor-settings.json. A value changed in Configure wins over .env, until the
# matching value in .env is changed (e.g. by running setup.sh again): then .env wins again.
# That is also how a forgotten password is reset.
def load_settings_file():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print("WARNING: could not read %s (%s); using the .env settings." % (SETTINGS_FILE, exc),
              flush=True)
        return {}


def save_settings_file(data):
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SETTINGS_FILE)
    except OSError as exc:
        raise UserError("Could not save the settings: the data folder is not writable (%s). "
                        "Run ./setup.sh again, it creates the folder with the right owner." % exc)


def reconcile(data):
    """Drop Configure values whose .env counterpart changed since they were saved."""
    changed = False
    auth = data.get("auth")
    if auth and AUTH_MODE == "login" and not check_password_hash(auth.get("env_fp", ""), EDITOR_USER + "\0" + EDITOR_PASSWORD):
        print("Login in .env changed: the login set in Configure is replaced by the .env login.",
              flush=True)
        data.pop("auth"); changed = True
    for key, env_value in (("homepage", env_homepage()), ("backup_keep", env_keep())):
        item = data.get(key)
        if item and item.get("env_fp") != fingerprint(env_value):
            print("%s in .env changed: using the .env value again." % key, flush=True)
            data.pop(key); changed = True
    return changed


SETTINGS = {}


def homepage_cfg():
    item = SETTINGS.get("homepage")
    if item:
        return dict(item["value"], source="editor")
    return dict(env_homepage(), source="setup")


def homepage_url():
    if not SETTINGS.get("homepage") and os.environ.get("HOMEPAGE_URL", "").strip():
        return os.environ["HOMEPAGE_URL"].strip()  # older .env files
    hp = homepage_cfg()
    return build_url(hp["protocol"], hp["host"], hp["port"])


def backup_keep():
    item = SETTINGS.get("backup_keep")
    return int(item["value"]) if item else env_keep()


# Logo shown at the left of the header: DATA_DIR/logo.<ext>
LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              "svg": "image/svg+xml"}
LOGO_MAX = 2 * 1024 * 1024


def logo_file():
    for ext in LOGO_TYPES:
        path = os.path.join(DATA_DIR, "logo." + ext)
        if os.path.isfile(path):
            return path, ext
    return None, None


def sniff_image(data):
    """Return the image type from the file's first bytes (not from its name), or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:2048].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if (head.startswith(b"<svg") or head.startswith(b"<?xml")) and b"<svg" in data[:4096].lower():
        return "svg"
    return None


def theme():
    return SETTINGS.get("theme") if SETTINGS.get("theme") in THEMES else "system"
try:
    TZ = ZoneInfo(os.environ.get("TZ") or "UTC")
except Exception:  # unknown zone name
    print("WARNING: unknown time zone %r, using UTC. Run ./setup.sh to choose one."
          % os.environ.get("TZ"), flush=True)
    TZ = ZoneInfo("UTC")

# Files that are backed up and can be edited.
MANAGED = ["services.yaml", "settings.yaml", "bookmarks.yaml", "widgets.yaml",
           "docker.yaml", "custom.css", "custom.js"]
SERVICE_FIELDS = ["icon", "href", "description", "siteMonitor", "ping", "statusStyle", "target"]
BOOKMARK_FIELDS = ["abbr", "icon", "href", "description"]
SETTING_TEXT = ["title", "description", "favicon", "theme", "color", "headerStyle", "language",
                "target", "statusStyle", "cardBlur", "iconStyle", "bookmarksStyle"]
SETTING_BOOL = ["fullWidth", "useEqualHeights", "disableCollapse", "groupsInitiallyCollapsed",
                "hideVersion", "hideErrors", "showStats"]
SETTING_INT = ["maxGroupColumns", "maxBookmarkGroupColumns"]
SETTING_KNOWN = SETTING_TEXT + SETTING_BOOL + SETTING_INT + ["background", "layout"]
LAYOUT_TEXT = ["tab", "style", "icon"]
LAYOUT_BOOL = ["header", "initiallyCollapsed", "useEqualHeights"]
LAYOUT_INT = ["columns"]
BACKUP_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?$")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024  # logo uploads (2 MB) + form overhead
lock = threading.Lock()


class UserError(Exception):
    """A problem the user can fix; shown as-is in the UI."""


# ---------------------------------------------------------------- YAML helpers
class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):  # indent lists under keys
        return super().increase_indent(flow, False)


def to_yaml(data):
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=4096)


def parse_yaml(text, where):
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UserError("%s: not valid YAML.\n%s" % (where, exc))


# ---------------------------------------------------------------- file helpers
def cfg_path(name):
    if name not in MANAGED:
        raise UserError("Unknown file: %s" % name)
    return os.path.join(CONFIG_DIR, name)


def read_text(name):
    path = cfg_path(name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def current_files():
    out = {}
    for name in MANAGED:
        text = read_text(name)
        if text is not None:
            out[name] = text
    return out


def validate(name, text):
    """Refuse to save content that Homepage could not load."""
    if not name.endswith(".yaml"):
        return
    data = parse_yaml(text, name)
    if data is None:
        return
    if name in ("services.yaml", "bookmarks.yaml"):
        if not isinstance(data, list):
            raise UserError("%s must be a list of groups (lines starting with '- ')." % name)
        for group in data:
            if not isinstance(group, dict) or len(group) != 1:
                raise UserError("%s: every group must be '- Group name:' followed by its items." % name)
            items = list(group.values())[0]
            if items is not None and not isinstance(items, list):
                raise UserError("%s: group '%s' must contain a list of items." % (name, list(group)[0]))
    elif name == "settings.yaml" and not isinstance(data, dict):
        raise UserError("settings.yaml must be a set of 'key: value' lines.")
    elif name == "widgets.yaml" and not isinstance(data, list):
        raise UserError("widgets.yaml must be a list of widgets (lines starting with '- ').")


# ---------------------------------------------------------------- backups
def now():
    return datetime.now(TZ)


def created_at(bid):
    """Exact creation time of a backup (from meta.json), used for ordering."""
    try:
        with open(os.path.join(BACKUP_DIR, bid, "meta.json"), encoding="utf-8") as fh:
            return datetime.fromisoformat(json.load(fh)["created"])
    except (OSError, ValueError, KeyError):
        return datetime.strptime(bid[:19], "%Y-%m-%d_%H-%M-%S").replace(tzinfo=TZ)


def backup_ids():
    """Backup ids, oldest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    ids = [d for d in os.listdir(BACKUP_DIR)
           if BACKUP_ID.match(d) and os.path.isdir(os.path.join(BACKUP_DIR, d))]
    return sorted(ids, key=lambda b: (created_at(b), b))


def backup_files(bid):
    folder = os.path.join(BACKUP_DIR, bid)
    out = {}
    for name in MANAGED:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


def check_id(bid):
    if not BACKUP_ID.match(bid or "") or not os.path.isdir(os.path.join(BACKUP_DIR, bid)):
        raise UserError("Backup not found: %s" % bid)


def make_backup(reason, note="", force=False):
    """Save all current config files. Returns the backup id, or None when the
    current files are identical to the newest backup (nothing new to save)."""
    files = current_files()
    if not files:
        return None
    ids = backup_ids()
    if ids and not force and backup_files(ids[-1]) == files:
        return None
    stamp = now()
    bid = base = stamp.strftime("%Y-%m-%d_%H-%M-%S")
    n = 2
    while os.path.exists(os.path.join(BACKUP_DIR, bid)):
        bid = "%s_%d" % (base, n)
        n += 1
    folder = os.path.join(BACKUP_DIR, bid)
    os.makedirs(folder)
    for name, text in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"created": stamp.isoformat(), "reason": reason, "note": note,
                   "files": sorted(files)}, fh, indent=2)
    prune(keep_id=bid)
    return bid


def prune(keep_id):
    keep = backup_keep()
    if keep <= 0:
        return
    ids = backup_ids()
    for bid in ids[:max(0, len(ids) - keep)]:
        if bid != keep_id:
            shutil.rmtree(os.path.join(BACKUP_DIR, bid), ignore_errors=True)


def describe(bid):
    meta = {}
    try:
        with open(os.path.join(BACKUP_DIR, bid, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        pass
    when = created_at(bid).astimezone(TZ)
    return {"id": bid, "date": when.strftime("%a %d %b %Y"), "time": when.strftime("%H:%M:%S"),
            "reason": meta.get("reason", ""), "note": meta.get("note", ""),
            "files": meta.get("files") or sorted(backup_files(bid))}


def save_file(name, text, reason):
    """Validate, back up everything, then write. Returns a result dict."""
    if text and not text.endswith("\n"):
        text += "\n"
    validate(name, text)
    with lock:
        if read_text(name) == text:
            return {"changed": False, "message": "No changes to save."}
        bid = make_backup(reason)
        with open(cfg_path(name), "w", encoding="utf-8") as fh:  # in place: keeps owner/permissions
            fh.write(text)
    return {"changed": True, "backup": describe(bid) if bid else None}


# ---------------------------------------------------------------- auth & safety
# Login page + signed session cookie (password managers can save and fill the form).
PUBLIC = {"/favicon.svg", "/favicon.png", "/favicon.ico", "/apple-touch-icon.png", "/logo", "/login",
          "/api/public", "/api/login", "/api/login/passkey/options", "/api/login/passkey/verify"}
LOCK_AFTER, LOCK_SECONDS = 5, 300      # 5 wrong passwords in a row -> wait 5 minutes
_fails = {}                            # ip -> [count, locked_until]


def load_secret():
    """Key that signs the session cookie, kept in the settings folder so logins survive restarts."""
    path = os.path.join(DATA_DIR, "secret.key")
    try:
        with open(path, "rb") as fh:
            key = fh.read()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    try:
        with open(path, "wb") as fh:
            fh.write(key)
        os.chmod(path, 0o600)
    except OSError:
        print("WARNING: settings folder not writable: logins end when the editor restarts.", flush=True)
    return key


app.secret_key = load_secret()
app.config.update(SESSION_COOKIE_NAME="he_session", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax")


def current_user():
    auth = SETTINGS.get("auth")
    return auth["user"] if auth else EDITOR_USER


def check_login(user, password):
    auth = SETTINGS.get("auth")
    if auth:
        return hmac.compare_digest(user, auth["user"]) and check_password_hash(auth["hash"], password)
    return hmac.compare_digest(user, EDITOR_USER) and hmac.compare_digest(password, EDITOR_PASSWORD)


def cred_version():
    """Changes whenever the login changes or "log out everywhere" is used: old sessions stop working."""
    auth = SETTINGS.get("auth")
    base = auth["hash"] if auth else EDITOR_USER + "\0" + EDITOR_PASSWORD
    return hashlib.sha256((base + "|" + str(SETTINGS.get("session_salt", ""))).encode()).hexdigest()[:20]


def idle_minutes():
    try:
        return max(0, int(SETTINGS.get("idle_minutes", 60)))
    except (TypeError, ValueError):
        return 60


def remember_days():
    try:
        return min(365, max(1, int(SETTINGS.get("remember_days", 30))))
    except (TypeError, ValueError):
        return 30


def start_session(remember):
    session.clear()
    session["u"] = current_user()
    session["v"] = cred_version()
    session["t"] = int(time.time())
    session["r"] = bool(remember)
    session.permanent = bool(remember)
    app.permanent_session_lifetime = timedelta(days=remember_days())


def logged_in():
    if AUTH_MODE == "off":
        return True
    if session.get("v") != cred_version() or session.get("u") != current_user():
        return False
    now_ts = int(time.time())
    last = int(session.get("t", 0))
    if session.get("r"):
        if now_ts - last > remember_days() * 86400:
            return False
    elif idle_minutes() and now_ts - last > idle_minutes() * 60:
        return False
    if now_ts - last > 60:  # refresh the activity time at most once a minute
        session["t"] = now_ts
        app.permanent_session_lifetime = timedelta(days=remember_days())
    return True


def client_ip():
    return request.remote_addr or "?"


def check_lock():
    count, until = _fails.get(client_ip(), [0, 0])
    if until > time.time():
        raise UserError("Too many wrong attempts. Try again in %d minutes."
                        % max(1, round((until - time.time()) / 60)))


def note_fail():
    entry = _fails.setdefault(client_ip(), [0, 0])
    entry[0] += 1
    if entry[0] >= LOCK_AFTER:
        entry[0], entry[1] = 0, time.time() + LOCK_SECONDS


@app.before_request
def guard():
    if request.path in PUBLIC:
        pass
    elif AUTH_MODE == "login" and (not EDITOR_PASSWORD or EDITOR_PASSWORD == "change-me"):
        return Response("No password set. Run ./setup.sh (or set EDITOR_PASSWORD in .env), "
                        "then: docker compose up -d\n", 503, mimetype="text/plain")
    elif not logged_in():
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify(error="Please log in again.", login=True), 401
        return redirect("/login")
    # Changes must come from this app's own pages (blocks cross-site form posts).
    if request.method in ("POST", "PUT", "DELETE") and request.path != "/login" and \
            request.headers.get("X-Requested-With") != "homepage-editor":
        return jsonify(error="Request refused."), 403
    return None


@app.get("/login")
def login_page():
    if AUTH_MODE == "off" or logged_in():
        return redirect("/")
    return send_from_directory(STATIC, "login.html")


@app.post("/login")
def login_form():
    """Plain HTML form post: the most reliable way for password managers to save and fill it."""
    if AUTH_MODE == "off":
        return redirect("/")
    sent_from = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if sent_from and sent_from.split("://", 1)[-1].split("/", 1)[0] != request.host:
        return redirect("/login?e=refused")
    try:
        check_lock()
    except UserError:
        return redirect("/login?e=locked")
    if not check_login(request.form.get("username", ""), request.form.get("password", "")):
        note_fail()
        return redirect("/login?e=wrong")
    _fails.pop(client_ip(), None)
    start_session(request.form.get("remember") == "on")
    return redirect("/")


@app.get("/api/public")
def public_info():
    """What the login page needs: look, logo, and whether passkeys are set up."""
    return jsonify(theme=theme(), logo=logo_info(), auth=AUTH_MODE,
                   passkeys=len(SETTINGS.get("passkeys") or []), remember_days=remember_days())


@app.post("/api/login")
def api_login():
    b = body()
    check_lock()
    if not check_login(str(b.get("user") or ""), str(b.get("password") or "")):
        note_fail()
        raise UserError("Wrong user name or password.")
    _fails.pop(client_ip(), None)
    start_session(b.get("remember"))
    return jsonify(ok=True)


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


# ---------------------------------------------------------------- passkeys (WebAuthn)
# Browsers allow passkeys only on https:// pages (or http://localhost). The passkey is bound to
# the host name used when it was added (e.g. editor.example.com).
try:
    import webauthn
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)
    PASSKEYS = True
except ImportError:  # library missing: passkeys simply not offered
    PASSKEYS = False


def rp_id():
    return request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"


def origin():
    proto = "https" if (request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https") else "http"
    return "%s://%s" % (proto, request.host)


def passkey_list():
    return [p for p in (SETTINGS.get("passkeys") or []) if isinstance(p, dict)]


def user_handle():
    if not SETTINGS.get("user_handle"):
        save_setting("user_handle", secrets.token_urlsafe(16))
    return SETTINGS["user_handle"].encode()


def need_passkeys():
    if not PASSKEYS:
        raise UserError("Passkeys are not available in this installation.")


@app.post("/api/passkey/register/options")
def passkey_register_options():
    need_passkeys()
    opts = webauthn.generate_registration_options(
        rp_id=rp_id(), rp_name="Homepage Editor", user_id=user_handle(), user_name=current_user(),
        user_display_name=current_user(),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["id"])) for p in passkey_list()],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.PREFERRED))
    session["reg_ch"] = bytes_to_base64url(opts.challenge)
    return Response(options_to_json(opts), mimetype="application/json")


@app.post("/api/passkey/register/verify")
def passkey_register_verify():
    need_passkeys()
    b = body()
    challenge = session.pop("reg_ch", None)
    if not challenge:
        raise UserError("Adding the passkey took too long. Try again.")
    try:
        v = webauthn.verify_registration_response(
            credential=b.get("credential"), expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id(), expected_origin=origin())
    except Exception as exc:
        raise UserError("The passkey could not be added: %s" % exc)
    name = str(b.get("name") or "").strip()[:60] or "Passkey"
    keys = passkey_list()
    keys.append({"id": bytes_to_base64url(v.credential_id), "key": bytes_to_base64url(v.credential_public_key),
                 "count": v.sign_count, "name": name, "rp": rp_id(),
                 "added": datetime.now(TZ).strftime("%d %b %Y %H:%M"), "used": ""})
    save_setting("passkeys", keys)
    return jsonify(ok=True)


@app.delete("/api/passkey/<pid>")
def passkey_delete(pid):
    keys = [p for p in passkey_list() if p["id"] != pid]
    save_setting("passkeys", keys or None)
    return jsonify(ok=True)


@app.post("/api/login/passkey/options")
def passkey_login_options():
    need_passkeys()
    check_lock()
    opts = webauthn.generate_authentication_options(
        rp_id=rp_id(), user_verification=UserVerificationRequirement.PREFERRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["id"])) for p in passkey_list()])
    session["auth_ch"] = bytes_to_base64url(opts.challenge)
    return Response(options_to_json(opts), mimetype="application/json")


@app.post("/api/login/passkey/verify")
def passkey_login_verify():
    need_passkeys()
    check_lock()
    b = body()
    challenge = session.pop("auth_ch", None)
    cred = b.get("credential") or {}
    stored = next((p for p in passkey_list() if p["id"] == cred.get("id")), None)
    if not challenge or not stored:
        note_fail()
        raise UserError("This passkey is not known here. Log in with your password.")
    try:
        v = webauthn.verify_authentication_response(
            credential=cred, expected_challenge=base64url_to_bytes(challenge), expected_rp_id=rp_id(),
            expected_origin=origin(), credential_public_key=base64url_to_bytes(stored["key"]),
            credential_current_sign_count=int(stored.get("count") or 0))
    except Exception as exc:
        note_fail()
        raise UserError("Passkey login failed: %s" % exc)
    keys = passkey_list()
    for p in keys:
        if p["id"] == stored["id"]:
            p["count"] = v.new_sign_count
            p["used"] = datetime.now(TZ).strftime("%d %b %Y %H:%M")
    save_setting("passkeys", keys)
    _fails.pop(client_ip(), None)
    start_session(b.get("remember"))
    return jsonify(ok=True)


@app.errorhandler(413)
def too_big(_exc):
    return jsonify(error="The file is too big: the maximum is 2 MB."), 413


@app.errorhandler(UserError)
def user_error(exc):
    return jsonify(error=str(exc)), 400


@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


STATIC = os.path.join(os.path.dirname(__file__), "static")


@app.get("/favicon.svg")
def favicon_svg():
    return send_from_directory(STATIC, "favicon.svg", mimetype="image/svg+xml", max_age=86400)


@app.get("/favicon.png")
@app.get("/favicon.ico")
def favicon_png():
    return send_from_directory(STATIC, "favicon-32.png", mimetype="image/png", max_age=86400)


@app.get("/apple-touch-icon.png")
def touch_icon():
    return send_from_directory(STATIC, "apple-touch-icon.png", mimetype="image/png", max_age=86400)


def logo_info():
    path, _ = logo_file()
    return {"url": "/logo?v=%d" % os.stat(path).st_mtime_ns if path else "",
            "show_title": SETTINGS.get("show_title", True) is not False}


@app.get("/logo")
def get_logo():
    path, ext = logo_file()
    if not path:
        return Response("No logo\n", 404, mimetype="text/plain")
    resp = send_file(path, mimetype=LOGO_TYPES[ext], max_age=0)
    # An uploaded SVG could contain scripts: never let it run, even when opened directly.
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.get("/api/info")
def info():
    return jsonify(version=VERSION, homepage_url=homepage_url(), files=MANAGED, keep=backup_keep(),
                   theme=theme(), logo=logo_info(), existing=sorted(current_files()),
                   user=current_user() if AUTH_MODE == "login" else "", auth=AUTH_MODE,
                   links=project_links(), backups=len(backup_ids()))


def project_links():
    gh = PROJECT["github"].rstrip("/")
    return {"github": gh, "issues": gh + "/issues" if gh else "", "discord": PROJECT["discord"],
            "sponsor": PROJECT["sponsor"], "docs": PROJECT["docs"] or (gh + "#readme" if gh else "")}


# ---------------------------------------------------------------- Configure
def data_writable():
    return os.path.isdir(DATA_DIR) and os.access(DATA_DIR, os.W_OK)


@app.get("/api/config")
def get_config():
    hp = homepage_cfg()
    return jsonify(
        login={"user": current_user(), "source": "editor" if SETTINGS.get("auth") else "setup",
               "auth": AUTH_MODE, "idle_minutes": idle_minutes(), "remember_days": remember_days(),
               "remembered": bool(session.get("r"))},
        passkeys={"available": PASSKEYS,
                  "list": [{k: p.get(k, "") for k in ("id", "name", "rp", "added", "used")} for p in passkey_list()]},
        links=project_links(),
        theme=theme(),
        logo=logo_info(),
        homepage={"protocol": hp["protocol"], "host": hp["host"], "port": hp["port"],
                  "url": homepage_url(), "source": hp["source"], "setup": env_homepage()},
        backups={"keep": backup_keep(), "source": "editor" if SETTINGS.get("backup_keep") else "setup",
                 "setup": env_keep()},
        about={"version": VERSION,
               "stack": os.environ.get("INFO_STACK_NAME", ""),
               "editor_port": os.environ.get("INFO_EDITOR_PORT", ""),
               "config_path": os.environ.get("INFO_CONFIG_PATH", "") or CONFIG_DIR,
               "backup_path": os.environ.get("INFO_BACKUP_PATH", "") or BACKUP_DIR,
               "data_path": os.environ.get("INFO_DATA_PATH", "") or DATA_DIR,
               "time_zone": str(TZ), "backups": len(backup_ids()),
               "data_writable": data_writable()})


def save_setting(key, value):
    with lock:
        data = dict(SETTINGS)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        save_settings_file(data)
        SETTINGS.clear(); SETTINGS.update(data)


def body():
    return request.get_json(force=True, silent=True) or {}


@app.put("/api/config/login")
def put_login():
    b = body()
    check_lock()
    if not check_login(current_user(), str(b.get("current") or "")):
        note_fail()
        raise UserError("The current password is not correct.")
    user = str(b.get("user") or "").strip()
    password = str(b.get("password") or "")
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", user):
        raise UserError("User name: 1 to 64 letters, digits or . _ @ - (no spaces).")
    if len(password) < 8:
        raise UserError("The new password must have at least 8 characters.")
    if password != str(b.get("password2") or ""):
        raise UserError("The two new passwords are not the same.")
    save_setting("auth", {"user": user, "hash": generate_password_hash(password),
                          "env_fp": generate_password_hash(EDITOR_USER + "\0" + EDITOR_PASSWORD)})
    start_session(session.get("r"))  # stay logged in here; other browsers must log in again
    return jsonify(ok=True)


@app.delete("/api/config/login")
def reset_login():
    check_lock()
    if not check_login(current_user(), str(body().get("current") or "")):
        note_fail()
        raise UserError("The current password is not correct.")
    save_setting("auth", None)
    start_session(session.get("r"))
    return jsonify(ok=True)


@app.put("/api/config/sessions")
def put_sessions():
    b = body()
    try:
        idle = int(b.get("idle_minutes"))
        days = int(b.get("remember_days"))
    except (TypeError, ValueError):
        raise UserError("Use whole numbers.")
    if not 0 <= idle <= 10080:
        raise UserError("Log out after inactivity: 0 to 10080 minutes (0 = never).")
    if not 1 <= days <= 365:
        raise UserError("Stay logged in: 1 to 365 days.")
    with lock:
        data = dict(SETTINGS); data["idle_minutes"] = idle; data["remember_days"] = days
        save_settings_file(data); SETTINGS.clear(); SETTINGS.update(data)
    return jsonify(ok=True)


@app.post("/api/config/logout-everywhere")
def logout_everywhere():
    save_setting("session_salt", secrets.token_hex(8))
    start_session(session.get("r"))  # this browser stays logged in
    return jsonify(ok=True)


@app.put("/api/config/theme")
def put_theme():
    value = str(body().get("theme") or "")
    if value not in THEMES:
        raise UserError("Unknown appearance: %s" % value)
    save_setting("theme", value)
    return jsonify(ok=True, theme=value)


@app.post("/api/config/logo")
def put_logo():
    up = request.files.get("logo")
    if not up:
        raise UserError("Choose a picture first.")
    data = up.read(LOGO_MAX + 1)
    if len(data) > LOGO_MAX:
        raise UserError("The picture is too big: the maximum is 2 MB.")
    ext = sniff_image(data)
    if not ext:
        raise UserError("Not a supported picture. Use PNG, JPG, GIF, WebP or SVG.")
    if not data_writable():
        raise UserError("Could not save the logo: the settings folder is not writable. Run ./setup.sh again.")
    with lock:
        old, _ = logo_file()
        tmp = os.path.join(DATA_DIR, "logo.upload")
        with open(tmp, "wb") as fh:
            fh.write(data)
        if old:
            os.remove(old)
        os.replace(tmp, os.path.join(DATA_DIR, "logo." + ext))
    return jsonify(ok=True, logo=logo_info())


@app.delete("/api/config/logo")
def delete_logo():
    with lock:
        path, _ = logo_file()
        if path:
            os.remove(path)
    return jsonify(ok=True, logo=logo_info())


@app.put("/api/config/title")
def put_title():
    save_setting("show_title", bool(body().get("show")))
    return jsonify(ok=True, logo=logo_info())


@app.put("/api/config/homepage")
def put_homepage():
    b = body()
    proto = str(b.get("protocol") or "http").strip()
    port = str(b.get("port") or "").strip()
    host = clean_host(str(b.get("host") or ""), port)
    if proto not in ("http", "https"):
        raise UserError("Protocol must be http or https.")
    if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:]+\]", host or "-"):
        raise UserError("Host: an IP address or host name, without http:// and without a port.")
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        raise UserError("Port must be a number from 1 to 65535, or empty for no port.")
    save_setting("homepage", {"value": {"protocol": proto, "host": host, "port": port},
                              "env_fp": fingerprint(env_homepage())})
    return jsonify(ok=True, url=homepage_url())


@app.delete("/api/config/homepage")
def reset_homepage():
    save_setting("homepage", None)
    return jsonify(ok=True, url=homepage_url())


@app.put("/api/config/backups")
def put_backups():
    try:
        keep = int(body().get("keep"))
    except (TypeError, ValueError):
        raise UserError("Number of backups must be a whole number (0 = keep all).")
    if not 0 <= keep <= 100000:
        raise UserError("Number of backups must be between 0 and 100000.")
    save_setting("backup_keep", {"value": keep, "env_fp": fingerprint(env_keep())})
    return jsonify(ok=True)


@app.delete("/api/config/backups")
def reset_backups():
    save_setting("backup_keep", None)
    return jsonify(ok=True)


# ---------------------------------------------------------------- services & bookmarks
def load_items(name, fields, kind):
    text = read_text(name) or ""
    data = parse_yaml(text, name) or []
    if not isinstance(data, list):
        raise UserError("%s is not a list of groups. Fix it in the Files tab." % name)
    groups, unsupported = [], []
    for group in data:
        if not isinstance(group, dict) or len(group) != 1:
            unsupported.append("an entry that is not a group")
            continue
        gname, items = next(iter(group.items()))
        rows = []
        for item in items or []:
            if not isinstance(item, dict) or len(item) != 1:
                unsupported.append("an odd entry in '%s'" % gname)
                continue
            iname, props = next(iter(item.items()))
            if kind == "bookmark" and isinstance(props, list):
                props = props[0] if props and isinstance(props[0], dict) else {}
            if isinstance(props, list):
                unsupported.append("nested group '%s' inside '%s'" % (iname, gname))
                continue
            props = props or {}
            row = {"name": str(iname)}
            for key in fields:
                value = props.get(key)
                row[key] = "" if value is None else str(value)
            rest = {k: v for k, v in props.items() if k not in fields}
            row["extra"] = to_yaml(rest) if rest else ""
            rows.append(row)
        groups.append({"name": str(gname), "items": rows})
    return {"groups": groups, "unsupported": unsupported}


def build_items(groups, fields, kind):
    out, group_names = [], set()
    for group in groups:
        gname = str(group.get("name") or "").strip()
        if not gname:
            raise UserError("A group has no name.")
        if gname in group_names:
            raise UserError("Two groups are called '%s'. Group names must be unique." % gname)
        group_names.add(gname)
        items, names = [], set()
        for row in group.get("items") or []:
            iname = str(row.get("name") or "").strip()
            if not iname:
                raise UserError("A %s in group '%s' has no name." % (kind, gname))
            if iname in names:
                raise UserError("Group '%s' has two %ss called '%s'. Names must be unique "
                                "inside a group." % (gname, kind, iname))
            names.add(iname)
            props = {}
            for key in fields:
                value = str(row.get(key) or "").strip()
                if value:
                    props[key] = value
            extra_text = str(row.get("extra") or "").strip()
            if extra_text:
                extra = parse_yaml(extra_text, "Advanced settings of '%s'" % iname)
                if extra is not None and not isinstance(extra, dict):
                    raise UserError("Advanced settings of '%s' must be 'key: value' lines." % iname)
                for key in (extra or {}):
                    if key in props:
                        raise UserError("'%s' of '%s' is set both in the form and in Advanced."
                                        % (key, iname))
                props.update(extra or {})
            items.append({iname: [props] if kind == "bookmark" else props})
        out.append({gname: items})
    return out


@app.get("/api/services")
def get_services():
    return jsonify(load_items("services.yaml", SERVICE_FIELDS, "service"))


@app.put("/api/services")
def put_services():
    current = load_items("services.yaml", SERVICE_FIELDS, "service")
    if current["unsupported"]:
        raise UserError("services.yaml contains %s, which this form cannot edit. "
                        "Use the Files tab." % ", ".join(current["unsupported"]))
    data = build_items(request.get_json(force=True).get("groups") or [], SERVICE_FIELDS, "service")
    return jsonify(save_file("services.yaml", to_yaml(data), "Before saving services"))


@app.get("/api/bookmarks")
def get_bookmarks():
    return jsonify(load_items("bookmarks.yaml", BOOKMARK_FIELDS, "bookmark"))


@app.put("/api/bookmarks")
def put_bookmarks():
    current = load_items("bookmarks.yaml", BOOKMARK_FIELDS, "bookmark")
    if current["unsupported"]:
        raise UserError("bookmarks.yaml contains %s, which this form cannot edit. "
                        "Use the Files tab." % ", ".join(current["unsupported"]))
    data = build_items(request.get_json(force=True).get("groups") or [], BOOKMARK_FIELDS, "bookmark")
    return jsonify(save_file("bookmarks.yaml", to_yaml(data), "Before saving bookmarks"))


# ---------------------------------------------------------------- settings
def load_settings():
    data = parse_yaml(read_text("settings.yaml") or "", "settings.yaml") or {}
    if not isinstance(data, dict):
        raise UserError("settings.yaml is not a set of 'key: value' lines. Fix it in the Files tab.")
    return data


def group_names(name):
    data = parse_yaml(read_text(name) or "", name) or []
    if not isinstance(data, list):
        return []
    return [str(next(iter(g))) for g in data if isinstance(g, dict) and len(g) == 1]


def to_bool(value):
    if value in (True, False):
        return value
    if str(value).lower() in ("true", "yes", "1"):
        return True
    if str(value).lower() in ("false", "no", "0"):
        return False
    return None


@app.get("/api/settings")
def get_settings():
    data = load_settings()
    fields = {}
    for key in SETTING_TEXT:
        fields[key] = "" if data.get(key) is None else str(data[key])
    for key in SETTING_BOOL:
        fields[key] = to_bool(data[key]) if key in data else None
    for key in SETTING_INT:
        fields[key] = data.get(key)
    background = data.get("background")
    if isinstance(background, str):
        background = {"image": background}
    background = background if isinstance(background, dict) else {}
    layout = data.get("layout") if isinstance(data.get("layout"), dict) else {}
    rows = []
    for gname, opts in layout.items():
        opts = opts if isinstance(opts, dict) else {}
        row = {"name": str(gname)}
        for key in LAYOUT_TEXT:
            row[key] = "" if opts.get(key) is None else str(opts[key])
        for key in LAYOUT_BOOL:
            row[key] = to_bool(opts[key]) if key in opts else None
        row["columns"] = opts.get("columns")
        row["other"] = {k: v for k, v in opts.items()
                        if k not in LAYOUT_TEXT + LAYOUT_BOOL + LAYOUT_INT}
        rows.append(row)
    extra = {k: v for k, v in data.items() if k not in SETTING_KNOWN}
    return jsonify(fields=fields, background=background, layout=rows,
                   extra=to_yaml(extra) if extra else "",
                   serviceGroups=group_names("services.yaml"),
                   bookmarkGroups=group_names("bookmarks.yaml"))


def merge_settings(desired, managed_keys):
    """Replace managed_keys in settings.yaml with `desired`, keep the key order."""
    current = load_settings()
    out = {}
    for key, value in current.items():
        if key in managed_keys:
            if key in desired:
                out[key] = desired[key]
        else:
            out[key] = value
    for key, value in desired.items():
        if key not in out:
            out[key] = value
    return out


@app.put("/api/settings/appearance")
def put_appearance():
    body = request.get_json(force=True)
    fields = body.get("fields") or {}
    desired = {}
    for key in SETTING_TEXT:
        value = str(fields.get(key) or "").strip()
        if value:
            desired[key] = value
    for key in SETTING_BOOL:
        value = to_bool(fields.get(key)) if fields.get(key) is not None else None
        if value is not None:
            desired[key] = value
    for key in SETTING_INT:
        value = fields.get(key)
        if value not in (None, ""):
            try:
                desired[key] = int(value)
            except (TypeError, ValueError):
                raise UserError("%s must be a whole number." % key)
    bg_in = body.get("background") or {}
    background = {}
    if str(bg_in.get("image") or "").strip():
        background["image"] = str(bg_in["image"]).strip()
    if bg_in.get("blur") is not None:  # "" is a valid value (default blur)
        background["blur"] = str(bg_in["blur"])
    for key in ("saturate", "brightness", "opacity"):
        if bg_in.get(key) not in (None, ""):
            background[key] = int(bg_in[key])
    if background:
        desired["background"] = background
    extra_text = str(body.get("extra") or "").strip()
    extra = parse_yaml(extra_text, "Other settings") if extra_text else {}
    extra = extra or {}
    if not isinstance(extra, dict):
        raise UserError("Other settings must be 'key: value' lines.")
    for key in extra:
        if key in SETTING_KNOWN:
            raise UserError("'%s' is in Other settings but belongs in the form above "
                            "(or the Layout tab). Remove it from Other settings." % key)
    desired.update(extra)
    current = load_settings()
    managed = set(SETTING_KNOWN) - {"layout"} | {k for k in current if k not in SETTING_KNOWN}
    data = merge_settings(desired, managed)
    return jsonify(save_file("settings.yaml", to_yaml(data), "Before saving appearance"))


@app.put("/api/settings/layout")
def put_layout():
    rows = request.get_json(force=True).get("layout") or []
    layout, seen = {}, set()
    for row in rows:
        gname = str(row.get("name") or "").strip()
        if not gname:
            continue
        if gname in seen:
            raise UserError("'%s' is in the layout twice." % gname)
        seen.add(gname)
        opts = {}
        for key in LAYOUT_TEXT:
            value = str(row.get(key) or "").strip()
            if value:
                opts[key] = value
        if row.get("columns") not in (None, ""):
            try:
                opts["columns"] = int(row["columns"])
            except (TypeError, ValueError):
                raise UserError("Columns of '%s' must be a whole number." % gname)
        for key in LAYOUT_BOOL:
            if row.get(key) is not None:
                opts[key] = bool(to_bool(row[key]))
        opts.update(row.get("other") or {})
        layout[gname] = opts
    data = merge_settings({"layout": layout} if layout else {}, {"layout"})
    return jsonify(save_file("settings.yaml", to_yaml(data), "Before saving layout"))


# ---------------------------------------------------------------- raw files
@app.get("/api/file/<name>")
def get_file(name):
    text = read_text(name)
    return jsonify(name=name, text=text or "", exists=text is not None)


@app.put("/api/file/<name>")
def put_file(name):
    cfg_path(name)
    text = str(request.get_json(force=True).get("text") or "")
    return jsonify(save_file(name, text, "Before saving %s (Files tab)" % name))


@app.post("/api/validate/<name>")
def validate_file(name):
    cfg_path(name)
    validate(name, str(request.get_json(force=True).get("text") or ""))
    return jsonify(ok=True)


# ---------------------------------------------------------------- backups API
@app.get("/api/backups")
def list_backups():
    return jsonify(backups=[describe(b) for b in reversed(backup_ids())], keep=backup_keep())


@app.post("/api/backups")
def create_backup():
    note = str((request.get_json(force=True) or {}).get("note") or "").strip()[:200]
    with lock:
        bid = make_backup("Manual backup", note, force=True)
    if not bid:
        raise UserError("No config files found to back up.")
    return jsonify(backup=describe(bid))


@app.post("/api/backups/<bid>/restore")
def restore_backup(bid):
    check_id(bid)
    files = backup_files(bid)
    for name, text in files.items():
        validate(name, text)
    with lock:
        if all(read_text(n) == t for n, t in files.items()):
            return jsonify(changed=False, message="Your files already match this backup.")
        safety = make_backup("Before restoring backup of %s" % describe(bid)["date"] + " "
                             + describe(bid)["time"])
        for name, text in files.items():
            with open(cfg_path(name), "w", encoding="utf-8") as fh:
                fh.write(text)
    return jsonify(changed=True, restored=sorted(files),
                   backup=describe(safety) if safety else None)


@app.get("/api/backups/<bid>/diff")
def diff_backup(bid):
    check_id(bid)
    old, new = backup_files(bid), current_files()
    parts = []
    for name in MANAGED:
        if name not in old and name not in new:
            continue
        diff = difflib.unified_diff((old.get(name) or "").splitlines(True),
                                    (new.get(name) or "").splitlines(True),
                                    fromfile="backup/" + name, tofile="now/" + name)
        parts.append("".join(diff))
    text = "".join(p for p in parts if p)
    return jsonify(diff=text or "No differences: your files are the same as this backup.")


@app.get("/api/backups/<bid>/download")
def download_backup(bid):
    check_id(bid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in backup_files(bid).items():
            zf.writestr(name, text)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="homepage-backup-%s.zip" % bid)


@app.delete("/api/backups/<bid>")
def delete_backup(bid):
    check_id(bid)
    shutil.rmtree(os.path.join(BACKUP_DIR, bid))
    return jsonify(ok=True)


# Load Configure settings at start; drop those whose .env value changed since.
SETTINGS.update(load_settings_file())
if reconcile(SETTINGS):
    try:
        save_settings_file(SETTINGS)
    except UserError as exc:
        print("WARNING: %s" % exc, flush=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
