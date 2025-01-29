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
r = redis.from_url(os.getenv("REDIS_URL"))  
bot = Bot(token=os.getenv("BOT_TOKEN"))  

# Self-pinging to keep Render alive  
def start_pinger():  
    def ping():  
        while True:  
            try:  
                requests.get(f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com/keepalive")  
            except:  
                pass  
            time.sleep(840)  # 14 minutes  
    threading.Thread(target=ping, daemon=True).start()  

start_pinger()  # Start background thread  

# Crypto RSS Feeds (Update these)  
RSS_FEEDS = [  
    "https://coindesk.com/arc/outboundfeeds/rss/",  
    "https://cointelegraph.com/rss",  
    "https://api.follow.it/rss-parser/63UwVgw0N3X6nAX0kLdD2r",  # Decrypt  
    "https://cryptopanic.com/news/rss/",  
]  

def get_crypto_prices():  
    try:  
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")  
        data = response.json()  
        return (  
            f"BTC: ${data['bitcoin']['usd']} ({data['bitcoin']['usd_24h_change']:.1f}%)\n"  
            f"ETH: ${data['ethereum']['usd']} ({data['ethereum']['usd_24h_change']:.1f}%)"  
        )  
    except:  
        return "BTC/ETH: Price data unavailable"  

def fetch_article(url):  
    try:  
        article = Article(url)  
        article.download()  
        article.parse()  
        return article.text, article.top_image  
    except:  
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
    except:  
        sentences = re.split(r'(?<=[.!?]) +', text)  
        return ' '.join(sentences[:3]) if len(sentences) > 3 else text  

@app.route("/keepalive")  
def keepalive():  
    return "🚀 Crypto News Bot Active", 200  

def post_to_telegram(title, url, summary, image):  
    prices = get_crypto_prices()  
    message = (  
        f"🔥 *{escape_markdown(title)}* 🔥\n\n"  
        f"{escape_markdown(summary)}\n\n"  
        f"📊 *Market Update*:\n{prices}\n\n"  
        f"[Read More]({url}) | #Crypto #Bitcoin #Altcoins"  
    )  
    if image:  
        bot.send_photo(chat_id=os.getenv("CHAT_ID"), photo=image, caption=message, parse_mode=ParseMode.MARKDOWN_V2)  
    else:  
        bot.send_message(chat_id=os.getenv("CHAT_ID"), text=message, parse_mode=ParseMode.MARKDOWN_V2)  

def escape_markdown(text):  
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)  

def process_feeds():  
    while True:  
        for feed_url in RSS_FEEDS:  
            feed = feedparser.parse(feed_url)  
            for entry in feed.entries[:5]:  # Latest 5  
                content, image = fetch_article(entry.link)  
                if content and is_unique(content):  
                    summary = summarize(content)  
                    post_to_telegram(entry.title, entry.link, summary, image)  
                    r.set(hashlib.md5(content[:500].encode()).hexdigest(), "1", ex=604800)  # Cache 7 days  
        time.sleep(900)  # 15 minutes between checks  

if __name__ == "__main__":  
    threading.Thread(target=process_feeds, daemon=True).start()  
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))  
