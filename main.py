import os
import requests
import logging
import json
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from telegram.error import TelegramError, Forbidden, BadRequest
from telegram.constants import ParseMode
from dotenv import load_dotenv

# Logging सेटअप
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# .env फ़ाइल लोड करें
load_dotenv()

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "https://encore.sahilraz9265.workers.dev/numbr?num=")
try:
    ADMIN_ID = int(os.getenv("7524032836"))
except (TypeError, ValueError):
    ADMIN_ID = None
    logger.error("ADMIN_ID is missing or invalid in .env file.")

# Settings
DAILY_CREDITS_LIMIT = 3
REFERRAL_CREDITS = 3
SUPPORT_CHANNEL_USERNAME = "narzoxbot"
SUPPORT_CHANNEL_LINK = "https://t.me/narzoxbot"
DATA_FILE = "bot_data.json"
BANNED_USERS_FILE = "banned_users.json"
# ---------------------

# --- GLOBAL STORAGE ---
USER_CREDITS = {}
USERS = set()
REFERRED_TRACKER = set()
UNLIMITED_USERS = {}  # {user_id: expiry_timestamp or "forever"}
BANNED_USERS = set()
USER_SEARCH_HISTORY = {}  # {user_id: [searches]}
DAILY_STATS = {"searches": 0, "new_users": 0, "referrals": 0}
# -----------------------------------------------------------------

def load_data():
    """JSON फाइल से डेटा लोड करें"""
    global USER_CREDITS, USERS, REFERRED_TRACKER, UNLIMITED_USERS, USER_SEARCH_HISTORY, DAILY_STATS
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                USER_CREDITS = {int(k): v for k, v in data.get('credits', {}).items()}
                USERS = set(data.get('users', []))
                REFERRED_TRACKER = set(tuple(x) for x in data.get('referrals', []))
                UNLIMITED_USERS = {int(k): v for k, v in data.get('unlimited', {}).items()}
                USER_SEARCH_HISTORY = {int(k): v for k, v in data.get('search_history', {}).items()}
                DAILY_STATS = data.get('daily_stats', {"searches": 0, "new_users": 0, "referrals": 0})
                logger.info(f"✅ Data loaded: {len(USERS)} users, {len(UNLIMITED_USERS)} unlimited users")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}")

