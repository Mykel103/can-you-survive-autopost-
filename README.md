# Can You Survive: free auto-poster

This repo posts the channel's videos to **Instagram Reels**, a **Facebook Page** and **TikTok** on a schedule, for free.

- **GitHub Actions** runs `post.py` every hour. That's free for public repos.
- `post.py` checks `queue.json`. When a video's `post_at` time has passed, it posts that video through Meta's official Graph API.
- It also posts the first comment, then records the result in `posted.json`.
- It posts at most one video per run, so a late start never floods your feed.

## Schedule

All 18 videos are in `queue.json`. One series goes out per day, at 12 PM, 5 PM and 9 PM Eastern:

| Day | Series |
| --- | --- |
| Tue Nov 17 | Half the Moon |
| Wed Nov 18 | Alien Invasion |
| Thu Nov 19 | Zombies Are Real |
| Fri Nov 20 | Vampire in Town |
| Sat Nov 21 | The Sun Goes Out |
| Sun Nov 22 | Chicago Dice Game (Instagram, Facebook **and TikTok**) |

Each video lists its accounts in `platforms` (`instagram`, `facebook`, `tiktok`). An account that isn't set up yet is skipped, so the rest still post.

To change a time, edit `post_at` in `queue.json`. To add a video, put the MP4 in `videos/` and add an entry to `queue.json`.

## One-time setup (about 20 minutes)

### 1. Instagram and Facebook Page
- Switch Instagram to a **Creator** or **Business** account (Instagram → Settings → Account type and tools).
- Link it to your **Facebook Page** (Facebook Page → Settings → Linked accounts → Instagram).

### 2. Free Meta developer app
1. Go to developers.facebook.com, log in with your Facebook account, and click **My Apps → Create App**.
2. Pick the option for managing a business / Pages and Instagram. Give it any name, like "Can You Survive Poster".
3. Leave it in **Development** mode. You're the app's admin, so it can post to your own Page and Instagram without Meta's review.

### 3. Get the token (it never expires)
1. Open the **Graph API Explorer**: developers.facebook.com/tools/explorer
2. Pick your app at the top right, then choose **User Token**.
3. Add these permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, `business_management`
4. Click **Generate Access Token** and approve your Page and Instagram account.
5. Open the **Access Token Debugger** (developers.facebook.com/tools/debug/accesstoken), paste that token, and click **Extend Access Token**. Copy the new long-lived token.
6. Back in the Explorer, paste the long-lived token and run `GET me/accounts`. Find your Page and copy its `access_token` and its `id`. A Page token made this way does not expire.

### 4. Add two GitHub secrets
In this repo, go to **Settings → Secrets and variables → Actions → New repository secret**:
- `META_PAGE_TOKEN` = the Page `access_token` from step 3.6
- `FB_PAGE_ID` = the Page `id` from step 3.6

Never paste the token anywhere else: not in chat, not in a file, not in a commit.

### 5. Test it
**Actions** tab → **autopost** → **Run workflow** → mode **check**.
It should print your Page name and your `@instagram` handle.

To make a real test post, run mode **now** with item `HalfTheMoon_Part1`.

## If something fails
Open the run in the **Actions** tab. The error from Meta is printed there, and it's also saved in `posted.json`. Each video gets 3 tries, then the poster moves on.

## TikTok setup (about 15 minutes, free)

**What to expect:** TikTok only lets apps post publicly by themselves after TikTok reviews the app, which takes a few weeks. Until then the poster uses **inbox mode**: at post time the video lands in your TikTok inbox (you get a notification), and you add the caption and tap **Post**. Once TikTok approves your app, it posts by itself.

1. **Make the app.** Go to developers.tiktok.com, log in with the TikTok account, and choose **Manage apps → Connect an app**. Name it anything (for example "CYS Poster") and pick **Web** as the platform.
2. **Add products.** Add **Login Kit** and **Content Posting API**. In Login Kit, set the redirect URI to `https://github.com/Mykel103/can-you-survive-autopost-`. In Content Posting API, turn on **Direct Post** (needed later). Scopes: `user.info.basic`, `video.upload`, `video.publish`. Use the **Sandbox** tab and add your TikTok account as a target user, so you can use it right away without waiting for review.
3. **Add three GitHub secrets** (repo **Settings → Secrets and variables → Actions → New repository secret**):
   - `TIKTOK_CLIENT_KEY`: the Client key from your app page
   - `TIKTOK_CLIENT_SECRET`: the Client secret from the same page
   - `TIKTOK_VAULT_KEY`: any long password you make up. It locks your TikTok login so nobody can read it from the public repo.
4. **Log in once.** Go to **Actions → autopost → Run workflow**, choose mode `tiktok-link` and run it. Open the run's log and tap the link, then log in and tap **Authorize**. TikTok sends you to this repo's GitHub page. Copy that whole page address from the address bar.
5. **Save the login.** Run the workflow again with mode `tiktok-code`, and paste that address into the **item** box. It's safe: the code inside it works once and expires within minutes.
6. **Test.** Run mode `tiktok-check`. It should say "TikTok login OK".

**After TikTok approves your app:** in the app page, submit it for review under Content Posting API. Once approved, add a repo **variable** (Settings → Secrets and variables → Actions → **Variables**) named `TIKTOK_MODE` with the value `direct`. From then on, videos post publicly by themselves, with the caption from `queue.json`.
