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

# Initialize services  
app = Flask(__name__)  
# r = redis.from_url(os.getenv("REDIS_URL"))  
bot = Bot(token=os.getenv("BOT_TOKEN"))  

r = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=os.getenv("REDIS_PORT"),
    password=os.getenv("REDIS_PASSWORD"),
    ssl=True,
    ssl_cert_reqs=None,  # Disable certificate validation
    decode_responses=True
)

# Self-pinging to keep Render alive  
def start_pinger():  
    def ping():  
        while True:  
            try:  
                requests.get(f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com/keepalive")
                print("Successfully pinged keepalive endpoint")  
            except Exception as e:
                print(f"Ping error: {str(e)}")  
            time.sleep(840)  # 14 minutes (prevents Render sleep)
    threading.Thread(target=ping, daemon=True).start()  

start_pinger()  # Start background thread  

# Verified working RSS feeds
RSS_FEEDS = [  
    "https://www.coindesk.com/arc/outboundfeeds/rss/",  # Fixed with www
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",  # Direct Decrypt feed
    "https://cryptopanic.com/news/rss/",
]

# Newspaper3k configuration
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_crypto_prices():  
    try:  
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")  
        data = response.json()  
        return (  
            f"BTC: ${data['bitcoin']['usd']} ({data['bitcoin']['usd_24h_change']:.1f}%)\n"  
            f"ETH: ${data['ethereum']['usd']} ({data['ethereum']['usd_24h_change']:.1f}%)"  
        )  
    except Exception as e:
        print(f"Price fetch error: {str(e)}")  
        return "BTC/ETH: Price data unavailable"  

def fetch_article(url):  
    try:  
        article = Article(url, headers=headers, request_timeout=10)
        article.download()
        article.parse()
        print(f"Fetched article from {url}")
        return article.text, article.top_image  
    except Exception as e:
        print(f"Article fetch failed: {str(e)}")  
        return "", ""  

def is_unique(content):  
    content_hash = hashlib.md5(content[:500].encode()).hexdigest()  
    return not r.exists(content_hash)  

def summarize(text):  
    try:  
        response = requests.post(  
            "https://api-inference.huggingface.co/models/facebook/bart-large-cnn",  
            headers={"Authorization": f"Bearer {os.getenv('HF_API_KEY')}"},  
            json={"inputs": text[:1024], "parameters": {"max_length": 150}}  
        )  
        return response.json()[0]['summary_text']  
    except Exception as e:
        print(f"Summarization failed: {str(e)}")  
        sentences = re.split(r'(?<=[.!?]) +', text)  
        return ' '.join(sentences[:3]) if len(sentences) > 3 else text  

@app.route("/keepalive")  
def keepalive():  
    return "🚀 Crypto News Bot Active", 200  

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
        print(f"Posted: {title}")
    except Exception as e:
        print(f"Posting failed: {str(e)}")

def escape_markdown(text):  
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)  

def process_feeds():  
    while True:  
        print("\n" + "="*40)
        print("Starting new feed check cycle")
        for feed_url in RSS_FEEDS:  
            try:
                print(f"\n🔍 Fetching feed: {feed_url}")  
                feed = feedparser.parse(feed_url)  
                
                if not feed.entries:
                    print(f"❗ No entries in feed: {feed_url}")
                    continue
                    
                print(f"Found {len(feed.entries)} entries")
                
                for entry in feed.entries[:5]:  # Latest 5  
                    print(f"\nProcessing: {entry.title}")
                    content, image = fetch_article(entry.link)  
                    
                    if not content:
                        print("Skipping - no content")
                        continue
                        
                    if is_unique(content):
                        print("New unique article found")
                        summary = summarize(content)  
                        post_to_telegram(entry.title, entry.link, summary, image)  
                        r.set(
                            hashlib.md5(content[:500].encode()).hexdigest(), 
                            "1", 
                            ex=604800  # 7 days
                        )
                    else:
                        print("Duplicate article - skipping")
                        
            except Exception as e:
                print(f"Feed processing error: {str(e)}")
                
        print("\nCompleted feed cycle. Sleeping...")        
        time.sleep(900)  # 15 minutes between checks  

if __name__ == "__main__":  
    print("🚀 Starting Crypto News Bot")
    print(f"Using Redis: {os.getenv('REDIS_URL', 'Not configured!')}")
    threading.Thread(target=process_feeds, daemon=True).start()  
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
