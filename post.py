"""Free auto-poster: posts the next due video from queue.json as an Instagram Reel, a Facebook Page Reel and a TikTok.

Runs inside GitHub Actions (see .github/workflows/autopost.yml). Uses only Meta's official Graph API.

Secrets needed (repo Settings -> Secrets and variables -> Actions):
  META_PAGE_TOKEN  long-lived Facebook PAGE access token (never expires when made from a long-lived user token)
  FB_PAGE_ID       numeric id of your Facebook Page

Modes:
  python post.py check     verify the token and show which Page + Instagram account it can post to
  python post.py dry-run   show what would be posted next, post nothing
  python post.py run       post the next due video (default)
  python post.py now ID    post one queue item immediately, ignoring its time (for a test)
  python post.py tiktok-link       print the one-time TikTok login link
  python post.py tiktok-code URL   save the TikTok login (URL = the page TikTok sent you back to)
  python post.py tiktok-check      test the TikTok login
TikTok setup and secrets: see tiktok.py and README.md.
"""
import json, os, sys, time
from datetime import datetime, timezone
import requests
import tiktok

GV = os.environ.get("GRAPH_VERSION", "v25.0")
G = f"https://graph.facebook.com/{GV}"
TOKEN = os.environ.get("META_PAGE_TOKEN", "")
PAGE_ID = os.environ.get("FB_PAGE_ID", "")
MAX_ATTEMPTS = 3
HERE = os.path.dirname(os.path.abspath(__file__))


