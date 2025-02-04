import os
import re
import time
import feedparser
import requests
import hashlib
import redis
import threading
from flask import Flask
from newspaper import Article
from telegram import Bot
from telegram.constants import ParseMode
from datetime import datetime

# Initialize services
app = Flask(__name__)

# Redis connection with SSL
r = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    password=os.getenv("REDIS_PASSWORD"),
    ssl=True,
    ssl_cert_reqs=None,
    decode_responses=True
)

# Telegram bot
bot = Bot(token=os.getenv("BOT_TOKEN"))

# Verified working RSS feeds
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://cryptopanic.com/news/rss/",
    "https://beincrypto.com/feed/",
    "https://coinjournal.net/feed/"
]

# Self-pinging to keep Render alive
def start_pinger():
    def ping():
        while True:
            try:
                requests.get(f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com/keepalive")
                print(f"[{datetime.now()}] Ping successful")
            except Exception as e:
                print(f"[{datetime.now()}] Ping failed: {str(e)}")
            time.sleep(840)  # 14 minutes

    threading.Thread(target=ping, daemon=True).start()

start_pinger()

# Fetch crypto prices
def get_crypto_prices():
    try:
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")
        data = response.json()
        return (
            f"BTC: ${data['bitcoin']['usd']} ({data['bitcoin']['usd_24h_change']:.1f}%)\n"
            f"ETH: ${data['ethereum']['usd']} ({data['ethereum']['usd_24h_change']:.1f}%)"
        )
    except Exception as e:
        print(f"[{datetime.now()}] Price fetch error: {str(e)}")
        return "BTC/ETH: Price data unavailable"

# Fetch article content
def fetch_article(url):
    try:
        article = Article(
            url,
            browser_user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            request_timeout=20,
            keep_article_html=True
        )
        article.download()
        article.parse()
        return article.text, article.top_image
    except Exception as e:
        print(f"[{datetime.now()}] Article fetch error: {str(e)}")
        return "", ""

# Check if article is unique
def is_unique(content):
    content_hash = hashlib.md5(content[:500].encode()).hexdigest()
    return not r.exists(content_hash)

# Summarize article content
def summarize(text):
    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/facebook/bart-large-cnn",
            headers={"Authorization": f"Bearer {os.getenv('HF_API_KEY')}"},
            json={"inputs": text[:1024], "parameters": {"max_length": 150}},
            timeout=30
        )
        return response.json()[0]['summary_text']
    except Exception as e:
        print(f"[{datetime.now()}] Summarization failed: {str(e)}")
        sentences = re.split(r'(?<=[.!?]) +', text)
        return ' '.join(sentences[:3]) if len(sentences) > 3 else text

# Post to Telegram
def post_to_telegram(title, url, summary, image):
    try:
        prices = get_crypto_prices()
        message = (
            f"🔥 *{escape_markdown(title)}* 🔥\n\n"
            f"{escape_markdown(summary)}\n\n"
            f"📊 *Market Update*:\n{prices}\n\n"
            f"[Read More]({url}) | #Crypto #Bitcoin #Altcoins"
        )
        if image:
            bot.send_photo(
                chat_id=os.getenv("CHAT_ID"),
                photo=image,
                caption=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            bot.send_message(
                chat_id=os.getenv("CHAT_ID"),
                text=message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        print(f"[{datetime.now()}] Posted: {title}")
    except Exception as e:
        print(f"[{datetime.now()}] Posting failed: {str(e)}")

# Escape markdown characters
def escape_markdown(text):
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

# Process RSS feeds
def process_feeds():
    while True:
        print(f"\n[{datetime.now()}] Starting feed check cycle")
        for feed_url in RSS_FEEDS:
            try:
                print(f"[{datetime.now()}] Fetching feed: {feed_url}")
                feed = feedparser.parse(feed_url)
                
                if not feed.entries:
                    print(f"[{datetime.now()}] No entries in feed: {feed_url}")
                    continue
                
                print(f"[{datetime.now()}] Found {len(feed.entries)} entries")
                
                for entry in feed.entries[:5]:  # Latest 5
                    print(f"[{datetime.now()}] Processing: {entry.title}")
                    content, image = fetch_article(entry.link)
                    
                    if not content:
                        print(f"[{datetime.now()}] Skipping - no content")
                        continue
                    
                    if is_unique(content):
                        print(f"[{datetime.now()}] New unique article found")
                        summary = summarize(content)
                        post_to_telegram(entry.title, entry.link, summary, image)
                        r.set(
                            hashlib.md5(content[:500].encode()).hexdigest(),
                            "1",
                            ex=604800  # 7 days
                        )
                    else:
                        print(f"[{datetime.now()}] Duplicate article - skipping")
                        
            except Exception as e:
                print(f"[{datetime.now()}] Feed processing error: {str(e)}")
                
        print(f"[{datetime.now()}] Completed feed cycle. Sleeping...")
        time.sleep(900)  # 15 minutes between checks

# Test endpoints
@app.route("/testpost")
def test_post():
    try:
        bot.send_message(chat_id=os.getenv("CHAT_ID"), text="🚀 Test message from bot!")
        return "Test post succeeded", 200
    except Exception as e:
        return f"Test post failed: {str(e)}", 500

@app.route("/testredis")
def test_redis():
    try:
        r.ping()
        return "Redis connected", 200
    except Exception as e:
        return f"Redis connection failed: {str(e)}", 500

@app.route("/keepalive")
def keepalive():
    return "🚀 Crypto News Bot Active", 200

# Start application
if __name__ == "__main__":
    print(f"[{datetime.now()}] 🚀 Starting Crypto News Bot")
    print(f"[{datetime.now()}] Using Redis: {os.getenv('REDIS_HOST')}")
    threading.Thread(target=process_feeds, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
