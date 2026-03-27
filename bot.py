import os
import asyncio
import re
import json
import tweepy
from pyrogram import Client, filters
from pyrogram.types import Message
import httpx

# ── ENV VARS WITH VALIDATION ──────────────────────────────
def get_env(key, cast=str):
    val = os.environ.get(key)
    if val is None:
        raise EnvironmentError(f"❌ Missing environment variable: {key}")
    try:
        return cast(val.strip())
    except Exception:
        raise EnvironmentError(f"❌ Invalid value for {key}: '{val}'")

try:
    API_ID         = get_env("API_ID", int)
    API_HASH       = get_env("API_HASH")
    BOT_TOKEN      = get_env("BOT_TOKEN")
    TWITTER_BEARER = get_env("TWITTER_BEARER_TOKEN")
    ALERT_CHAT_ID  = get_env("ALERT_CHAT_ID", int)
    print("✅ All environment variables loaded successfully")
    print(f"   API_ID: {API_ID}")
    print(f"   API_HASH: {API_HASH[:6]}...")
    print(f"   BOT_TOKEN: {BOT_TOKEN[:10]}...")
    print(f"   ALERT_CHAT_ID: {ALERT_CHAT_ID}")
except EnvironmentError as e:
    print(str(e))
    print("\n📋 Available environment variables:")
    for k, v in os.environ.items():
        if any(x in k.upper() for x in ["API", "BOT", "TWITTER", "ALERT", "CHAT"]):
            print(f"   {k} = {v[:10]}...")
    exit(1)

# ── STORAGE ───────────────────────────────────────────────
TRACKED_FILE = "tracked_accounts.json"

def load_tracked():
    if os.path.exists(TRACKED_FILE):
        with open(TRACKED_FILE) as f:
            return json.load(f)
    return []

def save_tracked(accounts):
    with open(TRACKED_FILE, "w") as f:
        json.dump(accounts, f)

tracked_accounts = load_tracked()

# ── SOLANA CA DETECTION ───────────────────────────────────
def extract_solana_ca(text):
    pattern = r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b'
    matches = re.findall(pattern, text)
    return [m for m in matches if len(m) >= 40]

# ── GET TOKEN INFO FROM DEXSCREENER ──────────────────────
async def get_token_info(ca: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}", timeout=10)
            data = r.json()
            pairs = data.get("pairs", [])
            if pairs:
                p = pairs[0]
                return {
                    "name": p.get("baseToken", {}).get("name", "Unknown"),
                    "symbol": p.get("baseToken", {}).get("symbol", "???"),
                    "price": p.get("priceUsd", "N/A"),
                    "liquidity": p.get("liquidity", {}).get("usd", 0),
                    "market_cap": p.get("marketCap", "N/A"),
                    "dex": p.get("dexId", "N/A"),
                    "url": p.get("url", "")
                }
    except:
        pass
    return None

# ── PYROGRAM BOT ──────────────────────────────────────────
app = Client("sniper_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    await message.reply(
        "🎯 **Solana CA Sniper Bot**\n\n"
        "I monitor X (Twitter) accounts and alert you the moment they post a Solana CA!\n\n"
        "**Commands:**\n"
        "`/add @username` — Track an X account\n"
        "`/remove @username` — Stop tracking\n"
        "`/list` — Show tracked accounts\n"
        "`/status` — Bot status"
    )

