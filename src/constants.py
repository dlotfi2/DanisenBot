import os
import sys

if  hasattr(sys, '_MEIPASS'):  # Running as bundled EXE
    PROJECT_ROOT = os.path.dirname(sys.executable)
    CONFIG_DIR = PROJECT_ROOT
else:  # Running from source
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    CONFIG_DIR = os.path.join(PROJECT_ROOT, 'config')
# Project paths

DB_PATH = os.path.join(CONFIG_DIR, 'danisen.db')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
LOG_FILE = os.path.join(PROJECT_ROOT, 'bot.log')

# Default configuration
DEFAULT_CONFIG = {
    "bot_token": "",
    "ACTIVE_MATCHES_CHANNEL_ID": "0",
    "REPORTED_MATCHES_CHANNEL_ID": "0",
    "total_dans": 7,
    "minimum_derank": 2,
    "maximum_rank_difference": 1,
    "rank_gap_for_more_points": 1,
    "recent_opponents_limit": 2,
    "point_rollover": True,
    "queue_status": True,
    "special_rank_up_rules": False,
    "max_active_matches": 3,
    "rankup_points_normal": 3,
    "rankup_points_special": 5,
    "rankdown_points": -3
}

# Logging colors
LOG_COLORS = {
    'DEBUG': 'black',
    'INFO': 'blue',
    'WARNING': 'orange',
    'ERROR': 'red',
    'CRITICAL': 'purple'
}

# GUI constants
GUI_WINDOW_TITLE = "Danisen Bot"
GUI_MIN_WIDTH = 600
GUI_MIN_HEIGHT = 400


#Danisen Constants
MAX_FIELDS_PER_EMBED = 25
MAX_DAN_RANK = 12
SPECIAL_RANK_THRESHOLD = 7
RANKUP_POINTS_NORMAL = 3
RANKUP_POINTS_SPECIAL = 5
RANKDOWN_POINTS = -3
DEFAULT_DAN = 1
DEFAULT_POINTS = 0

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)