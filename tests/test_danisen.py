import unittest
import coverage
import json
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque
import logging
import sys
import os
from src.constants import DB_PATH, CONFIG_PATH

# Add the project src directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Set the logging level to DEBUG
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]  # Output logs to the console
)
# Mock the discord.commands.slash_command decorator
def mock_slash_command(*args, **kwargs):
    def decorator(func):
        return func
    return decorator

# Apply the patch before importing Danisen
patch("discord.commands.slash_command", mock_slash_command).start()

from cogs.danisen import Danisen  # Import after patching
import discord

class TestDanisen(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Set up mocks for bot, database, and context
        self.bot = MagicMock()
        self.database = MagicMock()
        self.database_con = MagicMock()
        self.database_cur = MagicMock()
        self.database.cursor.return_value = self.database_cur
        self.database_con.cursor.return_value = self.database_cur
        self.database_con.commit = MagicMock()
        self.config_path = CONFIG_PATH

        # Initialize the Danisen cog
        self.danisen = Danisen(self.bot, self.database_con, self.config_path)

        # Mock database cursor methods
        self.database_cur.execute = MagicMock(return_value=self.database_cur)
        self.database_cur.fetchone = MagicMock()
        self.database_cur.fetchall = MagicMock()

        # Mock context and author
        self.ctx = AsyncMock()
        self.ctx.author = AsyncMock()
        self.ctx.author.add_roles = AsyncMock()
        self.ctx.author.remove_roles = AsyncMock()
        self.ctx.respond = AsyncMock()
        self.ctx.defer = AsyncMock()

    def mock_database_response(self, fetchone=None, fetchall=None):
        """Helper to mock database responses for fetchone and fetchall."""
        self.database_cur.fetchone.return_value = fetchone
        self.database_cur.fetchall.return_value = fetchall

    def mock_interaction(self):
        """Helper to mock ctx.interaction as a valid discord.Interaction."""
        self.ctx.interaction = MagicMock(spec=discord.Interaction)
        self.ctx.interaction.followup.send = AsyncMock()

    def mock_guild(self):
        """Helper to mock ctx.guild and its methods."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)

    async def call_and_verify(self, coro, *args, db_calls=None, response=None):
        """Helper to call a coroutine and verify database calls and responses."""
        await coro(*args)
        if db_calls:
            for call in db_calls:
                self.database_cur.execute.assert_any_call(*call)
        if response:
            self.ctx.respond.assert_called_with(response)

    async def test_register_new_player(self):
        """Test registering a new player with a character."""
        self.ctx.author.id = 12345
        self.ctx.author.name = "TestPlayer"
        char1 = "Hyde"

        self.mock_database_response(fetchone=None)
        self.mock_guild()

        await self.call_and_verify(
            self.danisen.register,
            self.ctx, char1,
            db_calls=[
                ("INSERT INTO players (discord_id, player_name, character, dan, points) VALUES (?, ?, ?, ?, ?)",
                 (12345, "TestPlayer", "Hyde", 1, 0))
            ],
            response=(
                "You are now registered as TestPlayer with the following character/s Hyde\n"
                "If you wish to add more characters, you can register multiple times!\n\n"
                "Welcome to the Danisen!"
            )
        )

    async def test_unregister_player_not_in_queue_or_match(self):
        """Test unregistering a player who is not in a queue or match."""
        self.ctx.author.id = 12345
        self.ctx.author.name = "TestPlayer"
        char1 = "Hyde"

        self.database_cur.fetchone.side_effect = [
            {"discord_id": 12345, "character": "Hyde", "dan": 1, "points": 0},
            None
        ]
        self.mock_guild()

        await self.call_and_verify(
            self.danisen.unregister,
            self.ctx, char1,
            db_calls=[
                ("SELECT * FROM players WHERE discord_id=? AND dan=?", (12345, 1)),
                ("DELETE FROM players WHERE discord_id=? AND character=?", (12345, "Hyde"))
            ],
            response="You have now unregistered Hyde"
        )

    async def test_join_queue(self):
        """Test joining the matchmaking queue."""
        self.ctx.author.id = 12345
        self.ctx.author.name = "TestPlayer"
        char = "Hyde"

        # Simulate player in the database
        self.mock_database_response(fetchone={"discord_id": 12345, "character": "Hyde", "dan": 1, "points": 0})

        # Call the join_queue function with rejoin_queue as part of args
        await self.call_and_verify(
            self.danisen.join_queue,
            self.ctx, char, False,  # Pass rejoin_queue as positional argument
            response="You've been added to the matchmaking queue with Hyde"
        )

    async def test_leave_queue(self):
        """Test leaving the matchmaking queue."""
        self.ctx.author.id = 12345
        self.danisen.matchmaking_queue.append({"player_name": "TestPlayer", "dan": 1, "discord_id": 12345, "character": "Hyde"})
        self.danisen.dans_in_queue[1].append({"player_name": "TestPlayer", "dan": 1, "discord_id": 12345, "character": "Hyde"})
        self.danisen.in_queue[12345] = [True, deque()]

        await self.call_and_verify(
            self.danisen.leave_queue,
            self.ctx,
            response="You have been removed from the queue"
        )

    async def test_view_queue(self):
        """Test viewing the matchmaking queue."""
        self.danisen.matchmaking_queue.extend([
            {"player_name": "Player1", "dan": 1},
            {"player_name": "Player2", "dan": 2}
        ])
        self.danisen.dans_in_queue[1].append({"player_name": "Player1", "dan": 1})
        self.danisen.dans_in_queue[2].append({"player_name": "Player2", "dan": 2})

        await self.call_and_verify(
            self.danisen.view_queue,
            self.ctx,
            response=(
                f"Current full MMQ {repr(self.danisen.matchmaking_queue)}\n"
                f"Current full DanQ {repr(self.danisen.dans_in_queue)}"
            )
        )

    async def test_dan(self):
        """Test viewing players in a specific dan."""
        self.mock_database_response(fetchall=[
            {"player_name": "Player1", "character": "Hyde", "dan": 1, "points": 0},
            {"player_name": "Player2", "character": "Linne", "dan": 1, "points": 2}
        ])
        self.mock_interaction()

        await self.danisen.dan(self.ctx, dan=1)
        self.ctx.interaction.followup.send.assert_called_once()

    async def test_leaderboard(self):
        """Test viewing the leaderboard."""
        self.mock_database_response(fetchall=[
            {"name": "Player1 Hyde", "value": "Dan: 2 Points: 3"},
            {"name": "Player2 Linne", "value": "Dan: 1 Points: 1"}
        ])
        self.mock_interaction()

        await self.danisen.leaderboard(self.ctx)
        self.ctx.interaction.followup.send.assert_called_once()

    async def test_update_max_matches(self):
        """Test updating the maximum number of active matches."""
        max_matches = 5

        await self.danisen.update_max_matches(self.ctx, max_matches)

        self.assertEqual(self.danisen.max_active_matches, 5)
        self.ctx.respond.assert_called_with("Max matches updated to 5")

    async def test_unregister_player_in_match(self):
        """Test unregistering a player who is in an active match."""
        self.ctx.author.name = "TestPlayer"
        char1 = "Hyde"

        self.danisen.in_match["TestPlayer"] = True

        await self.danisen.unregister(self.ctx, char1)

        self.database_cur.execute.assert_not_called()
        self.ctx.respond.assert_called_with("You cannot unregister while in an active match.")

    async def test_join_queue_already_in_queue(self):
        """Test joining the queue when already in the queue."""
        self.ctx.author.id = 12345
        char = "Hyde"

        self.danisen.in_queue[12345] = [True, deque()]

        await self.danisen.join_queue(self.ctx, char, rejoin_queue=False)

        self.ctx.respond.assert_called_with("You are already in the queue")

    async def test_score_update(self):
        """Test updating the score after a match."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]

        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 1, "points": 2, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 1, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)

        self.assertEqual(winner_rank, [2, 0])
        self.assertEqual(loser_rank, [1, 0])
        self.database_cur.execute.assert_any_call(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (2, 0, "Winner", "Hyde")
        )
        self.database_cur.execute.assert_any_call(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (1, 0, "Loser", "Linne")
        )

    async def test_score_update_configurable_rankup_points(self):
        """Test that a lower configured rankup_points_normal triggers rankup sooner."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]
        self.danisen.rankup_points_normal = 2

        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 1, "points": 1, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 1, "points": 3, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)

        # 1 point + 1 for the win = 2, which now meets the lowered rankup threshold
        self.assertEqual(winner_rank, [2, 0])

    async def test_score_update_configurable_rankdown_points(self):
        """Test that a less negative configured rankdown_points triggers rankdown sooner."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]
        self.danisen.rankdown_points = -1

        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 1, "points": 0, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 2, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)

        # 0 points - 1 for the loss = -1, which now meets the raised rankdown threshold
        self.assertEqual(loser_rank, [1, 0])

    async def test_set_queue(self):
        """Test enabling and disabling the matchmaking queue."""
        queue_status = False

        await self.danisen.set_queue(self.ctx, queue_status)

        self.assertFalse(self.danisen.queue_status)
        self.ctx.respond.assert_called_with("The matchmaking queue has been disabled")

        queue_status = True

        await self.danisen.set_queue(self.ctx, queue_status)

        self.assertTrue(self.danisen.queue_status)
        self.ctx.respond.assert_called_with("The matchmaking queue has been enabled")

    async def test_matchmake(self):
        """Test matchmaking between players."""
        self.ctx.interaction = AsyncMock()
        player1 = {"player_name": "Player1", "dan": 1, "discord_id": 12345, "character": "Hyde", "points": 0}
        player2 = {"player_name": "Player2", "dan": 1, "discord_id": 67890, "character": "Linne", "points": 0}

        self.danisen.matchmaking_queue.extend([player1, player2])
        self.danisen.dans_in_queue[1].extend([player1, player2])
        self.danisen.in_queue = {
            12345: [True, deque()],
            67890: [True, deque()]
        }

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.danisen.matchmake(self.ctx.interaction)

        self.assertFalse(self.danisen.in_queue[12345][0])
        self.assertFalse(self.danisen.in_queue[67890][0])
        self.assertTrue(self.danisen.in_match[12345])
        self.assertTrue(self.danisen.in_match[67890])

        mock_channel.send.assert_called_once()

    async def test_matchmake_dan1_and_dan12(self):
        """Test matchmaking between a dan1 and a dan12 player."""
        self.ctx.interaction = AsyncMock()

        # Player1 (dan1)
        player1 = {
            "player_name": "Player1",
            "discord_id": 12345,
            "character": "Hyde",
            "dan": 1,
            "points": 0
        }
        # Player2 (dan12)
        player2 = {
            "player_name": "Player2",
            "discord_id": 67890,
            "character": "Linne",
            "dan": 12,
            "points": 0
        }

        # Add players to the matchmaking queue and dans_in_queue
        self.danisen.matchmaking_queue.extend([player1, player2])
        self.danisen.dans_in_queue[1].append(player1)
        self.danisen.dans_in_queue[12].append(player2)
        self.danisen.in_queue = {
            12345: [True, deque()],
            67890: [True, deque()]
        }

        # Mock create_match_interaction
        self.danisen.create_match_interaction = AsyncMock()

        # Run matchmaking
        await self.danisen.matchmake(self.ctx.interaction)

        # Assertions
        self.danisen.create_match_interaction.assert_called_once_with(self.ctx.interaction, player1, player2)
        self.assertFalse(self.danisen.in_queue[12345][0])  # Player1 is no longer in queue
        self.assertFalse(self.danisen.in_queue[67890][0])  # Player2 is no longer in queue
        self.assertTrue(self.danisen.in_match[12345])  # Player1 is in a match
        self.assertTrue(self.danisen.in_match[67890])  # Player2 is in a match

    async def test_report_match(self):
        """Test reporting the result of a match."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]

        self.database_cur.fetchone.side_effect = [
            {"player_name": "Player1", "discord_id": 12345, "dan": 1, "points": 2, "character": "Hyde"},
            {"player_name": "Player2", "discord_id": 67890, "dan": 1, "points": 0, "character": "Linne"},
            None,
            None
        ]

        await self.danisen.report_match(
            self.ctx,
            player1_name="Player1",
            char1="Hyde",
            player2_name="Player2",
            char2="Linne",
            winner="player1"
        )

        self.database_cur.execute.assert_any_call(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (2, 0, "Player1", "Hyde")
        )
        self.database_cur.execute.assert_any_call(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (1, 0, "Player2", "Linne")
        )
        self.ctx.respond.assert_called_with(
            "Match has been reported as Player1's victory over Player2\n"
            "Player1's Hyde rank is now 2 dan 0 points\n"
            "Player2's Linne rank is now 1 dan 0 points"
        )

    async def test_can_manage_role(self):
        """Test if the bot can manage a specific role."""
        bot_member = MagicMock()
        bot_member.top_role.position = 10
        bot_member.guild_permissions.manage_roles = True

        role = MagicMock()
        role.position = 5

        result = self.danisen.can_manage_role(bot_member, role)

        self.assertTrue(result)

    async def test_update_config(self):
        """Test updating the configuration from the config file."""
        mock_config = {
            "ACTIVE_MATCHES_CHANNEL_ID": 123,
            "REPORTED_MATCHES_CHANNEL_ID": 456,
            "total_dans": 10,
            "minimum_derank": 1,
            "maximum_rank_difference": 2,
            "rank_gap_for_more_points": 1,
            "point_rollover": False,
            "queue_status": False,
            "recent_opponents_limit": 5,
            "rankup_points_normal": 4,
            "rankup_points_special": 6,
            "rankdown_points": -2
        }

        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(mock_config))):
            with patch("os.path.exists", return_value=True):
                self.danisen.update_config()

        self.assertEqual(self.danisen.ACTIVE_MATCHES_CHANNEL_ID, 123)
        self.assertEqual(self.danisen.REPORTED_MATCHES_CHANNEL_ID, 456)
        self.assertEqual(self.danisen.total_dans, 10)
        self.assertEqual(self.danisen.minimum_derank, 1)
        self.assertEqual(self.danisen.maximum_rank_difference, 2)
        self.assertEqual(self.danisen.rank_gap_for_more_points, 1)
        self.assertFalse(self.danisen.point_rollover)
        self.assertFalse(self.danisen.queue_status)
        self.assertEqual(self.danisen.recent_opponents_limit, 5)
        self.assertEqual(self.danisen.rankup_points_normal, 4)
        self.assertEqual(self.danisen.rankup_points_special, 6)
        self.assertEqual(self.danisen.rankdown_points, -2)

    async def test_set_rank(self):
        """Test setting the rank and points for a player."""
        player_name = "TestPlayer"
        char = "Hyde"
        dan = 3
        points = 2

        await self.danisen.set_rank(self.ctx, player_name, char, dan, points)

        self.database_cur.execute.assert_called_with(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (3, 2, "TestPlayer", "Hyde")
        )
        self.database_con.commit.assert_called_once()
        self.ctx.respond.assert_called_with("TestPlayer's Hyde rank updated to be dan 3 points 2")

    async def test_can_manage_role_insufficient_permissions(self):
        """Test if the bot cannot manage a role due to insufficient permissions."""
        bot_member = MagicMock()
        bot_member.top_role.position = 5
        bot_member.guild_permissions.manage_roles = False

        role = MagicMock()
        role.position = 10

        result = self.danisen.can_manage_role(bot_member, role)

        self.assertFalse(result)

    async def test_update_config_file_not_found(self):
        """Test behavior when the configuration file does not exist."""
        with patch("os.path.exists", return_value=False):
            self.danisen.update_config()

        self.assertEqual(self.danisen.total_dans, 12)
        self.assertTrue(self.danisen.queue_status)

    async def test_update_config_invalid_json(self):
        """Test behavior when the configuration file contains invalid JSON."""
        with patch("builtins.open", unittest.mock.mock_open(read_data="invalid_json")):
            with patch("os.path.exists", return_value=True):
                self.danisen.update_config()

        self.assertEqual(self.danisen.total_dans, 12)
        self.assertTrue(self.danisen.queue_status)

    async def test_view_config_redacts_bot_token(self):
        """Test that /view_config never exposes the raw bot token."""
        mock_config = {"bot_token": "super-secret-token"}

        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(mock_config))):
            with patch("os.path.exists", return_value=True):
                await self.danisen.view_config(self.ctx)

        self.ctx.respond.assert_called_once()
        _, kwargs = self.ctx.respond.call_args
        embed = kwargs["embed"]
        token_field = next(f for f in embed.fields if f.name == "bot_token")
        self.assertNotIn("super-secret-token", token_field.value)

    async def test_set_config_rankdown_points_valid(self):
        """Test that rankdown_points can be updated when it stays below the rankup thresholds and DEFAULT_POINTS."""
        with patch("os.path.exists", return_value=False):
            with patch("os.makedirs"):
                with patch("builtins.open", unittest.mock.mock_open()):
                    with patch("cogs.danisen.json.dump") as mock_dump:
                        with patch.object(self.danisen, "update_config"):
                            await self.danisen.set_config(self.ctx, "rankdown_points", "-2")

        written_cfg = mock_dump.call_args[0][0]
        self.assertEqual(written_cfg["rankdown_points"], -2)
        self.ctx.respond.assert_called_with(
            "Configuration key `rankdown_points` updated to `-2`", ephemeral=True
        )

    async def test_set_config_rankdown_points_rejects_non_negative(self):
        """Test that rankdown_points must stay below the default starting points (0)."""
        with patch("os.path.exists", return_value=False):
            with patch("builtins.open", unittest.mock.mock_open()):
                with patch("cogs.danisen.json.dump") as mock_dump:
                    await self.danisen.set_config(self.ctx, "rankdown_points", "0")

        mock_dump.assert_not_called()
        message = self.ctx.respond.call_args[0][0]
        self.assertIn("Invalid value", message)

    async def test_set_config_rankdown_points_rejects_when_not_below_rankup(self):
        """Test that rankdown_points must stay below both rankup thresholds."""
        existing_cfg = {"rankup_points_normal": -5, "rankup_points_special": -5}
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(existing_cfg))):
                with patch("cogs.danisen.json.dump") as mock_dump:
                    await self.danisen.set_config(self.ctx, "rankdown_points", "-3")

        mock_dump.assert_not_called()
        message = self.ctx.respond.call_args[0][0]
        self.assertIn("Invalid value", message)

    async def test_set_config_rankup_points_normal_rejects_when_at_or_below_rankdown(self):
        """Test that lowering rankup_points_normal to or below rankdown_points is rejected."""
        existing_cfg = {"rankdown_points": -3}
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(existing_cfg))):
                with patch("cogs.danisen.json.dump") as mock_dump:
                    await self.danisen.set_config(self.ctx, "rankup_points_normal", "-3")

        mock_dump.assert_not_called()
        message = self.ctx.respond.call_args[0][0]
        self.assertIn("Invalid value", message)

    async def test_dead_role_role_removal(self):
        """Test if the correct role is returned for removal."""
        self.database_cur.fetchone.return_value = None  # Simulate no remaining players in the dan
        
        mock_role = MagicMock()
        mock_role.name = "Dan 1"
        ctx = MagicMock()
        ctx.guild.roles = [mock_role]

        player = {"discord_id": 12345, "dan": 1}
        role = self.danisen.dead_role(ctx, player)

        self.assertEqual(role.name, "Dan 1")

    async def test_dead_role_no_removal(self):
        """Test if no role is returned when the player is not the last in the dan."""
        self.database_cur.fetchone.return_value = {"discord_id": 12345, "dan": 1}  # Simulate remaining players
        ctx = MagicMock()

        player = {"discord_id": 12345, "dan": 1}
        role = self.danisen.dead_role(ctx, player)

        self.assertIsNone(role)

    async def test_score_update_rankup(self):
        """Test rank-up behavior when a player reaches the required points."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]

        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 1, "points": 2, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 1, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)

        self.assertEqual(winner_rank, [2, 0])  # Winner ranks up to Dan 2
        self.assertEqual(loser_rank, [1, 0])  # Loser remains at Dan 1

    async def test_score_update_rankdown(self):
        """Test rank-down behavior when a player loses enough points."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]

        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 1, "points": 0, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 2, "points": -3, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)

        self.assertEqual(winner_rank, [1, 2])  # Winner gains 2 points due to rank gap
        self.assertEqual(loser_rank, [1, 0])  # Loser ranks down to Dan 1

    async def test_set_rank_invalid_player(self):
        """Test setting rank for a player who does not exist."""
        self.database_cur.execute.return_value = None  # Simulate no matching player

        await self.danisen.set_rank(self.ctx, "NonExistentPlayer", "Hyde", 3, 2)

        self.ctx.respond.assert_called_with("NonExistentPlayer's Hyde rank updated to be dan 3 points 2")

    async def test_join_queue_queue_closed(self):
        """Test joining the queue when the queue is closed."""
        self.danisen.queue_status = False  # Close the queue

        await self.danisen.join_queue(self.ctx, "Hyde", rejoin_queue=False)

        self.ctx.respond.assert_called_with("The matchmaking queue is currently closed")

    async def test_view_queue_empty(self):
        """Test viewing the queue when it is empty."""
        self.danisen.matchmaking_queue.clear()
        self.danisen.dans_in_queue = {dan: deque() for dan in range(1, self.danisen.total_dans + 1)}

        await self.danisen.view_queue(self.ctx)

        expected_mmq = repr(self.danisen.matchmaking_queue)
        expected_danq = repr(self.danisen.dans_in_queue)
        self.ctx.respond.assert_called_with(f"Current full MMQ {expected_mmq}\nCurrent full DanQ {expected_danq}")

    async def test_matchmake_insufficient_players(self):
        """Test matchmaking when there are fewer than two players in the queue."""
        self.danisen.matchmaking_queue.append({"player_name": "Player1", "dan": 1})

        await self.danisen.matchmake(self.ctx.interaction)

        self.assertEqual(len(self.danisen.matchmaking_queue), 1)

    async def test_report_match_invalid_player(self):
        """Test reporting a match when one or both players do not exist."""
        self.database_cur.fetchone.side_effect = [None, None]  # Simulate no players found

        await self.danisen.report_match(
            self.ctx,
            player1_name="NonExistentPlayer1",
            char1="Hyde",
            player2_name="NonExistentPlayer2",
            char2="Linne",
            winner="player1"
        )

        self.ctx.respond.assert_called_with("No player named NonExistentPlayer1 with character Hyde")

    async def test_rank(self):
        """Test retrieving a player's rank for a specific character."""
        self.ctx.author.id = 12345
        self.ctx.author.name = "TestPlayer"
        char = "Hyde"

        mock_member = MagicMock()
        mock_member.name = "TestPlayer"
        mock_member.id = 12345
        self.ctx.guild.members = [mock_member]

        self.mock_database_response(fetchone={"player_name": "TestPlayer", "dan": 2, "points": 3, "character": "Hyde"})

        await self.danisen.rank(self.ctx, char, discord_name="TestPlayer")

        self.ctx.respond.assert_called_with("TestPlayer's rank for Hyde is 2 dan 3 points")

    async def test_rejoin_queue(self):
        """Test rejoining the matchmaking queue."""
        player = {"discord_id": 12345, "player_name": "TestPlayer", "dan": 1, "character": "Hyde"}

        self.mock_database_response(fetchone={"discord_id": 12345, "player_name": "TestPlayer", "dan": 1, "character": "Hyde", "points": 0})

        self.danisen.rejoin_queue(player)

        self.assertTrue(self.danisen.in_queue[12345][0])
        self.assertEqual(self.danisen.dans_in_queue[1][0]['discord_id'], 12345)
        self.assertEqual(self.danisen.matchmaking_queue[0]['discord_id'], 12345)

    async def test_report_match_queue(self):
        """Test reporting a match result from the queue."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 1"), MagicMock(name="Dan 2")]

        player1 = {"player_name": "Player1", "discord_id": 12345, "dan": 1, "points": 2, "character": "Hyde"}
        player2 = {"player_name": "Player2", "discord_id": 67890, "dan": 1, "points": 0, "character": "Linne"}

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        self.bot.get_channel = MagicMock(return_value=mock_channel)

        await self.danisen.report_match_queue(self.ctx, player1, player2, winner="player1")

        self.database_cur.execute.assert_any_call(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (2, 0, "Player1", "Hyde")
        )
        self.database_cur.execute.assert_any_call(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (1, 0, "Player2", "Linne")
        )
        mock_channel.send.assert_called_once_with(
            "Match has been reported as Player1's victory over Player2\n"
            "Player1's Hyde rank is now 2 dan 0 points\n"
            "Player2's Linne rank is now 1 dan 0 points"
        )

    async def test_danisen_stats(self):
        """Test viewing various statistics about the Danisen system."""
        self.database_cur.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[
                {"name": "Hyde", "value": 5},
                {"name": "Linne", "value": 3}
            ])),
            MagicMock(fetchall=MagicMock(return_value=[
                {"name": 1, "value": 4},
                {"name": 2, "value": 4}
            ]))
        ]
        self.mock_interaction()

        await self.danisen.danisen_stats(self.ctx)

        self.ctx.interaction.followup.send.assert_called_once()

    async def test_matchmake_skip_duplicate_and_continue(self):
        """Test matchmaking skips duplicate matches and continues checking the same dan queue."""
        self.ctx.interaction = AsyncMock()

        # Player1 (dan3)
        player1 = {
            "player_name": "Player1",
            "discord_id": 12345,
            "character": "Hyde",
            "dan": 3,
            "points": 0
        }
        # Player2 (dan3, recent opponent of Player1)
        player2 = {
            "player_name": "Player2",
            "discord_id": 67890,
            "character": "Linne",
            "dan": 3,
            "points": 0
        }
        # Player3 (dan3, valid match for Player1)
        player3 = {
            "player_name": "Player3",
            "discord_id": 11223,
            "character": "Waldstein",
            "dan": 3,
            "points": 0
        }

        # Add players to the matchmaking queue and dans_in_queue
        self.danisen.matchmaking_queue.extend([player1, player2, player3])
        self.danisen.dans_in_queue[3].extend([player1, player2, player3])
        self.danisen.in_queue = {
            12345: [True, deque([67890])],  # Player2 is a recent opponent of Player1
            67890: [True, deque()],
            11223: [True, deque()]
        }

        # Mock create_match_interaction
        self.danisen.create_match_interaction = AsyncMock()

        # Run matchmaking
        await self.danisen.matchmake(self.ctx.interaction)

        # Assertions
        self.danisen.create_match_interaction.assert_called_once_with(self.ctx.interaction, player1, player3)
        self.assertFalse(self.danisen.in_queue[12345][0])  # Player1 is no longer in queue
        self.assertFalse(self.danisen.in_queue[11223][0])  # Player3 is no longer in queue
        self.assertTrue(self.danisen.in_match[12345])  # Player1 is in a match
        self.assertTrue(self.danisen.in_match[11223])  # Player3 is in a match
        self.assertTrue(self.danisen.in_queue[67890][0])  # Player2 remains in the queue

    async def test_score_update_special_rank_rules(self):
        """Test special rank up rules for high-ranked players."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 8"), MagicMock(name="Dan 9")]

        # Mock config to enable special rank rules
        mock_config = {"special_rank_up_rules": True}
        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(mock_config))):
            with patch("os.path.exists", return_value=True):
                self.danisen.update_config()

        # Test case 1: High-rank player vs high-rank player (should allow rankup)
        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 8, "points": 4, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 8, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)
        self.assertEqual(winner_rank, [9, 0], "High-rank player should rank up when beating another high-rank player")

        # Test case 2: High-rank player vs low-rank player (should not allow rankup)
        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 8, "points": 4, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 4, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)
        self.assertEqual(winner_rank, [8, 4], "High-rank player should not rank up when beating a low-rank player")

        # Test case 3: Low-rank player vs any player (normal rules apply)
        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 4, "points": 2, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 4, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)
        self.assertEqual(winner_rank, [5, 0], "Low-rank player should follow normal rankup rules")

    async def test_score_update_special_rank_rules_disabled(self):
        """Test rank up behavior when special rank rules are disabled."""
        self.ctx.guild = MagicMock()
        self.ctx.guild.get_member = MagicMock(return_value=self.ctx.author)
        self.ctx.guild.roles = [MagicMock(name="Dan 8"), MagicMock(name="Dan 9")]

        # Mock config to disable special rank rules
        mock_config = {"special_rank_up_rules": False}
        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(mock_config))):
            with patch("os.path.exists", return_value=True):
                self.danisen.update_config()

        # High-rank player vs low-rank player (should allow rankup when rules disabled)
        winner = {"player_name": "Winner", "discord_id": 12345, "dan": 8, "points": 4, "character": "Hyde"}
        loser = {"player_name": "Loser", "discord_id": 67890, "dan": 6, "points": 0, "character": "Linne"}

        winner_rank, loser_rank = await self.danisen.score_update(self.ctx, winner, loser)
        self.assertEqual(winner_rank, [9, 0], "High-rank player should rank up normally when special rules are disabled")

    def _make_role(self, name, position=1):
        role = MagicMock()
        role.name = name
        role.position = position
        return role

    async def test_sync_guild_roles_adds_and_removes(self):
        """Test that sync_guild_roles reconciles stale/missing roles against the database."""
        self.mock_database_response(fetchall=[
            {"discord_id": 111, "character": "Hyde", "dan": 2}
        ])

        stale_dan_role = self._make_role("Dan 1")
        stale_char_role = self._make_role("Linne")
        dan2_role = self._make_role("Dan 2")
        hyde_role = self._make_role("Hyde")

        member = MagicMock()
        member.id = 111
        member.name = "TestPlayer"
        member.roles = [stale_dan_role, stale_char_role]
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()

        bot_member = MagicMock()
        bot_member.top_role.position = 10
        bot_member.guild_permissions.manage_roles = True

        self.bot.user.id = 999
        guild = MagicMock()
        guild.members = [member]
        guild.roles = [stale_dan_role, stale_char_role, dan2_role, hyde_role]
        guild.get_member = MagicMock(return_value=bot_member)

        result = await self.danisen.sync_guild_roles(guild)

        self.assertEqual(result, {"added": 2, "removed": 2, "skipped_members": 0})
        member.add_roles.assert_called_once()
        self.assertEqual(set(member.add_roles.call_args.args), {dan2_role, hyde_role})
        member.remove_roles.assert_called_once()
        self.assertEqual(set(member.remove_roles.call_args.args), {stale_dan_role, stale_char_role})

    async def test_sync_guild_roles_skips_when_bot_cannot_manage_role(self):
        """Test that sync_guild_roles leaves roles alone when the bot lacks permission to manage them."""
        self.mock_database_response(fetchall=[
            {"discord_id": 111, "character": "Hyde", "dan": 2}
        ])

        stale_dan_role = self._make_role("Dan 1")
        dan2_role = self._make_role("Dan 2")
        hyde_role = self._make_role("Hyde")

        member = MagicMock()
        member.id = 111
        member.name = "TestPlayer"
        member.roles = [stale_dan_role]
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()

        bot_member = MagicMock()
        bot_member.top_role.position = 1  # Too low to manage any of these roles
        bot_member.guild_permissions.manage_roles = True

        self.bot.user.id = 999
        guild = MagicMock()
        guild.members = [member]
        guild.roles = [stale_dan_role, dan2_role, hyde_role]
        guild.get_member = MagicMock(return_value=bot_member)

        result = await self.danisen.sync_guild_roles(guild)

        self.assertEqual(result, {"added": 0, "removed": 0, "skipped_members": 1})
        member.add_roles.assert_not_called()
        member.remove_roles.assert_not_called()

    async def test_sync_roles_command_reports_summary(self):
        """Test that the /sync_roles command defers, syncs, and reports a summary."""
        self.danisen.sync_guild_roles = AsyncMock(return_value={"added": 3, "removed": 1, "skipped_members": 0})
        self.ctx.guild = MagicMock()

        await self.danisen.sync_roles(self.ctx)

        self.ctx.defer.assert_called_once()
        self.danisen.sync_guild_roles.assert_called_once_with(self.ctx.guild)
        self.ctx.respond.assert_called_with(
            "Role sync complete. Added 3 role(s), removed 1 role(s)."
        )
