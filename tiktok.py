"""TikTok posting through TikTok's official Content Posting API (free).

Secrets (repo Settings -> Secrets and variables -> Actions -> Secrets):
  TIKTOK_CLIENT_KEY     from your app on developers.tiktok.com
  TIKTOK_CLIENT_SECRET  from the same app
  TIKTOK_VAULT_KEY      any long random password you make up (it locks the saved TikTok login)
Optional variable (Settings -> Secrets and variables -> Actions -> Variables):
  TIKTOK_MODE           "inbox" (default): the video lands in your TikTok inbox and you tap Post in the app.
                        "direct": posts publicly by itself. Only works after TikTok audits your app;
                        before that TikTok forces every direct post to private (only you can see it).

The TikTok login is kept in tiktok_token.enc, encrypted with TIKTOK_VAULT_KEY, so the public repo never
shows it. Each run refreshes it and saves the new one.
"""
import base64, hashlib, json, os, time, urllib.parse
import requests

API = "https://open.tiktokapis.com/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.join(HERE, "tiktok_token.enc")
REDIRECT = os.environ.get("TIKTOK_REDIRECT_URI", "https://github.com/Mykel103/can-you-survive-autopost-")
KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
VKEY = os.environ.get("TIKTOK_VAULT_KEY", "")
MODE = (os.environ.get("TIKTOK_MODE") or "inbox").strip().lower()


def configured():
    return bool(KEY and SECRET and VKEY and os.path.exists(VAULT))


def _fernet():
    from cryptography.fernet import Fernet
    k = hashlib.pbkdf2_hmac("sha256", VKEY.encode(), b"cys-tiktok-vault", 200_000)
    return Fernet(base64.urlsafe_b64encode(k))


def _save(tok):
    open(VAULT, "wb").write(_fernet().encrypt(json.dumps(tok).encode()))


def _load():
    return json.loads(_fernet().decrypt(open(VAULT, "rb").read()))


def _token_call(**data):
    r = requests.post(f"{API}/oauth/token/", data=dict(client_key=KEY, client_secret=SECRET, **data),
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    j = r.json()
    if r.status_code >= 400 or "access_token" not in j:
        raise RuntimeError(f"TikTok login failed: {json.dumps({k: j.get(k) for k in ('error', 'error_description', 'log_id')})}")
    return j


def login_link():
    q = dict(client_key=KEY, scope="user.info.basic,video.upload,video.publish", response_type="code", redirect_uri=REDIRECT, state="cys")
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(q)


def save_code(code):
    code = urllib.parse.unquote(code.strip())
    if "code=" in code:  # they pasted the whole address-bar link
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)["code"][0]
    j = _token_call(grant_type="authorization_code", code=code, redirect_uri=REDIRECT)
    _save({"refresh_token": j["refresh_token"], "open_id": j.get("open_id")})
    return j.get("open_id")


def access_token():
    tok = _load()
    j = _token_call(grant_type="refresh_token", refresh_token=tok["refresh_token"])
    if j.get("refresh_token") and j["refresh_token"] != tok["refresh_token"]:
        tok["refresh_token"] = j["refresh_token"]
        _save(tok)
    return j["access_token"]


def _call(at, path, body):
    r = requests.post(f"{API}/{path}", json=body, timeout=120,
                      headers={"Authorization": f"Bearer {at}", "Content-Type": "application/json; charset=UTF-8"})
    j = r.json()
    err = j.get("error", {})
    if r.status_code >= 400 or (err.get("code") not in (None, "ok")):
        raise RuntimeError(f"TikTok {path} -> {r.status_code}: {json.dumps(err)[:500]}")
    return j.get("data", {})


def creator(at):
    return _call(at, "post/publish/creator_info/query/", {})


def post(item):
    at = access_token()
    path = os.path.join(HERE, item["file"])
    size = os.path.getsize(path)
    chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
    n = 1 if chunk == size else size // chunk  # TikTok: last chunk absorbs the remainder
    src = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": n}
    if MODE == "direct":
        info = creator(at)
        opts = info.get("privacy_level_options", [])
        privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opts else (opts[0] if opts else "SELF_ONLY")
        cap = item.get("tiktok_caption") or item["caption"]
        d = _call(at, "post/publish/video/init/", {"post_info": {"title": cap[:2200], "privacy_level": privacy, "disable_comment": False,
                                                                  "disable_duet": False, "disable_stitch": False}, "source_info": src})
    else:
        d = _call(at, "post/publish/inbox/video/init/", {"source_info": src})
    pid, url = d["publish_id"], d["upload_url"]
    with open(path, "rb") as f:
        for k in range(n):
            start = k * chunk
            end = size - 1 if k == n - 1 else start + chunk - 1
            f.seek(start); data = f.read(end - start + 1)
            r = requests.put(url, data=data, timeout=600, headers={"Content-Type": "video/mp4", "Content-Length": str(len(data)),
                                                                   "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code >= 400:
                raise RuntimeError(f"TikTok upload -> {r.status_code}: {r.text[:300]}")
    done = ("PUBLISH_COMPLETE",) if MODE == "direct" else ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE")
    for _ in range(60):
        s = _call(at, "post/publish/status/fetch/", {"publish_id": pid})
        st = s.get("status")
        if st in done:
            return pid
        if st == "FAILED":
            raise RuntimeError(f"TikTok could not process the video: {s.get('fail_reason')}")
        time.sleep(10)
    raise RuntimeError("TikTok processing timed out")
