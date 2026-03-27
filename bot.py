import os
import threading
import re
import json
import time
import httpx
import telebot
from xml.etree import ElementTree

# ── ENV VARS ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))  # Your Telegram ID - only you can add/remove accounts

if not BOT_TOKEN:
    raise EnvironmentError("❌ Missing BOT_TOKEN environment variable")

print("✅ Environment variables loaded!")

# ── BOT SETUP ─────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN)

# ── NITTER INSTANCES ──────────────────────────────────────
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

# ── STORAGE ───────────────────────────────────────────────
TRACKED_FILE  = "tracked_accounts.json"
USERS_FILE    = "users.json"

def load_tracked():
    if os.path.exists(TRACKED_FILE):
        with open(TRACKED_FILE) as f:
            return json.load(f)
    return {}

def save_tracked(data):
    with open(TRACKED_FILE, "w") as f:
        json.dump(data, f)

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return set(json.load(f))
    return set()

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(list(users), f)

tracked_accounts = load_tracked()
subscribed_users = load_users()  # All users who will receive alerts
seen_tweet_ids   = set()

# ── SOLANA CA DETECTION ───────────────────────────────────
def extract_solana_ca(text):
    pattern = r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b'
    matches = re.findall(pattern, text)
    return [m for m in matches if len(m) >= 40]

# ── FETCH TWEETS VIA NITTER RSS ───────────────────────────
def fetch_tweets(username: str):
    headers = {"User-Agent": "Mozilla/5.0 (RSS Reader)"}
    for instance in NITTER_INSTANCES:
        try:
            url = f"{instance}/{username}/rss"
            r = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                root = ElementTree.fromstring(r.text)
                channel = root.find("channel")
                if channel is None:
                    continue
                tweets = []
                for item in channel.findall("item"):
                    title = item.findtext("title", "")
                    link  = item.findtext("link", "")
                    desc  = item.findtext("description", "")
                    guid  = item.findtext("guid", link)
                    full_text = re.sub(r'<[^>]+>', ' ', f"{title} {desc}")
                    tweets.append({"id": guid, "text": full_text, "link": link})
                return tweets
        except Exception as e:
            print(f"⚠️ Nitter {instance} failed: {e}")
    return []

# ── GET TOKEN INFO FROM DEXSCREENER ──────────────────────
def get_token_info(ca: str):
    try:
        r = httpx.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}", timeout=10)
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

# ── USER COMMANDS ─────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name or "there"

    # Auto-subscribe user
    subscribed_users.add(user_id)
    save_users(subscribed_users)

    bot.reply_to(message, (
        f"👋 Hey *{username}*! Welcome to the Solana CA Sniper Bot!\n\n"
        f"🎯 I monitor X accounts and alert you the moment they post a Solana contract address.\n\n"
        f"You're now *subscribed* to all alerts!\n\n"
        f"*Commands:*\n"
        f"`/stop` — Unsubscribe from alerts\n"
        f"`/list` — See tracked X accounts\n"
        f"`/status` — Bot status\n\n"
        f"_Alerts will be sent automatically when a CA is detected!_ 🚀"
    ), parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop(message):
    user_id = message.from_user.id
    if user_id in subscribed_users:
        subscribed_users.discard(user_id)
        save_users(subscribed_users)
        bot.reply_to(message, "🔕 You've unsubscribed from alerts.\nSend /start anytime to re-subscribe.")
    else:
        bot.reply_to(message, "You're not subscribed. Send /start to subscribe.")

@bot.message_handler(commands=['list'])
def list_accounts(message):
    if not tracked_accounts:
        bot.reply_to(message, "📭 No X accounts are being tracked yet.")
        return
    accs = "\n".join([f"• @{a}" for a in tracked_accounts])
    bot.reply_to(message, f"👀 *Currently Tracked X Accounts:*\n\n{accs}", parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status(message):
    bot.reply_to(message, (
        f"✅ *Bot Status*\n\n"
        f"👀 Tracking: *{len(tracked_accounts)}* X accounts\n"
        f"👥 Subscribers: *{len(subscribed_users)}* users\n"
        f"🔄 Polling every 60 seconds"
    ), parse_mode="Markdown")

# ── ADMIN-ONLY COMMANDS ───────────────────────────────────
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
        save_tracked(tracked_accounts)
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
        save_tracked(tracked_accounts)
        bot.reply_to(message, f"🗑️ Stopped tracking *@{username}*", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"❌ `@{username}` was not in the list.", parse_mode="Markdown")

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ Admin only.")
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        bot.reply_to(message, "Usage: `/broadcast Your message here`", parse_mode="Markdown")
        return
    sent = 0
    for uid in list(subscribed_users):
        try:
            bot.send_message(uid, f"📢 *Announcement:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            pass
    bot.reply_to(message, f"✅ Broadcast sent to {sent} users.")

@bot.message_handler(commands=['users'])
def show_users(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ Admin only.")
        return
    bot.reply_to(message, f"👥 Total subscribers: *{len(subscribed_users)}*", parse_mode="Markdown")

# ── BROADCAST ALERT TO ALL SUBSCRIBERS ───────────────────
def broadcast_alert(msg: str):
    failed = []
    for uid in list(subscribed_users):
        try:
            bot.send_message(uid, msg, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            print(f"⚠️ Failed to send to {uid}: {e}")
            failed.append(uid)
    # Remove users who blocked the bot
    for uid in failed:
        subscribed_users.discard(uid)
    if failed:
        save_users(subscribed_users)

# ── POLLING LOOP ──────────────────────────────────────────
def poll_loop():
    print("🔄 Polling started via Nitter RSS...")
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
                            print(f"✅ Alert broadcasted for CA: {ca} from @{username}")

            except Exception as e:
                print(f"⚠️ Error polling @{username}: {e}")

        time.sleep(60)

# ── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🤖 Bot started! Admin ID: {ADMIN_ID}")
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    bot.infinity_polling()
