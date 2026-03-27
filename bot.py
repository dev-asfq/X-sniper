import os
import threading
import re
import json
import time
import httpx
import telebot

# ── ENV VARS ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise EnvironmentError("❌ Missing BOT_TOKEN environment variable")

print("✅ Environment variables loaded!")

# ── BOT SETUP ─────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN)

# ── STORAGE ───────────────────────────────────────────────
TRACKED_FILE = "tracked_accounts.json"
USERS_FILE   = "users.json"

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

tracked_accounts = load_json(TRACKED_FILE, {})
subscribed_users = set(load_json(USERS_FILE, []))
seen_tweet_ids   = set()

# ── SOLANA CA DETECTION ───────────────────────────────────
def extract_solana_ca(text):
    pattern = r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b'
    matches = re.findall(pattern, text)
    return [m for m in matches if len(m) >= 40]

# ── FETCH TWEETS VIA TWITTER SYNDICATION API (no key needed)
def fetch_tweets(username: str):
    """
    Uses Twitter's internal syndication endpoint —
    the same one that powers embedded tweets on websites.
    No API key required.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }

        # Step 1: get user ID from username
        url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
        r = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)

        if r.status_code != 200:
            print(f"⚠️ Twitter syndication returned {r.status_code} for @{username}")
            return []

        data = r.json()
        timeline = data.get("props", {}).get("pageProps", {}).get("timeline", {})
        entries = timeline.get("entries", [])

        tweets = []
        for entry in entries:
            content = entry.get("content", {})
            tweet   = content.get("tweet", {})
            if not tweet:
                continue

            tweet_id   = tweet.get("id_str", "")
            full_text  = tweet.get("full_text") or tweet.get("text", "")
            tweet_link = f"https://x.com/{username}/status/{tweet_id}"

            if tweet_id:
                tweets.append({
                    "id":   tweet_id,
                    "text": full_text,
                    "link": tweet_link
                })

        return tweets

    except Exception as e:
        print(f"⚠️ Error fetching tweets for @{username}: {e}")
        return []

# ── GET TOKEN INFO FROM DEXSCREENER ──────────────────────
def get_token_info(ca: str):
    try:
        r = httpx.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{ca}",
            timeout=10
        )
        data = r.json()
        pairs = data.get("pairs", [])
        if pairs:
            p = pairs[0]
            return {
                "name":       p.get("baseToken", {}).get("name", "Unknown"),
                "symbol":     p.get("baseToken", {}).get("symbol", "???"),
                "price":      p.get("priceUsd", "N/A"),
                "liquidity":  p.get("liquidity", {}).get("usd", 0),
                "market_cap": p.get("marketCap", "N/A"),
                "dex":        p.get("dexId", "N/A"),
                "url":        p.get("url", "")
            }
    except:
        pass
    return None

def is_admin(message):
    return message.from_user.id == ADMIN_ID

# ── BROADCAST TO ALL SUBSCRIBERS ─────────────────────────
def broadcast_alert(msg: str):
    failed = []
    for uid in list(subscribed_users):
        try:
            bot.send_message(uid, msg, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            print(f"⚠️ Failed to send to {uid}: {e}")
            failed.append(uid)
    for uid in failed:
        subscribed_users.discard(uid)
    if failed:
        save_json(USERS_FILE, list(subscribed_users))

# ── USER COMMANDS ─────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start(message):
    user_id  = message.from_user.id
    name     = message.from_user.first_name or "there"
    subscribed_users.add(user_id)
    save_json(USERS_FILE, list(subscribed_users))
    bot.reply_to(message, (
        f"👋 Hey *{name}*! Welcome to the Solana CA Sniper Bot!\n\n"
        f"🎯 I monitor X accounts and alert you the moment they post a Solana CA!\n\n"
        f"✅ You're now *subscribed* to all alerts!\n\n"
        f"*Commands:*\n"
        f"`/stop` — Unsubscribe from alerts\n"
        f"`/list` — See tracked X accounts\n"
        f"`/status` — Bot stats"
    ), parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop(message):
    user_id = message.from_user.id
    if user_id in subscribed_users:
        subscribed_users.discard(user_id)
        save_json(USERS_FILE, list(subscribed_users))
        bot.reply_to(message, "🔕 Unsubscribed! Send /start anytime to re-subscribe.")
    else:
        bot.reply_to(message, "You're not subscribed. Send /start to subscribe.")

@bot.message_handler(commands=['list'])
def list_accounts(message):
    if not tracked_accounts:
        bot.reply_to(message, "📭 No X accounts being tracked yet.")
        return
    accs = "\n".join([f"• @{a}" for a in tracked_accounts])
    bot.reply_to(message, f"👀 *Tracked X Accounts:*\n\n{accs}", parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status(message):
    bot.reply_to(message, (
        f"✅ *Bot Status*\n\n"
        f"👀 Tracking: *{len(tracked_accounts)}* X accounts\n"
        f"👥 Subscribers: *{len(subscribed_users)}* users\n"
        f"🔄 Polling every 60 seconds"
    ), parse_mode="Markdown")

# ── ADMIN COMMANDS ────────────────────────────────────────
@bot.message_handler(commands=['add'])
def add_account(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ Only the bot admin can add tracked accounts.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/add @username`", parse_mode="Markdown")
        return
    username = parts[1].lstrip("@").lower()
    if username not in tracked_accounts:
        tracked_accounts[username] = None
        save_json(TRACKED_FILE, tracked_accounts)
        bot.reply_to(message, f"✅ Now tracking *@{username}*!", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"⚠️ Already tracking *@{username}*", parse_mode="Markdown")

@bot.message_handler(commands=['remove'])
def remove_account(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ Only the bot admin can remove tracked accounts.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/remove @username`")
        return
    username = parts[1].lstrip("@").lower()
    if username in tracked_accounts:
        del tracked_accounts[username]
        save_json(TRACKED_FILE, tracked_accounts)
        bot.reply_to(message, f"🗑️ Stopped tracking *@{username}*", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ `@{username}` not found in list.", parse_mode="Markdown")

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ Admin only.")
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        bot.reply_to(message, "Usage: `/broadcast Your message`", parse_mode="Markdown")
        return
    sent = 0
    for uid in list(subscribed_users):
        try:
            bot.send_message(uid, f"📢 *Announcement:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            pass
    bot.reply_to(message, f"✅ Sent to {sent} users.")

@bot.message_handler(commands=['users'])
def show_users(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ Admin only.")
        return
    bot.reply_to(message, f"👥 Total subscribers: *{len(subscribed_users)}*", parse_mode="Markdown")

# ── POLLING LOOP ──────────────────────────────────────────
def poll_loop():
    print("🔄 Polling started via Twitter Syndication API...")
    while True:
        for username in list(tracked_accounts.keys()):
            try:
                tweets = fetch_tweets(username)
                if not tweets:
                    continue

                for tweet in tweets:
                    tweet_id = tweet["id"]
                    if tweet_id in seen_tweet_ids:
                        continue

                    seen_tweet_ids.add(tweet_id)
                    cas = extract_solana_ca(tweet["text"])

                    if cas:
                        for ca in cas:
                            token_info = get_token_info(ca)

                            if token_info:
                                msg = (
                                    f"🚨 *CA DETECTED!*\n\n"
                                    f"👤 Posted by: [@{username}]({tweet['link']})\n\n"
                                    f"🪙 *{token_info['name']} (${token_info['symbol']})*\n"
                                    f"💰 Price: `${token_info['price']}`\n"
                                    f"💧 Liquidity: `${token_info['liquidity']:,}`\n"
                                    f"📊 Market Cap: `${token_info['market_cap']}`\n"
                                    f"🔁 DEX: `{token_info['dex']}`\n\n"
                                    f"📋 *CA:*\n`{ca}`\n\n"
                                    f"🔗 [DexScreener]({token_info['url']}) | "
                                    f"[Pump.fun](https://pump.fun/{ca}) | "
                                    f"[Birdeye](https://birdeye.so/token/{ca})"
                                )
                            else:
                                msg = (
                                    f"🚨 *CA DETECTED!*\n\n"
                                    f"👤 Posted by: [@{username}]({tweet['link']})\n\n"
                                    f"📋 *CA:*\n`{ca}`\n\n"
                                    f"🔗 [Pump.fun](https://pump.fun/{ca}) | "
                                    f"[Birdeye](https://birdeye.so/token/{ca}) | "
                                    f"[DexScreener](https://dexscreener.com/solana/{ca})"
                                )

                            broadcast_alert(msg)
                            print(f"✅ Alert broadcasted: {ca} from @{username}")

            except Exception as e:
                print(f"⚠️ Error polling @{username}: {e}")

        time.sleep(60)

# ── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🤖 Bot started! Admin ID: {ADMIN_ID}")
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    bot.infinity_polling()