@app.on_message(filters.command("add"))
async def add_account(client, message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Usage: `/add @username`", parse_mode="markdown")
        return

    username = parts[1].lstrip("@").lower()

    try:
        tw_client = tweepy.Client(bearer_token=TWITTER_BEARER)
        user = tw_client.get_user(username=username)
        if not user.data:
            await message.reply(f"❌ X account `@{username}` not found.")
            return
    except Exception as e:
        await message.reply(f"❌ Could not verify account: {str(e)[:100]}")
        return

    if username not in tracked_accounts:
        tracked_accounts.append(username)
        save_tracked(tracked_accounts)
        await message.reply(f"✅ Now tracking **@{username}** on X!")
    else:
        await message.reply(f"⚠️ Already tracking **@{username}**")

@app.on_message(filters.command("remove"))
async def remove_account(client, message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Usage: `/remove @username`")
        return

    username = parts[1].lstrip("@").lower()
    if username in tracked_accounts:
        tracked_accounts.remove(username)
        save_tracked(tracked_accounts)
        await message.reply(f"🗑️ Stopped tracking **@{username}**")
    else:
        await message.reply(f"❌ `@{username}` was not in your list.")

@app.on_message(filters.command("list"))
async def list_accounts(client, message: Message):
    if not tracked_accounts:
        await message.reply("📭 No accounts being tracked yet.\nUse `/add @username` to add one.")
        return
    accs = "\n".join([f"• @{a}" for a in tracked_accounts])
    await message.reply(f"👀 **Tracked X Accounts:**\n\n{accs}")

@app.on_message(filters.command("status"))
async def status(client, message: Message):
    await message.reply(
        f"✅ Bot is running\n"
        f"👀 Tracking **{len(tracked_accounts)}** X accounts\n"
        f"🔄 Polling every 30 seconds"
    )

# ── TWITTER POLLING LOOP ──────────────────────────────────
seen_tweet_ids = set()

async def poll_twitter():
    tw_client = tweepy.Client(bearer_token=TWITTER_BEARER)
    print("🔄 Twitter polling started...")

    while True:
        for username in list(tracked_accounts):
            try:
                user = tw_client.get_user(username=username)
                if not user.data:
                    continue

                tweets = tw_client.get_users_tweets(
                    id=user.data.id,
                    max_results=5,
                    tweet_fields=["created_at", "text"]
                )

                if not tweets.data:
                    continue

                for tweet in tweets.data:
                    if tweet.id in seen_tweet_ids:
                        continue

                    seen_tweet_ids.add(tweet.id)
                    cas = extract_solana_ca(tweet.text)

                    if cas:
                        for ca in cas:
                            token_info = await get_token_info(ca)
                            tweet_url = f"https://x.com/{username}/status/{tweet.id}"

                            if token_info:
                                msg = (
                                    f"🚨 **CA DETECTED!**\n\n"
                                    f"👤 Posted by: [@{username}]({tweet_url})\n\n"
                                    f"🪙 **{token_info['name']} (${token_info['symbol']})**\n"
                                    f"💰 Price: `${token_info['price']}`\n"
                                    f"💧 Liquidity: `${token_info['liquidity']:,}`\n"
                                    f"📊 Market Cap: `${token_info['market_cap']}`\n"
                                    f"🔁 DEX: `{token_info['dex']}`\n\n"
                                    f"📋 **CA:**\n`{ca}`\n\n"
                                    f"🔗 [DexScreener]({token_info['url']}) | "
                                    f"[Pump.fun](https://pump.fun/{ca}) | "
                                    f"[Birdeye](https://birdeye.so/token/{ca})"
                                )
                            else:
                                msg = (
                                    f"🚨 **CA DETECTED!**\n\n"
                                    f"👤 Posted by: [@{username}]({tweet_url})\n\n"
                                    f"📋 **CA:**\n`{ca}`\n\n"
                                    f"🔗 [Pump.fun](https://pump.fun/{ca}) | "
                                    f"[Birdeye](https://birdeye.so/token/{ca}) | "
                                    f"[DexScreener](https://dexscreener.com/solana/{ca})"
                                )

                            await app.send_message(ALERT_CHAT_ID, msg, disable_web_page_preview=True)
                            print(f"✅ Sent alert for CA: {ca} from @{username}")

            except Exception as e:
                print(f"⚠️ Error polling @{username}: {e}")

        await asyncio.sleep(30)

# ── MAIN ──────────────────────────────────────────────────
async def main():
    await app.start()
    print("🤖 Bot started!")
    await poll_twitter()

if __name__ == "__main__":
    asyncio.run(main())