def save_data():
    """JSON फाइल में डेटा सेव करें"""
    try:
        data = {
            'credits': USER_CREDITS,
            'users': list(USERS),
            'referrals': [list(x) for x in REFERRED_TRACKER],
            'unlimited': UNLIMITED_USERS,
            'search_history': USER_SEARCH_HISTORY,
            'daily_stats': DAILY_STATS,
            'last_updated': datetime.now().isoformat()
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ Error saving data: {e}")

def load_banned_users():
    """बैन किए गए यूजर्स लोड करें"""
    global BANNED_USERS
    try:
        if os.path.exists(BANNED_USERS_FILE):
            with open(BANNED_USERS_FILE, 'r') as f:
                BANNED_USERS = set(json.load(f))
    except Exception as e:
        logger.error(f"Error loading banned users: {e}")

def save_banned_users():
    """बैन किए गए यूजर्स सेव करें"""
    try:
        with open(BANNED_USERS_FILE, 'w') as f:
            json.dump(list(BANNED_USERS), f)
    except Exception as e:
        logger.error(f"Error saving banned users: {e}")

def get_credits(user_id: int) -> int:
    """यूजर के वर्तमान क्रेडिट्स प्राप्त करें"""
    if is_unlimited(user_id):
        return float('inf')
    
    if user_id not in USER_CREDITS:
        USER_CREDITS[user_id] = DAILY_CREDITS_LIMIT
        save_data()
    
    return USER_CREDITS.get(user_id, 0)

def is_unlimited(user_id: int) -> bool:
    """चेक करें कि यूजर के पास अनलिमिटेड एक्सेस है या नहीं"""
    if user_id == ADMIN_ID:
        return True
    
    if user_id not in UNLIMITED_USERS:
        return False
    
    expiry = UNLIMITED_USERS[user_id]
    
    if expiry == "forever":
        return True
    
    if datetime.now().timestamp() < expiry:
        return True
    else:
        del UNLIMITED_USERS[user_id]
        save_data()
        return False

def get_unlimited_expiry_text(user_id: int) -> str:
    """अनलिमिटेड एक्सपायरी का टेक्स्ट पाएं"""
    if user_id not in UNLIMITED_USERS:
        return ""
    
    expiry = UNLIMITED_USERS[user_id]
    if expiry == "forever":
        return "हमेशा के लिए ♾️"
    
    expiry_date = datetime.fromtimestamp(expiry)
    remaining = expiry_date - datetime.now()
    
    if remaining.days > 0:
        return f"{remaining.days} दिन बाकी"
    elif remaining.seconds > 3600:
        hours = remaining.seconds // 3600
        return f"{hours} घंटे बाकी"
    else:
        minutes = remaining.seconds // 60
        return f"{minutes} मिनट बाकी"

def get_referral_link(bot_username: str, user_id: int) -> str:
    """रेफरल लिंक बनाएं"""
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

def save_user(user_id: int) -> None:
    """यूजर ID सेव करें"""
    if user_id not in USERS:
        USERS.add(user_id)
        DAILY_STATS["new_users"] += 1
        save_data()

def add_search_history(user_id: int, number: str) -> None:
    """सर्च हिस्ट्री में जोड़ें"""
    if user_id not in USER_SEARCH_HISTORY:
        USER_SEARCH_HISTORY[user_id] = []
    
    USER_SEARCH_HISTORY[user_id].append({
        "number": number,
        "timestamp": datetime.now().isoformat()
    })
    
    # केवल आखिरी 50 सर्च रखें
    if len(USER_SEARCH_HISTORY[user_id]) > 50:
        USER_SEARCH_HISTORY[user_id] = USER_SEARCH_HISTORY[user_id][-50:]
    
    DAILY_STATS["searches"] += 1
    save_data()

async def check_channel_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """चेक करें कि यूजर चैनल का मेंबर है या नहीं"""
    try:
        member = await context.bot.get_chat_member(f"@{SUPPORT_CHANNEL_USERNAME}", user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError as e:
        logger.error(f"Error checking membership for {user_id}: {e}")
        return False

async def force_channel_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """यूजर को चैनल ज्वाइन करने के लिए कहें"""
    user_id = update.effective_user.id
    
    # Admin और Unlimited users को bypass दें
    if user_id == ADMIN_ID or (user_id in UNLIMITED_USERS and is_unlimited(user_id)):
        return True
    
    is_member = await check_channel_membership(user_id, context)
    
    if not is_member:
        keyboard = [
            [InlineKeyboardButton("📢 चैनल ज्वाइन करें", url=SUPPORT_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ मैंने ज्वाइन कर लिया", callback_data='check_membership')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            "🔒 **बॉट का उपयोग करने के लिए आपको पहले हमारे चैनल को ज्वाइन करना होगा!**\n\n"
            "✨ **चैनल ज्वाइन करने के फायदे:**\n"
            "• नई अपडेट्स सबसे पहले पाएं\n"
            "• स्पेशल ऑफर्स और बोनस क्रेडिट्स\n"
            "• प्रीमियम फीचर्स का एक्सेस\n\n"
            "नीचे दिए गए बटन से चैनल ज्वाइन करें और फिर '✅ मैंने ज्वाइन कर लिया' पर क्लिक करें।"
        )
        
        if update.message:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        elif update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        
        return False
    
    return True

def is_banned(user_id: int) -> bool:
    """चेक करें कि यूजर बैन है या नहीं"""
    return user_id in BANNED_USERS

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start कमांड हैंडलर"""
    user_id = update.effective_user.id
    username = update.effective_user.first_name or "friend"
    bot_username = context.bot.username
    
    # बैन चेक
    if is_banned(user_id):
        await update.message.reply_text(
            "🚫 **आप इस बॉट का उपयोग करने से बैन हैं।**\n\n"
            "अधिक जानकारी के लिए सपोर्ट चैनल से संपर्क करें।"
        )
        return
    
    save_user(user_id)
    
    # चैनल मेंबरशिप चेक करें
    if not await force_channel_join(update, context):
        return
    
    # रेफरल लॉजिक
    referral_success = False
    if context.args and context.args[0].startswith('ref_'):
        try:
            referrer_id = int(context.args[0].split('_')[1])
            referral_key = (referrer_id, user_id)
            
            if referrer_id != user_id and referral_key not in REFERRED_TRACKER:
                if referrer_id in USERS:
                    if not is_unlimited(referrer_id):
                        USER_CREDITS[referrer_id] = USER_CREDITS.get(referrer_id, 0) + REFERRAL_CREDITS
                    
                    REFERRED_TRACKER.add(referral_key)
                    DAILY_STATS["referrals"] += 1
                    save_data()
                    
                    referrer_credits = "अनलिमिटेड ♾️" if is_unlimited(referrer_id) else USER_CREDITS[referrer_id]
                    
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"🥳 **बधाई हो!** 🎉\n\n"
                                f"👤 **{username}** ने आपके रेफरल लिंक से बॉट शुरू किया है।\n"
                                f"🎁 आपको **{REFERRAL_CREDITS} क्रेडिट** मिले हैं!\n"
                                f"💰 **आपके कुल क्रेडिट:** {referrer_credits}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except:
                        pass
                    
                    referral_success = True
                    await update.message.reply_text(
                        f"✅ **स्वागत है!** 🎊\n\n"
                        f"आपने रेफरल के ज़रिए बॉट शुरू किया है।\n"
                        f"आपको **{DAILY_CREDITS_LIMIT}** शुरुआती क्रेडिट मिले हैं। 🎁"
                    )
        except Exception as e:
            logger.error(f"Referral Error: {e}")
    
    # वेलकम मैसेज
    current_credits = get_credits(user_id)
    is_unli = is_unlimited(user_id)
    credit_text = "अनलिमिटेड ♾️" if is_unli else str(current_credits)
    
    keyboard = [
        [
            InlineKeyboardButton("🔍 नंबर सर्च करें", callback_data='how_to_search'),
            InlineKeyboardButton(f"🎁 {REFERRAL_CREDITS} क्रेडिट कमाएँ", callback_data='get_referral_link')
        ],
        [
            InlineKeyboardButton(f"💰 क्रेडिट्स ({credit_text})", callback_data='show_credits'),
            InlineKeyboardButton("📊 मेरी रेफरल", callback_data='my_referrals')
        ],
        [
            InlineKeyboardButton("📜 सर्च हिस्ट्री", callback_data='search_history'),
            InlineKeyboardButton("ℹ️ मदद", callback_data='help')
        ],
        [
            InlineKeyboardButton("📢 Support Channel", url=SUPPORT_CHANNEL_LINK)
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    unlimited_badge = " 👑" if is_unli else ""
    expiry_text = ""
    if is_unli and user_id != ADMIN_ID:
        expiry_text = f"\n⏰ **वैलिडिटी:** {get_unlimited_expiry_text(user_id)}"
    
    if not referral_success:
        welcome_message = (
            f"🤖 **नमस्ते {username}{unlimited_badge}!**\n"
            f"मैं आपका एडवांस्ड नंबर सर्च बॉट हूँ। 🚀\n\n"
            f"💎 **आपके क्रेडिट्स:** {credit_text}{expiry_text}\n\n"
            "✨ **मुख्य फीचर्स:**\n"
            "• 🔍 किसी भी नंबर की पूरी जानकारी\n"
            "• 🎁 रेफरल करके अनलिमिटेड क्रेडिट कमाएं\n"
            "• 📊 अपनी सर्च हिस्ट्री देखें\n"
            "• ⚡ तेज़ और सटीक रिजल्ट्स\n\n"
            f"🎁 **हर रेफरल = {REFERRAL_CREDITS} फ्री क्रेडिट!**\n\n"
            "👇 **शुरुआत करने के लिए नीचे के बटन दबाएं**"
        )
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/search कमांड हैंडलर"""
    user_id = update.effective_user.id
    save_user(user_id)
    
    # बैन चेक
    if is_banned(user_id):
        await update.message.reply_text("🚫 आप बैन हैं।")
        return
    
    # चैनल मेंबरशिप चेक
    if not await force_channel_join(update, context):
        return
    
    current_credits = get_credits(user_id)
    is_unli = is_unlimited(user_id)
    
    # क्रेडिट चेक
    if not is_unli and current_credits <= 0:
        keyboard = [
            [InlineKeyboardButton(f"🎁 {REFERRAL_CREDITS} क्रेडिट कमाएँ", callback_data='get_referral_link')],
            [InlineKeyboardButton("💳 क्रेडिट खरीदें", callback_data='buy_credits')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🛑 **क्रेडिट खत्म हो गए!** 😔\n\n"
            "आपके पास अभी **0 क्रेडिट** हैं।\n\n"
            "**क्रेडिट कैसे पाएं:**\n"
            f"1️⃣ दोस्तों को रेफर करें (+{REFERRAL_CREDITS} क्रेडिट हर रेफरल)\n"
            "2️⃣ Support channel से संपर्क करें\n\n"
            "👇 **नीचे के बटन से शुरू करें**",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ **गलत तरीका!**\n\n"
            "कृपया `/search` के बाद एक नंबर दें।\n\n"
            "**सही तरीका:**\n"
            "`/search 9798423774`\n\n"
            "**या सीधे नंबर भेजें:**\n"
            "`9798423774`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    num = context.args[0].strip().replace("+91", "").replace(" ", "").replace("-", "")
    
    # नंबर वैलिडेशन
    if not num.isdigit():
        await update.message.reply_text("❌ कृपया केवल नंबर दें। कोई अक्षर या स्पेशल कैरेक्टर न डालें।")
        return
    
    if len(num) < 10:
        await update.message.reply_text("❌ कृपया कम से कम 10 अंकों का मोबाइल नंबर दें।")
        return
    
    api_url = f"{API_BASE_URL}{num}"
    
    credit_msg = "" if is_unli else " (1 क्रेडिट लगेगा)"
    searching_msg = await update.message.reply_text(
        f"🔍 **सर्च हो रही है...**\n"
        f"📱 नंबर: `{num}`{credit_msg}\n\n"
        "⏳ कृपया प्रतीक्षा करें...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # क्रेडिट घटाएं
        if not is_unli:
            USER_CREDITS[user_id] -= 1
            save_data()
        
        # सर्च हिस्ट्री में जोड़ें
        add_search_history(user_id, num)
        
        response_message = "✅ **जानकारी मिल गई!** 🎉\n\n"
        user_data = None
        
        # API रिस्पांस प्रोसेस करें
        if 'result' in data and isinstance(data['result'], list) and len(data['result']) > 0:
            user_data = data['result'][0]
        elif isinstance(data, dict) and any(data.values()):
            user_data = data
        
        if user_data:
            # Api_owner हटाएं
            if 'Api_owner' in user_data:
                del user_data['Api_owner']
            
            response_message += "📋 **विवरण:**\n"
            for key, value in user_data.items():
                if value and str(value).strip():
                    clean_key = key.replace('_', ' ').title()
                    # Emoji जोड़ें
                    emoji = "📌"
                    if 'name' in key.lower():
                        emoji = "👤"
                    elif 'mobile' in key.lower() or 'phone' in key.lower():
                        emoji = "📱"
                    elif 'email' in key.lower():
                        emoji = "📧"
                    elif 'address' in key.lower():
                        emoji = "🏠"
                    elif 'state' in key.lower():
                        emoji = "🗺️"
                    elif 'city' in key.lower():
                        emoji = "🏙️"
                    
                    response_message += f"{emoji} **{clean_key}:** `{value}`\n"
            
            remaining_credits = "अनलिमिटेड ♾️" if is_unli else USER_CREDITS[user_id]
            response_message += f"\n💰 **क्रेडिट्स बाकी:** {remaining_credits}"
            
            if not is_unli and USER_CREDITS[user_id] <= 2:
                response_message += f"\n\n⚠️ **कम क्रेडिट!** दोस्तों को रेफर करें और {REFERRAL_CREDITS} क्रेडिट पाएं!"
            
            await searching_msg.edit_text(response_message, parse_mode=ParseMode.MARKDOWN)
        else:
            remaining_credits = "अनलिमिटेड ♾️" if is_unli else USER_CREDITS[user_id]
            await searching_msg.edit_text(
                f"❌ **जानकारी नहीं मिली**\n\n"
                f"📱 नंबर: `{num}`\n"
                f"इस नंबर के लिए कोई जानकारी उपलब्ध नहीं है।\n\n"
                f"💰 **क्रेडिट्स बाकी:** {remaining_credits}",
                parse_mode=ParseMode.MARKDOWN
            )
    
    except requests.exceptions.Timeout:
        if not is_unli:
            USER_CREDITS[user_id] += 1
            save_data()
        await searching_msg.edit_text(
            "⏰ **टाइमआउट!**\n\n"
            "सर्विस का रिस्पांस नहीं आया। कृपया कुछ देर बाद कोशिश करें।\n"
            "आपका क्रेडिट वापस कर दिया गया है।"
        )
    
    except requests.exceptions.RequestException as e:
        if not is_unli:
            USER_CREDITS[user_id] += 1
            save_data()
        logger.error(f"API Request Error: {e}")
        await searching_msg.edit_text(
            "🛑 **सर्विस में समस्या!**\n\n"
            "बाहरी सर्विस से कनेक्ट नहीं हो पा रहा है।\n"
            "कृपया बाद में कोशिश करें।\n\n"
            "आपका क्रेडिट वापस कर दिया गया है। ✅"
        )
    
    except Exception as e:
        logger.error(f"Unexpected Error: {e}")
        await searching_msg.edit_text(
            "❌ **अनपेक्षित गलती!**\n\n"
            "कुछ गलत हो गया। कृपया बाद में कोशिश करें।"
        )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """सीधे नंबर भेजने पर हैंडल करें"""
    text = update.message.text.strip()
    
    # चेक करें कि यह एक नंबर है
    clean_num = text.replace("+91", "").replace(" ", "").replace("-", "")
    if clean_num.isdigit() and len(clean_num) >= 10:
        context.args = [clean_num]
        await search_command(update, context)

async def unlimited_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """किसी यूजर को अनलिमिटेड एक्सेस दें (एडमिन ओनली)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "📝 **Unlimited Access Command**\n\n"
            "**Usage:**\n"
            "`/unlimited <user_id> [time]`\n\n"
            "**Examples:**\n"
            "• `/unlimited 123456789` ➜ Forever\n"
            "• `/unlimited 123456789 1h` ➜ 1 Hour\n"
            "• `/unlimited 123456789 12h` ➜ 12 Hours\n"
            "• `/unlimited 123456789 1d` ➜ 1 Day\n"
            "• `/unlimited 123456789 7d` ➜ 7 Days\n"
            "• `/unlimited 123456789 30d` ➜ 30 Days\n"
            "• `/unlimited 123456789 365d` ➜ 1 Year",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID. Please provide a valid number.")
        return
    
    expiry = "forever"
    duration_text = "हमेशा के लिए ♾️"
    
    if len(context.args) > 1:
        time_str = context.args[1].lower()
        try:
            if time_str.endswith('h'):
                hours = int(time_str[:-1])
                expiry = (datetime.now() + timedelta(hours=hours)).timestamp()
                duration_text = f"{hours} घंटे"
            elif time_str.endswith('d'):
                days = int(time_str[:-1])
                expiry = (datetime.now() + timedelta(days=days)).timestamp()
                duration_text = f"{days} दिन"
            elif time_str.endswith('m'):
                months = int(time_str[:-1])
                expiry = (datetime.now() + timedelta(days=months*30)).timestamp()
                duration_text = f"{months} महीने"
            else:
                await update.message.reply_text("❌ Invalid time format. Use: 1h, 7d, 30d, etc.")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid time value.")
            return
    
    UNLIMITED_USERS[target_user_id] = expiry
    save_data()
    
    keyboard = [
        [InlineKeyboardButton("📊 View All Unlimited Users", callback_data='admin_unlimited_list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ **Unlimited Access Granted!** 👑\n\n"
        f"👤 **User ID:** `{target_user_id}`\n"
        f"⏰ **Duration:** {duration_text}\n"
        f"📅 **Date:** {datetime.now().strftime('%d-%m-%Y %H:%M')}`",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"🎉 **बधाई हो!** 👑\n\n"
                f"आपको **Unlimited Search Access** मिल गया है!\n"
                f"⏰ **अवधि:** {duration_text}\n\n"
                f"अब आप बिना किसी लिमिट के सर्च कर सकते हैं! 🚀",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass

async def remove_unlimited_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """किसी यूजर का अनलिमिटेड एक्सेस हटाएं"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 **Usage:** `/remove_unlimited <user_id>`\n\n"
            "**Example:** `/remove_unlimited 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
        return
    
    if target_user_id in UNLIMITED_USERS:
        del UNLIMITED_USERS[target_user_id]
        save_data()
        await update.message.reply_text(
            f"✅ **Unlimited Access Removed**\n\n"
            f"User `{target_user_id}` का unlimited access हटा दिया गया है।",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="⚠️ आपका **Unlimited Access** समाप्त हो गया है।\n\n"
                    "अब आप normal credits के साथ बॉट का उपयोग कर सकते हैं।"
            )
        except:
            pass
    else:
        await update.message.reply_text(f"❌ User `{target_user_id}` के पास unlimited access नहीं है।", parse_mode=ParseMode.MARKDOWN)

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """किसी यूजर को बैन करें"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 **Usage:** `/ban <user_id> [reason]`\n\n"
            "**Example:** `/ban 123456789 Spam`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
        return
    
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
    
    BANNED_USERS.add(target_user_id)
    save_banned_users()
    
    await update.message.reply_text(
        f"🚫 **User Banned**\n\n"
        f"👤 **User ID:** `{target_user_id}`\n"
        f"📝 **Reason:** {reason}",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"🚫 **You have been banned from using this bot.**\n\n"
                f"**Reason:** {reason}\n\n"
                "Contact support for more information."
        )
    except:
        pass

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """किसी यूजर को अनबैन करें"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    if not context.args:
        await update.message.reply_text("📝 **Usage:** `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
        return
    
    if target_user_id in BANNED_USERS:
        BANNED_USERS.remove(target_user_id)
        save_banned_users()
        await update.message.reply_text(f"✅ User `{target_user_id}` को unban कर दिया गया है।", parse_mode=ParseMode.MARKDOWN)
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="✅ **Good news!** आपको unban कर दिया गया है।\n\n"
                    "अब आप बॉट का दोबारा उपयोग कर सकते हैं।"
            )
        except:
            pass
    else:
        await update.message.reply_text(f"❌ User `{target_user_id}` banned नहीं है।", parse_mode=ParseMode.MARKDOWN)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """बॉट की स्टेटिस्टिक्स दिखाएं"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    total_users = len(USERS)
    total_referrals = len(REFERRED_TRACKER)
    unlimited_users = len(UNLIMITED_USERS)
    banned_users = len(BANNED_USERS)
    total_searches = DAILY_STATS.get("searches", 0)
    
    # Total credits distributed
    total_credits_used = sum(DAILY_CREDITS_LIMIT - USER_CREDITS.get(uid, 0) for uid in USERS if uid not in UNLIMITED_USERS)
    
    keyboard = [
        [
            InlineKeyboardButton("👥 Top Users", callback_data='admin_top_users'),
            InlineKeyboardButton("👑 Unlimited List", callback_data='admin_unlimited_list')
        ],
        [
            InlineKeyboardButton("🚫 Banned Users", callback_data='admin_banned_list'),
            InlineKeyboardButton("🔄 Refresh", callback_data='admin_stats')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    stats_message = (
        "📊 **Bot Statistics Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 **Total Users:** {total_users}\n"
        f"🔗 **Total Referrals:** {total_referrals}\n"
        f"👑 **Unlimited Users:** {unlimited_users}\n"
        f"🚫 **Banned Users:** {banned_users}\n"
        f"🔍 **Total Searches:** {total_searches}\n"
        f"💳 **Credits Used:** {total_credits_used}\n\n"
        f"📅 **Today's Stats:**\n"
        f"  • New Users: {DAILY_STATS.get('new_users', 0)}\n"
        f"  • Searches: {DAILY_STATS.get('searches', 0)}\n"
        f"  • Referrals: {DAILY_STATS.get('referrals', 0)}\n\n"
        f"⏰ **Last Update:** {datetime.now().strftime('%d-%m-%Y %H:%M')}"
    )
    
    await update.message.reply_text(stats_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """सभी यूजर्स को मैसेज ब्रॉडकास्ट करें"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    if not context.args:
        await update.message.reply_text(
            "📣 **Broadcast Command**\n\n"
            "**Usage:** `/broadcast <message>`\n\n"
            "**Example:**\n"
            "`/broadcast 🎉 Bot में नया फीचर आ गया है!`\n\n"
            "**Tips:**\n"
            "• Markdown formatting supported\n"
            "• Use \\n for new lines\n"
            "• Keep messages short and clear",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    broadcast_message = " ".join(context.args)
    success_count = 0
    failure_count = 0
    blocked_count = 0
    
    status_msg = await update.message.reply_text(
        f"⏳ **Broadcasting...**\n\n"
        f"👥 Target Users: {len(USERS)}\n"
        f"✅ Sent: 0\n"
        f"❌ Failed: 0"
    )
    
    for idx, chat_id in enumerate(USERS):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📢 **Broadcast Message**\n\n{broadcast_message}",
                parse_mode=ParseMode.MARKDOWN
            )
            success_count += 1
            
            # हर 50 मैसेज के बाद स्टेटस अपडेट करें
            if (idx + 1) % 50 == 0:
                await status_msg.edit_text(
                    f"⏳ **Broadcasting...**\n\n"
                    f"👥 Target Users: {len(USERS)}\n"
                    f"✅ Sent: {success_count}\n"
                    f"❌ Failed: {failure_count}\n"
                    f"📊 Progress: {idx + 1}/{len(USERS)}"
                )
            
            # Telegram rate limit से बचने के लिए delay
            if (idx + 1) % 30 == 0:
                await asyncio.sleep(1)
                
        except Forbidden:
            blocked_count += 1
            failure_count += 1
        except Exception as e:
            failure_count += 1
            logger.info(f"Failed to send to {chat_id}: {e}")
    
    final_message = (
        f"✅ **Broadcast Complete!**\n\n"
        f"📊 **Results:**\n"
        f"✅ Successfully Sent: {success_count}\n"
        f"❌ Failed: {failure_count}\n"
        f"🚫 Blocked Bot: {blocked_count}\n"
        f"📈 Success Rate: {(success_count/len(USERS)*100):.1f}%"
    )
    
    await status_msg.edit_text(final_message)

async def add_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """किसी यूजर को क्रेडिट्स जोड़ें"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ **अस्वीकृत!** यह कमांड केवल एडमिन के लिए है।")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "📝 **Usage:** `/addcredits <user_id> <credits>`\n\n"
            "**Example:** `/addcredits 123456789 50`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        credits_to_add = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ कृपया मान्य नंबर दें।")
        return
    
    if credits_to_add <= 0:
        await update.message.reply_text("❌ Credits 0 से ज्यादा होने चाहिए।")
        return
    
    if target_user_id not in USER_CREDITS:
        USER_CREDITS[target_user_id] = 0
    
    USER_CREDITS[target_user_id] += credits_to_add
    save_data()
    
    await update.message.reply_text(
        f"✅ **Credits Added Successfully!**\n\n"
        f"👤 **User ID:** `{target_user_id}`\n"
        f"➕ **Added:** {credits_to_add} credits\n"
        f"💰 **New Total:** {USER_CREDITS[target_user_id]} credits",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"🎉 **Bonus Credits!**\n\n"
                f"आपको **{credits_to_add} bonus credits** मिले हैं!\n"
                f"💰 **Total Credits:** {USER_CREDITS[target_user_id]}",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline बटन हैंडलर"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    save_user(user_id)
    bot_username = context.bot.username
    
    # चैनल मेंबरशिप चेक
    if query.data == 'check_membership':
        is_member = await check_channel_membership(user_id, context)
        if is_member:
            keyboard = [[InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "✅ **धन्यवाद!** 🎉\n\n"
                "आपने चैनल ज्वाइन कर लिया है।\n\n"
                "अब आप बॉट का पूरी तरह उपयोग कर सकते हैं!\n\n"
                "**सर्च करने के लिए:**\n"
                "`/search <नंबर>`\n"
                "**या सीधे नंबर भेजें**",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.answer("❌ आप अभी भी चैनल के मेंबर नहीं हैं! कृपया पहले ज्वाइन करें।", show_alert=True)
        return
    
    # बाकी बटन्स के लिए चैनल चेक
    if not await force_channel_join(update, context):
        return
    
    # बैन चेक
    if is_banned(user_id) and query.data != 'main_menu':
        await query.answer("🚫 आप बैन हैं।", show_alert=True)
        return
    
    if query.data == 'show_credits':
        current_credits = get_credits(user_id)
        is_unli = is_unlimited(user_id)
        credit_text = "अनलिमिटेड ♾️" if is_unli else str(current_credits)
        referral_count = sum(1 for r in REFERRED_TRACKER if r[0] == user_id)
        
        expiry_info = ""
        if is_unli and user_id != ADMIN_ID:
            expiry_info = f"\n⏰ **वैलिडिटी:** {get_unlimited_expiry_text(user_id)}"
        
        keyboard = [
            [InlineKeyboardButton(f"🎁 {REFERRAL_CREDITS} क्रेडिट कमाएँ", callback_data='get_referral_link')],
            [InlineKeyboardButton("📊 रेफरल स्टेटस", callback_data='my_referrals')],
            [InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        credits_msg = (
            f"💰 **आपके क्रेडिट्स:** {credit_text}{expiry_info}\n"
            f"🔗 **आपके रेफरल:** {referral_count}\n"
            f"🎁 **हर रेफरल:** +{REFERRAL_CREDITS} क्रेडिट\n\n"
        )
        
        if not is_unli:
            if current_credits <= 0:
                credits_msg += "⚠️ **क्रेडिट खत्म!** अभी रेफर करें और क्रेडिट कमाएं!"
            elif current_credits <= 2:
                credits_msg += f"⚠️ **कम क्रेडिट बचे हैं!** जल्दी रेफर करें।"
            else:
                credits_msg += f"✅ आप अभी **{current_credits} बार** सर्च कर सकते हैं।"
        
        await query.edit_message_text(credits_msg, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == 'get_referral_link':
        referral_link = get_referral_link(bot_username, user_id)
        referral_count = sum(1 for r in REFERRED_TRACKER if r[0] == user_id)
        total_earned = referral_count * REFERRAL_CREDITS
        current_credits = get_credits(user_id)
        credit_text = "अनलिमिटेड ♾️" if is_unlimited(user_id) else str(current_credits)
        
        referral_message = (
            "🔗 **आपका रेफरल लिंक:**\n"
            f"`{referral_link}`\n\n"
            "📋 **कैसे काम करता है:**\n"
            "1️⃣ ऊपर का लिंक कॉपी करें\n"
            "2️⃣ दोस्तों को WhatsApp/Telegram पर भेजें\n"
            f"3️⃣ जब वे ज्वाइन करें, आपको {REFERRAL_CREDITS} क्रेडिट मिलेंगे\n\n"
            "📊 **आपकी रेफरल स्टेट:**\n"
            f"👥 **कुल रेफरल:** {referral_count}\n"
            f"💰 **कमाए क्रेडिट:** {total_earned}\n"
            f"💎 **मौजूदा क्रेडिट:** {credit_text}"
        )
        
        share_text = f"🔍 Number Search Bot - किसी भी नंबर की जानकारी पाएं!\n\n{referral_link}"
        encoded_text = requests.utils.quote(share_text)
        
        keyboard = [
            [InlineKeyboardButton("💬 WhatsApp पर शेयर करें", url=f"https://wa.me/?text={encoded_text}")],
            [InlineKeyboardButton("📤 Telegram पर शेयर करें", url=f"https://t.me/share/url?url={referral_link}&text=Try this bot!")],
            [InlineKeyboardButton("🔙 वापस जाएँ", callback_data='show_credits')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(referral_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == 'my_referrals':
        referral_count = sum(1 for r in REFERRED_TRACKER if r[0] == user_id)
        total_earned = referral_count * REFERRAL_CREDITS
        
        # Top referrers में position
        referral_counts = {}
        for ref_id, _ in REFERRED_TRACKER:
            referral_counts[ref_id] = referral_counts.get(ref_id, 0) + 1
        
        sorted_referrers = sorted(referral_counts.items(), key=lambda x: x[1], reverse=True)
        user_rank = next((i+1 for i, (uid, _) in enumerate(sorted_referrers) if uid == user_id), "N/A")
        
        keyboard = [
            [InlineKeyboardButton("🎁 रेफरल लिंक पाएं", callback_data='get_referral_link')],
            [InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 **आपकी रेफरल स्टेटिस्टिक्स**\n\n"
            f"👥 **कुल रेफरल:** {referral_count}\n"
            f"💰 **कुल कमाए क्रेडिट:** {total_earned}\n"
            f"🎁 **प्रति रेफरल:** {REFERRAL_CREDITS} क्रेडिट\n"
            f"🏆 **आपकी रैंक:** #{user_rank}\n\n"
            "💡 **टिप:** जितने ज्यादा रेफर करेंगे, उतने ज्यादा क्रेडिट मिलेंगे!\n"
            "कोई लिमिट नहीं! 🚀",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'search_history':
        if user_id not in USER_SEARCH_HISTORY or not USER_SEARCH_HISTORY[user_id]:
            keyboard = [[InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📜 **सर्च हिस्ट्री खाली है**\n\n"
                "आपने अभी तक कोई सर्च नहीं की है।\n\n"
                "सर्च करने के लिए:\n"
                "`/search <नंबर>`",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        history = USER_SEARCH_HISTORY[user_id][-10:]  # Last 10 searches
        history_text = "📜 **आपकी आखिरी 10 सर्च:**\n\n"
        
        for idx, search in enumerate(reversed(history), 1):
            number = search['number']
            timestamp = datetime.fromisoformat(search['timestamp']).strftime('%d-%m-%Y %H:%M')
            history_text += f"{idx}. `{number}` - {timestamp}\n"
        
        keyboard = [
            [InlineKeyboardButton("🗑️ हिस्ट्री साफ करें", callback_data='clear_history')],
            [InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(history_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == 'clear_history':
        if user_id in USER_SEARCH_HISTORY:
            USER_SEARCH_HISTORY[user_id] = []
            save_data()
        
        keyboard = [[InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "✅ **हिस्ट्री साफ कर दी गई है!**",
            reply_markup=reply_markup
        )
    
    elif query.data == 'how_to_search':
        keyboard = [[InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔍 **नंबर कैसे सर्च करें:**\n\n"
            "**तरीका 1:** Command से\n"
            "`/search 9798423774`\n\n"
            "**तरीका 2:** सीधे नंबर भेजें\n"
            "`9798423774`\n\n"
            "📌 **नोट:**\n"
            "• हर सर्च में 1 क्रेडिट लगता है\n"
            "• 10 अंकों का mobile number डालें\n"
            "• +91 या 0 लगाने की जरूरत नहीं",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'help':
        keyboard = [[InlineKeyboardButton("🔙 मुख्य मेनू", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ **मदद और जानकारी**\n\n"
            "**📱 उपलब्ध कमांड्स:**\n"
            "• `/start` - बॉट शुरू करें\n"
            "• `/search <नंबर>` - नंबर सर्च करें\n\n"
            "**💰 क्रेडिट सिस्टम:**\n"
            f"• शुरुआत में {DAILY_CREDITS_LIMIT} फ्री क्रेडिट\n"
            f"• हर रेफरल पर {REFERRAL_CREDITS} क्रेडिट\n"
            "• हर सर्च में 1 क्रेडिट खर्च\n"
            "• रेफरल की कोई लिमिट नहीं!\n\n"
            "**🎁 रेफरल कैसे करें:**\n"
            "1. अपना रेफरल लिंक पाएं\n"
            "2. दोस्तों को भेजें\n"
            "3. जब वे ज्वाइन करें, क्रेडिट पाएं\n\n"
            f"**📢 सपोर्ट:** @{SUPPORT_CHANNEL_USERNAME}\n\n"
            "**🔒 प्राइवेसी:**\n"
            "आपकी सर्च हिस्ट्री सुरक्षित रहती है।",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'buy_credits':
        keyboard = [
            [InlineKeyboardButton("📢 Support से संपर्क करें", url=SUPPORT_CHANNEL_LINK)],
            [InlineKeyboardButton("🔙 वापस जाएँ", callback_data='show_credits')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "💳 **क्रेडिट खरीदें**\n\n"
            "अभी फिलहाल क्रेडिट खरीदने का कोई सिस्टम नहीं है।\n\n"
            "**फ्री क्रेडिट पाने के तरीके:**\n"
            f"🎁 दोस्तों को रेफर करें - हर रेफरल पर {REFERRAL_CREDITS} क्रेडिट\n"
            "📢 हमारे चैनल पर updates के लिए ज्वाइन करें\n\n"
            "अधिक जानकारी के लिए सपोर्ट चैनल से संपर्क करें।",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == 'main_menu':
        current_credits = get_credits(user_id)
        is_unli = is_unlimited(user_id)
        credit_text = "अनलिमिटेड ♾️" if is_unli else str(current_credits)
        username = query.from_user.first_name or "friend"
        unlimited_badge = " 👑" if is_unli else ""
        
        expiry_text = ""
        if is_unli and user_id != ADMIN_ID:
            expiry_text = f"\n⏰ **वैलिडिटी:** {get_unlimited_expiry_text(user_id)}"
        
        keyboard = [
            [
                InlineKeyboardButton("🔍 नंबर सर्च करें", callback_data='how_to_search'),
                InlineKeyboardButton(f"🎁 {REFERRAL_CREDITS} क्रेडिट कमाएँ", callback_data='get_referral_link')
            ],
            [
                InlineKeyboardButton(f"💰 क्रेडिट्स ({credit_text})", callback_data='show_credits'),
                InlineKeyboardButton("📊 मेरी रेफरल", callback_data='my_referrals')
            ],
            [
                InlineKeyboardButton("📜 सर्च हिस्ट्री", callback_data='search_history'),
                InlineKeyboardButton("ℹ️ मदद", callback_data='help')
            ],
            [
                InlineKeyboardButton("📢 Support Channel", url=SUPPORT_CHANNEL_LINK)
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = (
            f"🤖 **नमस्ते {username}{unlimited_badge}!**\n"
            f"मैं आपका एडवांस्ड नंबर सर्च बॉट हूँ। 🚀\n\n"
            f"💎 **आपके क्रेडिट्स:** {credit_text}{expiry_text}\n\n"
            "✨ **मुख्य फीचर्स:**\n"
            "• 🔍 किसी भी नंबर की पूरी जानकारी\n"
            "• 🎁 रेफरल करके अनलिमिटेड क्रेडिट कमाएं\n"
            "• 📊 अपनी सर्च हिस्ट्री देखें\n"
            "• ⚡ तेज़ और सटीक रिजल्ट्स\n\n"
            f"🎁 **हर रेफरल = {REFERRAL_CREDITS} फ्री क्रेडिट!**\n\n"
            "👇 **शुरुआत करने के लिए नीचे के बटन दबाएं**"
        )
        
        await query.edit_message_text(welcome_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    # Admin Buttons
    elif query.data == 'admin_stats' and user_id == ADMIN_ID:
        await stats_command(update, context)
    
    elif query.data == 'admin_top_users' and user_id == ADMIN_ID:
        # Top users by referrals
        referral_counts = {}
        for ref_id, _ in REFERRED_TRACKER:
            referral_counts[ref_id] = referral_counts.get(ref_id, 0) + 1
        
        sorted_referrers = sorted(referral_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        top_users_text = "🏆 **Top 10 Referrers:**\n\n"
        for idx, (uid, count) in enumerate(sorted_referrers, 1):
            emoji = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}️⃣"
            top_users_text += f"{emoji} User `{uid}` - {count} रेफरल\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Stats", callback_data='admin_stats')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(top_users_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == 'admin_unlimited_list' and user_id == ADMIN_ID:
        if not UNLIMITED_USERS:
            keyboard = [[InlineKeyboardButton("🔙 Back to Stats", callback_data='admin_stats')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "👑 **Unlimited Users List:**\n\nNo unlimited users found.",
                reply_markup=reply_markup
            )
            return
        
        unlimited_text = "👑 **Unlimited Users List:**\n\n"
        for uid, expiry in list(UNLIMITED_USERS.items())[:20]:
            if expiry == "forever":
                expiry_str = "Forever ♾️"
            else:
                expiry_date = datetime.fromtimestamp(expiry)
                expiry_str = expiry_date.strftime('%d-%m-%Y %H:%M')
            unlimited_text += f"• User `{uid}` - {expiry_str}\n"
        
        if len(UNLIMITED_USERS) > 20:
            unlimited_text += f"\n... and {len(UNLIMITED_USERS) - 20} more"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Stats", callback_data='admin_stats')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(unlimited_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == 'admin_banned_list' and user_id == ADMIN_ID:
        if not BANNED_USERS:
            keyboard = [[InlineKeyboardButton("🔙 Back to Stats", callback_data='admin_stats')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "🚫 **Banned Users List:**\n\nNo banned users found.",
                reply_markup=reply_markup
            )
            return
        
        banned_text = "🚫 **Banned Users List:**\n\n"
        for uid in list(BANNED_USERS)[:30]:
            banned_text += f"• User `{uid}`\n"
        
        if len(BANNED_USERS) > 30:
            banned_text += f"\n... and {len(BANNED_USERS) - 30} more"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Stats", callback_data='admin_stats')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(banned_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def set_bot_commands(application: Application) -> None:
    """बॉट commands सेट करें"""
    commands = [
        BotCommand("start", "🚀 Start the bot"),
        BotCommand("search", "🔍 Search a number"),
    ]
    
    await application.bot.set_my_commands(commands)
    logger.info("✅ Bot commands set successfully")

async def post_init(application: Application) -> None:
    """Initialization के बाद चलाएं"""
    await set_bot_commands(application)
    
    # Admin को startup notification भेजें
    if ADMIN_ID:
        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ **Bot Started Successfully!**\n\n"
                    f"👥 Total Users: {len(USERS)}\n"
                    f"👑 Unlimited Users: {len(UNLIMITED_USERS)}\n"
                    f"🚫 Banned Users: {len(BANNED_USERS)}\n"
                    f"⏰ Time: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

def main() -> None:
    """मुख्य फंक्शन"""
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN is not set in environment variables.")
        return
    
    if ADMIN_ID is None:
        print("⚠️ WARNING: ADMIN_ID is not set. Admin commands will not work.")
    
    # डेटा लोड करें
    load_data()
    load_banned_users()
    
    # Application बनाएं
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # User Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    
    # Admin Commands
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("unlimited", unlimited_command))
    application.add_handler(CommandHandler("remove_unlimited", remove_unlimited_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("addcredits", add_credits_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    
    # Message Handler - सीधे नंबर भेजने के लिए
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # Button Handler
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("=" * 50)
    print("✅ ADVANCED BOT IS RUNNING")
    print("=" * 50)
    print(f"👤 Admin ID: {ADMIN_ID}")
    print(f"📢 Channel: @{SUPPORT_CHANNEL_USERNAME}")
    print(f"👥 Total Users: {len(USERS)}")
    print(f"👑 Unlimited Users: {len(UNLIMITED_USERS)}")
    print(f"🚫 Banned Users: {len(BANNED_USERS)}")
    print(f"🔗 Total Referrals: {len(REFERRED_TRACKER)}")
    print(f"🔍 Total Searches: {DAILY_STATS.get('searches', 0)}")
    print(f"⏰ Started at: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
    print("=" * 50)
    
    # Start polling
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
