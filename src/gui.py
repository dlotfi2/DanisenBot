from bot import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
import asyncio
import sys
import json
import os
import qasync
from io import StringIO
import logging
import shutil
from constants import (
    DB_PATH, CONFIG_PATH, LOG_FILE, DEFAULT_CONFIG,
    LOG_COLORS, GUI_WINDOW_TITLE, GUI_MIN_WIDTH, GUI_MIN_HEIGHT,
    DEFAULT_DAN, DEFAULT_POINTS,
    RANKUP_POINTS_NORMAL, RANKUP_POINTS_SPECIAL, RANKDOWN_POINTS
)

from utils.config import save_config, load_config

# Create our custom stderr that redirects to logging
class LoggedStderr:
    def write(self, msg):
        if msg.strip():  # Only log non-empty messages
            stderr_logger.error(msg)
    
    def flush(self):
        pass

# Centralized logging setup
def setup_logging():
    logging.basicConfig(
        filename=LOG_FILE,
        filemode='w',
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    sys.excepthook = lambda exc_type, exc_value, exc_traceback: logging.error(
        "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
    )
    sys.stderr = LoggedStderr()

setup_logging()

# Create a logger for stderr
stderr_logger = logging.getLogger('stderr')
stderr_logger.setLevel(logging.DEBUG)

sys.stderr = LoggedStderr()

class MainTab(QWidget):
    def __init__(self, bot):
        super().__init__()

        # Create and configure logger
        self.logger = logging.getLogger(__name__)

        layout = QVBoxLayout(self)

        # Create status label to show current state
        self.status_label = QLabel("Current State: Stopped\n(Make sure you have a valid token in the config!)")
        layout.addWidget(self.status_label)

        #Add Main Content
        self.start_bot_button = QPushButton(text="Start the Bot")
        self.start_bot_button.setCheckable(True)
        self.start_bot_button.clicked.connect(self.on_button_clicked)

        layout.addWidget(self.start_bot_button)

        self.is_running = False

        self.bot = bot

    def on_button_clicked(self):
        self.is_running = self.start_bot_button.isChecked()
        if self.is_running:
            asyncio.create_task( self.start_bot())
            self.start_bot_button.setText("Stop")
            self.status_label.setText("Current State: Running")
        else:
            asyncio.create_task( self.stop_bot())
            self.start_bot_button.setText("Start")
            self.status_label.setText("Current State: Stopped")

    async def start_bot(self):
        self.logger.info("start_bot")
        try:
            config = load_config(CONFIG_PATH)
            token = config['bot_token']
            await self.bot.start(token)
        except Exception as e:
            self.logger.error(f"Failed to start bot: {str(e)}")

    async def stop_bot(self):
        self.logger.info("stop_bot")
        await self.bot.close()

class ConfigTab(QWidget):
    def __init__(self, bot):
        super().__init__()

        self.logger = logging.getLogger(__name__)

        self.bot = bot
        layout = QVBoxLayout(self)
        self.config_form_layout = QFormLayout()

        #create fields for the form
        self.bot_token = QLineEdit()
        self.bot_token.setPlaceholderText("Enter your Discord Bot Token")

        self.ACTIVE_MATCHES_CHANNEL_ID = QLineEdit()
        self.ACTIVE_MATCHES_CHANNEL_ID.setPlaceholderText("Enter the discord channel id where you want the bot to post match messages")

        self.REPORTED_MATCHES_CHANNEL_ID = QLineEdit()
        self.REPORTED_MATCHES_CHANNEL_ID.setPlaceholderText("Enter the discord channel id where you want the bot to report match results")

        self.total_dans = QSpinBox()
        self.total_dans.setRange(1, 12)
        self.total_dans.setValue(7)

        #you cannot derank below self.minimum_derank
        self.minimum_derank = QSpinBox()
        self.minimum_derank.setRange(1,12)
        self.minimum_derank.setValue(2)

        #you cannot gain points beating someone who is maximum_rank_difference below you in rank
        self.maximum_rank_difference = QSpinBox()
        self.maximum_rank_difference.setRange(1,11)
        self.maximum_rank_difference.setValue(2)

        self.rank_gap_for_more_points = QSpinBox()
        self.rank_gap_for_more_points.setRange(1,11)
        self.rank_gap_for_more_points.setValue(1)

        self.recent_opponents_limit = QSpinBox()
        self.recent_opponents_limit.setRange(1, 20)
        self.recent_opponents_limit.setValue(2)  # Default value
        self.recent_opponents_limit.setToolTip("Limit of recent opponents to track for matchmaking")

        self.max_active_matches = QSpinBox()
        self.max_active_matches.setRange(1, 10)
        self.max_active_matches.setValue(3)  # Default value
        self.max_active_matches.setToolTip("Maximum number of active matches allowed at a time")

        self.point_rollover = QCheckBox()
        self.point_rollover.setToolTip("whether point gains roll over on rank up")

        self.queue_status = QCheckBox()
        self.queue_status.setToolTip("whether matchmaking queue is enabled/disabled")

        self.special_rank_up_rules = QCheckBox()
        self.special_rank_up_rules.setToolTip("Enable special rank-up rules for players 7dan and above")

        self.rankup_points_normal = QSpinBox()
        self.rankup_points_normal.setRange(1, 20)
        self.rankup_points_normal.setValue(RANKUP_POINTS_NORMAL)
        self.rankup_points_normal.setToolTip("Points needed to rank up below the special rank threshold")

        self.rankup_points_special = QSpinBox()
        self.rankup_points_special.setRange(1, 20)
        self.rankup_points_special.setValue(RANKUP_POINTS_SPECIAL)
        self.rankup_points_special.setToolTip("Points needed to rank up at/above the special rank threshold")

        self.rankdown_points = QSpinBox()
        self.rankdown_points.setRange(-20, -1)
        self.rankdown_points.setValue(RANKDOWN_POINTS)
        self.rankdown_points.setToolTip("Points at/below which a player ranks down (must stay below 0 and below the rank-up thresholds)")

        #Adding fields to form
        self.config_form_layout.addRow("Bot Token:", self.bot_token)
        self.config_form_layout.addRow("Active Match Channel Id:", self.ACTIVE_MATCHES_CHANNEL_ID)
        self.config_form_layout.addRow("Reported Match Channel Id:", self.REPORTED_MATCHES_CHANNEL_ID)
        self.config_form_layout.addRow("Total Dans:", self.total_dans)
        self.config_form_layout.addRow("Minimum Derank:", self.minimum_derank)
        self.config_form_layout.addRow("Maximum Rank Difference:", self.maximum_rank_difference)
        self.config_form_layout.addRow("Rank Gap for More Points:", self.rank_gap_for_more_points)
        self.config_form_layout.addRow("Recent Opponents Limit:", self.recent_opponents_limit)
        self.config_form_layout.addRow("Max Active Matches:", self.max_active_matches)
        self.config_form_layout.addRow("Rankup Points (Normal):", self.rankup_points_normal)
        self.config_form_layout.addRow("Rankup Points (Special):", self.rankup_points_special)
        self.config_form_layout.addRow("Rankdown Points:", self.rankdown_points)

        #add checkboxes
        self.config_form_layout.addRow("Point Rollover:",  self.point_rollover)
        self.config_form_layout.addRow("Matchmaking Queue Status",  self.queue_status)
        self.config_form_layout.addRow("Special Rank-Up Rules:", self.special_rank_up_rules)
        
        layout.addLayout(self.config_form_layout)

        # Create save/load buttons
        self.button_layout = QVBoxLayout()
        self.save_button = QPushButton("Save Configuration")
        self.load_button = QPushButton("Load Configuration")
        self.save_button.clicked.connect(self.save_config)
        self.load_button.clicked.connect(self.load_config)
        self.button_layout.addWidget(self.save_button)
        self.button_layout.addWidget(self.load_button)

        #add buttons to layout
        layout.addLayout(self.button_layout)

        self.settings_file = CONFIG_PATH
        self.load_config()

    def get_config_dict(self):
        """Get current configuration as a dictionary"""
        return {
            #Text
            "bot_token" : self.bot_token.text(),
            "ACTIVE_MATCHES_CHANNEL_ID" : self.ACTIVE_MATCHES_CHANNEL_ID.text(),
            "REPORTED_MATCHES_CHANNEL_ID" : self.REPORTED_MATCHES_CHANNEL_ID.text(),
            #Numbers
            "total_dans" : self.total_dans.value(),
            "minimum_derank" : self.minimum_derank.value(),
            "maximum_rank_difference" : self.maximum_rank_difference.value(),
            "rank_gap_for_more_points" : self.rank_gap_for_more_points.value(),
            "recent_opponents_limit": self.recent_opponents_limit.value(),  # New
            "max_active_matches": self.max_active_matches.value(),  # New parameter
            "rankup_points_normal": self.rankup_points_normal.value(),
            "rankup_points_special": self.rankup_points_special.value(),
            "rankdown_points": self.rankdown_points.value(),
            #Bools
            "point_rollover" : self.point_rollover.isChecked(),
            "queue_status" :  self.queue_status.isChecked(),
            "special_rank_up_rules": self.special_rank_up_rules.isChecked(),
        }

    def set_config_dict(self, config):
        """Set configuration from a dictionary"""
        #Text
        self.bot_token.setText(config.get("bot_token", ""))
        self.ACTIVE_MATCHES_CHANNEL_ID.setText(config.get("ACTIVE_MATCHES_CHANNEL_ID", ""))
        self.REPORTED_MATCHES_CHANNEL_ID.setText(config.get("REPORTED_MATCHES_CHANNEL_ID", ""))
        #Numbers
        self.total_dans.setValue(config.get("total_dans", 7))
        self.minimum_derank.setValue(config.get("minimum_derank", 2))
        self.maximum_rank_difference.setValue(config.get("maximum_rank_difference", 1))
        self.rank_gap_for_more_points.setValue(config.get("rank_gap_for_more_points", 1))
        self.max_active_matches.setValue(config.get("max_active_matches", 3))
        self.recent_opponents_limit.setValue(config.get("recent_opponents_limit", 5))
        self.rankup_points_normal.setValue(config.get("rankup_points_normal", RANKUP_POINTS_NORMAL))
        self.rankup_points_special.setValue(config.get("rankup_points_special", RANKUP_POINTS_SPECIAL))
        self.rankdown_points.setValue(config.get("rankdown_points", RANKDOWN_POINTS))
        #Bools
        self.point_rollover.setChecked(config.get("point_rollover", True))
        self.queue_status.setChecked(config.get("queue_status", True))
        self.special_rank_up_rules.setChecked(config.get("special_rank_up_rules", False))

    def save_config(self):
        """Save configuration to file"""
        try:
            config = self.get_config_dict()
            save_config(self.settings_file, config)

            update_bot_config(self.bot)
            QMessageBox.information(self, "Success", "Configuration saved successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save configuration: {str(e)}")
    
    def load_config(self):
        """Load configuration from file"""
        try:
            self.logger.info(f"ConfigTab: Loading config from {self.settings_file}")
            config = load_config(self.settings_file)
            self.set_config_dict(config)
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Failed to load configuration: {str(e)}")

class ColoredQTextEditLogger(logging.Handler):
    COLORS = LOG_COLORS

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.text_widget.setReadOnly(True)
        format_string = '%(asctime)s - %(levelname)s - %(message)s'
        self.setFormatter(logging.Formatter(format_string))

    def emit(self, record):
        color = self.COLORS.get(record.levelno, 'black')
        msg = self.format(record)
        html = f'<span style="color: {color};">{msg}</span>'
        self.text_widget.append(html)

class LogTab(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        self.config_form_layout = QFormLayout()

        # Create and configure logger
        self.logger = logging.getLogger(__name__)

        # Create text display
        self.text_display = QTextEdit()
        self.text_display.setReadOnly(True)
        layout.addWidget(self.text_display)

        # Configure the root logger instead of creating a new one
        root_logger = logging.getLogger()  # Get the root logger
        root_logger.setLevel(logging.INFO)

        self.logs_handler = ColoredQTextEditLogger(self.text_display)
        root_logger.addHandler(self.logs_handler)

        #Add Main Content
        self.save_logs_button = QPushButton(text="Save Logs")
        self.save_logs_button.clicked.connect(self.save_logs)

        layout.addWidget(self.save_logs_button)
    
    def save_logs(self):
        text = self.text_display.toPlainText()

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Output",
            "",
            "Text Files (*.txt);;All Files (*)"
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as file:
                    file.write(text)
                self.logger.info(f"Output saved to {file_path}")
            except Exception as e:
                self.logger.error(f"Error saving file: {str(e)}")

class AdminTab(QWidget):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

        # Create and configure logger
        self.logger = logging.getLogger(__name__)

        layout = QVBoxLayout(self)
        # Create a button to trigger the save file dialog
        self.reset_season_button = QPushButton('Reset Danisen for new season\n(will backup danisen db file and resync roles)', self)
        self.reset_season_button.clicked.connect(self.reset_season)

        layout.addWidget(self.reset_season_button)

    def reset_season(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Output",
            "",
            "Database Files (*.db);;All Files (*)"
        )
        if not file_path:
            self.logger.info("Season reset cancelled by user (no backup file chosen).")
            return
        asyncio.create_task(self._reset_season_async(file_path))

    async def _reset_season_async(self, file_path):
        danisen = self.bot.get_cog("Danisen")
        self.logger.info("Season reset requested from Admin tab.")
        try:
            shutil.copy(DB_PATH, file_path)
            self.logger.info(f"danisen.db file backed up to {file_path}")

            danisen.database_cur.execute(
                "UPDATE players SET dan = ?, points = ?",
                (DEFAULT_DAN, DEFAULT_POINTS)
            )
            danisen.database_con.commit()
            self.logger.info(f"Player data reset to dan {DEFAULT_DAN}, points {DEFAULT_POINTS} for all players.")

            if self.bot.is_ready():
                self.logger.info(f"Resyncing roles across {len(self.bot.guilds)} guild(s) after season reset.")
                for guild in self.bot.guilds:
                    await danisen.sync_guild_roles(guild)
            else:
                self.logger.warning(
                    "Bot is not connected, so roles could not be resynced. "
                    "Run /sync_roles once the bot is back online."
                )
        except Exception as e:
            self.logger.error(f"Failed to reset season: {str(e)}")

class DanisenWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Create Log Tab so we dont miss any logs
        logtab = LogTab()


        self.con = sqlite3.connect(DB_PATH)

        # Create and configure logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        #Create config file if non-existant
        self.settings_file = CONFIG_PATH
        if not os.path.exists(self.settings_file):
            save_config(self.settings_file, DEFAULT_CONFIG)

        #Creating DanisenBot
        self.bot = create_bot(self.con)

        self.setWindowTitle(GUI_WINDOW_TITLE)
        self.setMinimumSize(GUI_MIN_WIDTH, GUI_MIN_HEIGHT)

        # Create the central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Tab widget
        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)


        # Add Tabs
        tabs.addTab(MainTab(self.bot), "Main")
        tabs.addTab(ConfigTab(self.bot), "Config")
        tabs.addTab(logtab, "Logs")
        tabs.addTab(AdminTab(self.bot), "Admin")
        #TODO tabs.addTab(self.create_logs_tab(), "Logs")

        layout.addWidget(tabs)

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)

def main():
    app = QApplication(sys.argv)

    # Create the qasync loop
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Create and show window
    window = DanisenWindow()
    window.show()

    # Run the event loop
    with loop:
        loop.run_forever()

if __name__ == '__main__':
    main()