def api(method, path, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, path if path.startswith("http") else f"{G}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text}
    if r.status_code >= 400 or "error" in j:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {json.dumps(j.get('error', j))[:600]}")
    return j


def ig_user_id():
    j = api("GET", PAGE_ID, fields="name,instagram_business_account{id,username}")
    iga = j.get("instagram_business_account")
    if not iga:
        raise RuntimeError(f"Page '{j.get('name')}' has no linked Instagram Business/Creator account.")
    return iga["id"], iga.get("username"), j.get("name")


def _upload(upload_url, rel_path):
    data = open(os.path.join(HERE, rel_path), "rb").read()
    r = requests.post(upload_url, data=data, timeout=600,
                      headers={"Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(len(data))})
    if r.status_code >= 400:
        raise RuntimeError(f"upload -> {r.status_code}: {r.text[:400]}")


def post_instagram(item, url):
    igid, _, _ = ig_user_id()
    # Resumable upload straight from the repo checkout: no public video link needed.
    c = api("POST", f"{igid}/media", media_type="REELS", upload_type="resumable", caption=item["caption"], share_to_feed="true")
    cid = c["id"]
    up = c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{GV}/{cid}"
    _upload(up, item["file"])
    for _ in range(60):  # up to ~10 minutes of processing
        s = api("GET", cid, fields="status_code,status")
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the video: {s}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram processing timed out")
    m = api("POST", f"{igid}/media_publish", creation_id=cid)
    mid = m["id"]
    if item.get("first_comment"):
        try:
            api("POST", f"{mid}/comments", message=item["first_comment"])
        except Exception as e:  # comment is a bonus; never fail the post for it
            print("  (instagram comment skipped:", e, ")")
    return mid


def post_facebook(item, url):
    s = api("POST", f"{PAGE_ID}/video_reels", upload_phase="start")
    vid, up = s["video_id"], s["upload_url"]
    _upload(up, item["file"])
    api("POST", f"{PAGE_ID}/video_reels", upload_phase="finish", video_id=vid, video_state="PUBLISHED", description=item["caption"])
    if item.get("first_comment"):
        for _ in range(12):
            try:
                api("POST", f"{vid}/comments", message=item["first_comment"])
                break
            except Exception as e:
                last = e
                time.sleep(15)
        else:
            print("  (facebook comment skipped:", last, ")")
    return vid


def ready(plat):
    if plat == "tiktok":
        return tiktok.configured()
    return bool(TOKEN and PAGE_ID)


POSTERS = {"instagram": lambda item, url: post_instagram(item, url),
           "facebook": lambda item, url: post_facebook(item, url),
           "tiktok": lambda item, url: tiktok.post(item)}


def load(name, default):
    p = os.path.join(HERE, name)
    return json.load(open(p)) if os.path.exists(p) else default


def save_state(state):
    json.dump(state, open(os.path.join(HERE, "posted.json"), "w"), indent=2)


def due(item, now):
    return datetime.fromisoformat(item["post_at"]) <= now


def post_item(item, state, dry=False):
    url = item["file"]
    st = state.setdefault(item["id"], {})
    for plat in item.get("platforms", ["instagram", "facebook"]):
        rec = st.setdefault(plat, {})
        if rec.get("id") or rec.get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        if not ready(plat):
            print(f"-> {item['id']} to {plat}: skipped, {plat} is not set up yet")
            continue
        print(f"-> {item['id']} to {plat}  ({url}, {os.path.getsize(os.path.join(HERE, url)) // 1024} KB)")
        if dry:
            continue
        try:
            rec["id"] = POSTERS[plat](item, url)
            rec["posted_at"] = datetime.now(timezone.utc).isoformat()
            rec.pop("error", None)
            print(f"   posted: {rec['id']}")
        except Exception as e:
            rec["attempts"] = rec.get("attempts", 0) + 1
            rec["error"] = str(e)[:500]
            print(f"   FAILED (attempt {rec['attempts']}/{MAX_ATTEMPTS}): {e}")
        save_state(state)


def finished(item, state):
    st = state.get(item["id"], {})
    # A platform that isn't set up yet doesn't hold up the queue.
    return all(st.get(p, {}).get("id") or st.get(p, {}).get("attempts", 0) >= MAX_ATTEMPTS or not ready(p)
               for p in item.get("platforms", ["instagram", "facebook"]))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "tiktok-link":
        if not tiktok.KEY:
            sys.exit("Add the TIKTOK_CLIENT_KEY secret first. See README.md, TikTok step 3.")
        print("Open this link, log in to the TikTok account you want to post to, and tap Authorize:")
        print(tiktok.login_link())
        print("TikTok then sends you to a GitHub page. Copy that whole page address and run mode tiktok-code with it.")
        return
    if mode == "tiktok-code":
        oid = tiktok.save_code(sys.argv[2])
        print(f"TikTok login saved for account {str(oid)[:6]}... It's stored encrypted in tiktok_token.enc.")
        return
    if mode == "tiktok-check":
        at = tiktok.access_token()
        print("TikTok login OK. Posting mode:", tiktok.MODE)
        try:
            info = tiktok.creator(at)
            print("Account: @" + str(info.get("creator_username")), "| allowed privacy:", info.get("privacy_level_options"))
        except Exception as e:
            print("(account details not available:", e, ")")
        return
    if not (TOKEN and PAGE_ID) and not tiktok.configured():
        sys.exit("No accounts are set up yet. Add the Meta secrets (README step 3) and/or TikTok (README, TikTok section).")
    queue, state = load("queue.json", []), load("posted.json", {})
    if os.path.exists(os.path.join(HERE, "PAUSED")) and mode in ("run", "dry-run"):
        print("Posting is paused (delete the PAUSED file to resume).")
        return
    if mode == "check":
        if not (TOKEN and PAGE_ID):
            sys.exit("Missing META_PAGE_TOKEN or FB_PAGE_ID secret. See README.md, step 3.")
        igid, user, page = ig_user_id()
        dbg = api("GET", "debug_token", input_token=TOKEN).get("data", {})
        print(f"OK. Page: {page} ({PAGE_ID})  Instagram: @{user} ({igid})")
        print("Token type:", dbg.get("type"), "| expires:", dbg.get("expires_at") or "never", "| scopes:", ", ".join(dbg.get("scopes", [])))
        nxt = next((q for q in queue if not finished(q, state)), None)
        print("Next in queue:", nxt and f"{nxt['id']} at {nxt['post_at']}")
        return
    if mode == "now":
        item = next(q for q in queue if q["id"] == sys.argv[2])
        post_item(item, state)
        return
    now = datetime.now(timezone.utc)
    for item in queue:
        if finished(item, state):
            continue
        if not due(item, now):
            print(f"Nothing due. Next: {item['id']} at {item['post_at']}")
            return
        post_item(item, state, dry=(mode == "dry-run"))
        return  # at most one video per run, so a late start never floods your feed
    print("Queue finished. Add more videos to queue.json.")


if __name__ == "__main__":
    main()
