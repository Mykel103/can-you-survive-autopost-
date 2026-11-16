"""Free auto-poster: posts the next due video from queue.json as an Instagram Reel and a Facebook Page Reel.

Runs inside GitHub Actions (see .github/workflows/autopost.yml). Uses only Meta's official Graph API.

Secrets needed (repo Settings -> Secrets and variables -> Actions):
  META_PAGE_TOKEN  long-lived Facebook PAGE access token (never expires when made from a long-lived user token)
  FB_PAGE_ID       numeric id of your Facebook Page

Modes:
  python post.py check     verify the token and show which Page + Instagram account it can post to
  python post.py dry-run   show what would be posted next, post nothing
  python post.py run       post the next due video (default)
  python post.py now ID    post one queue item immediately, ignoring its time (for a test)
"""
import json, os, sys, time
from datetime import datetime, timezone
import requests

GV = os.environ.get("GRAPH_VERSION", "v25.0")
G = f"https://graph.facebook.com/{GV}"
TOKEN = os.environ.get("META_PAGE_TOKEN", "")
PAGE_ID = os.environ.get("FB_PAGE_ID", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
MEDIA_BASE = os.environ.get("MEDIA_BASE_URL") or f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
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


def post_instagram(item, url):
    igid, _, _ = ig_user_id()
    c = api("POST", f"{igid}/media", media_type="REELS", video_url=url, caption=item["caption"], share_to_feed="true")
    cid = c["id"]
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
    r = requests.post(up, headers={"Authorization": f"OAuth {TOKEN}", "file_url": url}, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"facebook upload -> {r.status_code}: {r.text[:400]}")
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


def load(name, default):
    p = os.path.join(HERE, name)
    return json.load(open(p)) if os.path.exists(p) else default


def save_state(state):
    json.dump(state, open(os.path.join(HERE, "posted.json"), "w"), indent=2)


def due(item, now):
    return datetime.fromisoformat(item["post_at"]) <= now


def post_item(item, state, dry=False):
    url = MEDIA_BASE + item["file"]
    st = state.setdefault(item["id"], {})
    for plat in item.get("platforms", ["instagram", "facebook"]):
        rec = st.setdefault(plat, {})
        if rec.get("id") or rec.get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        print(f"-> {item['id']} to {plat}  ({url})")
        if dry:
            continue
        try:
            rec["id"] = (post_instagram if plat == "instagram" else post_facebook)(item, url)
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
    return all(st.get(p, {}).get("id") or st.get(p, {}).get("attempts", 0) >= MAX_ATTEMPTS for p in item.get("platforms", ["instagram", "facebook"]))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if not TOKEN or not PAGE_ID:
        sys.exit("Missing META_PAGE_TOKEN or FB_PAGE_ID secret. See README.md, step 3.")
    queue, state = load("queue.json", []), load("posted.json", {})
    if mode == "check":
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
