import discord, sqlite3, json, logging
from discord.ext import commands, pages
from cogs.database import *
from cogs.custom_views import *
import os
from collections import deque
from constants import *

class Danisen(commands.Cog):
    # Predefined characters and players
    characters = ["Hyde", "Linne", "Waldstein", "Carmine", "Orie", "Gordeau", "Merkava", "Vatista", "Seth", "Yuzuriha", "Hilda", "Chaos", "Nanase", "Byakuya", "Phonon", "Mika", "Wagner", "Enkidu", "Londrekia", "Tsurugi", "Kaguya", "Kuon", "Uzuki", "Eltnum", "Akatsuki", "Ogre", "Izumi", "Zohar"]
    players = ["player1", "player2"]
    dan_colours = [
        discord.Colour.from_rgb(255, 255, 255), discord.Colour.from_rgb(255, 255, 0), discord.Colour.from_rgb(255, 153, 0),
        discord.Colour.from_rgb(39, 78, 19), discord.Colour.from_rgb(97, 0, 162), discord.Colour.from_rgb(0, 0, 177), discord.Colour.from_rgb(120, 63, 4),
        # SPECIAL RANKS
        discord.Colour.from_rgb(0, 0, 255), discord.Colour.from_rgb(120, 63, 4), discord.Colour.from_rgb(255, 0, 0), discord.Colour.from_rgb(152, 0, 0), discord.Colour.from_rgb(0, 0, 0)
    ]

    def __init__(self, bot, database, config_path):
        # Initialize the cog
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        self.bot = bot
        self.config_path = config_path
        self.update_config()

        # Database setup
        self.database_con = database
        self.database_con.row_factory = sqlite3.Row
        self.database_cur = self.database_con.cursor()
        self.database_cur.execute("CREATE TABLE IF NOT EXISTS players(discord_id, player_name, character, dan, points, PRIMARY KEY (discord_id, character))")

        # Queue and matchmaking setup
        # Keyed over the fixed MAX_DAN_RANK range (not the configurable total_dans) so that
        # changing total_dans at runtime via /set_config can never leave a dan without a queue.
        self.dans_in_queue = {dan: deque() for dan in range(1, MAX_DAN_RANK + 1)}
        self.matchmaking_queue = deque()
        self.max_active_matches = 3
        self.cur_active_matches = 0
        self.recent_opponents_limit = 2
        self.in_queue = {}  # Format: discord_id: [in_queue, deque of last played discord_ids]
        self.in_match = {}  # Format: discord_id: in_match

    def can_manage_role(self, bot_member, role):
        # Check if the bot can manage a specific role
        return bot_member.top_role.position > role.position and bot_member.guild_permissions.manage_roles

    def update_config(self):
        # Load configuration from the config file
        config = {}  # Initialize config as an empty dictionary
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
        except Exception as e:
            self.logger.warning(f"Failed to load configuration: {str(e)}")  # Fix logging issue

        # Set all configuration values
        self.ACTIVE_MATCHES_CHANNEL_ID = int(config.get('ACTIVE_MATCHES_CHANNEL_ID', 0))
        self.REPORTED_MATCHES_CHANNEL_ID = int(config.get('REPORTED_MATCHES_CHANNEL_ID', 0))
        # Clamped to MAX_DAN_RANK since dans_in_queue and dan_colours are both sized to it
        self.total_dans = max(1, min(config.get('total_dans', MAX_DAN_RANK), MAX_DAN_RANK))
        self.minimum_derank = config.get('minimum_derank', DEFAULT_DAN)
        self.maximum_rank_difference = config.get('maximum_rank_difference', 2)
        self.rank_gap_for_more_points = config.get('rank_gap_for_more_points', 1)
        self.point_rollover = config.get('point_rollover', True)
        self.queue_status = config.get('queue_status', True)
        self.recent_opponents_limit = config.get('recent_opponents_limit', 2)
        self.max_active_matches = config.get('max_active_matches', 3)  # New parameter
        self.special_rank_up_rules = config.get('special_rank_up_rules', False)

    @discord.commands.slash_command(description="Close or open the MM queue (admin debug cmd)")
    @discord.commands.default_permissions(manage_roles=True)
    async def set_queue(self, ctx: discord.ApplicationContext, queue_status: discord.Option(bool)):
        # Enable or disable the matchmaking queue
        self.queue_status = queue_status
        if not queue_status:
            self.matchmaking_queue.clear()  # Clear the deque
            self.dans_in_queue = {dan: deque() for dan in range(1, MAX_DAN_RANK + 1)}  # Reset to empty deques
            self.in_queue = {}
            self.in_match = {}
            await ctx.respond("The matchmaking queue has been disabled")
        else:
            await ctx.respond("The matchmaking queue has been enabled")

    def dead_role(self, ctx, player):
        # Check if a player's dan role should be removed
        role = None
        self.logger.info(f'Checking if dan should be removed as well')
        res = self.database_cur.execute(
            "SELECT * FROM players WHERE discord_id=? AND dan=?",
            (player['discord_id'], player['dan'])
        )
        remaining_daniel = res.fetchone()
        if not remaining_daniel:
            self.logger.info(f"Dan role {player['dan']} will be removed")
            role = discord.utils.get(ctx.guild.roles, name=f"Dan {player['dan']}")
        return role

    async def sync_guild_roles(self, guild):
        """Reconciles every member's dan/character roles against the database (the source of truth).

        Handles drift from things like a season reset (dan/points changed in the DB without
        touching roles) or a role removal that silently failed earlier due to the bot's role
        being too low at the time. Only adds/removes roles this bot is able to manage; anything
        it can't touch is counted in 'skipped_members' rather than attempted.
        """
        rows = self.database_cur.execute("SELECT discord_id, character, dan FROM players").fetchall()
        expected_by_member = {}
        for row in rows:
            entry = expected_by_member.setdefault(row['discord_id'], {'dans': set(), 'chars': set()})
            entry['dans'].add(row['dan'])
            entry['chars'].add(row['character'])

        managed_role_names = {f"Dan {dan}" for dan in range(1, MAX_DAN_RANK + 1)} | set(self.characters)
        bot_member = guild.get_member(self.bot.user.id)

        self.logger.info(f"Starting role sync for guild '{guild.name}' ({guild.id}), {len(guild.members)} member(s)")

        added = 0
        removed = 0
        skipped_members = 0

        for member in guild.members:
            entry = expected_by_member.get(member.id, {'dans': set(), 'chars': set()})
            expected_names = {f"Dan {dan}" for dan in entry['dans']} | entry['chars']

            current_managed_roles = [role for role in member.roles if role.name in managed_role_names]
            current_names = {role.name for role in current_managed_roles}

            roles_to_add = [
                role for name in (expected_names - current_names)
                if (role := discord.utils.get(guild.roles, name=name))
            ]
            roles_to_remove = [role for role in current_managed_roles if role.name not in expected_names]

            manageable_add = [role for role in roles_to_add if self.can_manage_role(bot_member, role)]
            manageable_remove = [role for role in roles_to_remove if self.can_manage_role(bot_member, role)]

            if len(manageable_add) < len(roles_to_add) or len(manageable_remove) < len(roles_to_remove):
                skipped_members += 1
                self.logger.warning(f"Could not fully sync roles for {member.name} ({member.id}) due to bot's role being too low")

            if manageable_add:
                self.logger.info(f"Adding roles {[r.name for r in manageable_add]} to {member.name} ({member.id})")
                await member.add_roles(*manageable_add)
                added += len(manageable_add)
            if manageable_remove:
                self.logger.info(f"Removing roles {[r.name for r in manageable_remove]} from {member.name} ({member.id})")
                await member.remove_roles(*manageable_remove)
                removed += len(manageable_remove)

        self.logger.info(
            f"Role sync finished for guild '{guild.name}' ({guild.id}): "
            f"added {added}, removed {removed}, skipped {skipped_members} member(s)"
        )
        return {"added": added, "removed": removed, "skipped_members": skipped_members}

    async def score_update(self, ctx, winner, loser):
        # Update scores for a match
        winner_rank = [winner['dan'], winner['points']]
        loser_rank = [loser['dan'], loser['points']]
        rankdown = False
        rankup = False

        # Determine rankup points based on rank type
        rankup_points = RANKUP_POINTS_SPECIAL if winner_rank[0] >= SPECIAL_RANK_THRESHOLD else RANKUP_POINTS_NORMAL

        # Winning logic
        if winner_rank[0] > loser_rank[0] + self.maximum_rank_difference:
            return winner_rank, loser_rank

        if loser_rank[0] >= winner_rank[0] + self.rank_gap_for_more_points:
            winner_rank[1] += 2  # Lower-ranked player gains 2 points
        else:
            winner_rank[1] += 1  # Higher-ranked player gains 1 point

        # Losing logic
        if loser_rank[0] > self.minimum_derank:
            loser_rank[1] -= 1
        elif loser_rank[1] > 0:
            loser_rank[1] -= 1

        # Rankup logic with special rules
        if winner_rank[1] >= rankup_points:
            can_rankup = True
            
            # Check special rank rules
            if self.special_rank_up_rules and winner_rank[0] >= SPECIAL_RANK_THRESHOLD:
                # Can only rank up by beating another high-ranked player
                can_rankup = loser_rank[0] >= SPECIAL_RANK_THRESHOLD
                if not can_rankup:
                    # Reset points to rankup_points - 1 if can't rank up
                    winner_rank[1] = rankup_points - 1
            
            if can_rankup:
                winner_rank[0] += 1
                winner_rank[1] = winner_rank[1] % rankup_points if self.point_rollover else 0
                rankup = True

        # Rankdown logic
        if loser_rank[1] <= RANKDOWN_POINTS:
            loser_rank[0] -= 1
            loser_rank[1] = DEFAULT_POINTS
            rankdown = True

        # Log new scores
        self.logger.info("New Scores")
        self.logger.info(f"Winner : {winner['player_name']} dan {winner_rank[0]}, points {winner_rank[1]}")
        self.logger.info(f"Loser : {loser['player_name']} dan {loser_rank[0]}, points {loser_rank[1]}")

        # Update database
        self.database_cur.execute(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (winner_rank[0], winner_rank[1], winner['player_name'], winner['character'])
        )
        self.database_cur.execute(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (loser_rank[0], loser_rank[1], loser['player_name'], loser['character'])
        )
        self.database_con.commit()

        # Update roles on rankup/down
        if rankup:
            role = discord.utils.get(ctx.guild.roles, name=f"Dan {winner_rank[0]}")
            member = ctx.guild.get_member(winner['discord_id'])
            bot_member = ctx.guild.get_member(self.bot.user.id)
            if role and self.can_manage_role(bot_member, role):
                await member.add_roles(role)
            role = self.dead_role(ctx, winner)
            if role and self.can_manage_role(bot_member, role):
                await member.remove_roles(role)

        if rankdown:
            member = ctx.guild.get_member(loser['discord_id'])
            role = self.dead_role(ctx, loser)
            if role:
                await member.remove_roles(role)

        return winner_rank, loser_rank

    # Custom decorator for validation
    def is_valid_char(self, char):
        return char in self.characters

    async def character_autocomplete(self, ctx: discord.AutocompleteContext):
        return [character for character in self.characters if character.lower().startswith(ctx.value.lower())]

    async def player_autocomplete(self, ctx: discord.AutocompleteContext):
        res = self.database_cur.execute("SELECT player_name FROM players")
        name_list=res.fetchall()
        names = set([name[0] for name in name_list])
        return [name for name in names if (name.lower()).startswith(ctx.value.lower())]

    @discord.commands.slash_command(description="set a players rank (admin debug cmd)")
    @discord.commands.default_permissions(manage_roles=True)
    async def set_rank(self, ctx : discord.ApplicationContext,
                        player_name :  discord.Option(str, autocomplete=player_autocomplete),
                        char : discord.Option(str, autocomplete=character_autocomplete),
                        dan :  discord.Option(int),
                        points : discord.Option(int)):
        if not self.is_valid_char(char):
            await ctx.respond(f"Invalid char selected {char}. Please choose a valid char.")
            return
        self.database_cur.execute(
            "UPDATE players SET dan = ?, points = ? WHERE player_name=? AND character=?",
            (dan, points, player_name, char)
        )
        self.database_con.commit()
        await ctx.respond(f"{player_name}'s {char} rank updated to be dan {dan} points {points}")

    @discord.commands.slash_command(description="Reconcile every member's dan/character roles with the database (admin cmd)")
    @discord.commands.default_permissions(manage_roles=True)
    async def sync_roles(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        self.logger.info(f"/sync_roles invoked by {ctx.author.name} ({ctx.author.id}) in guild '{ctx.guild.name}' ({ctx.guild.id})")
        result = await self.sync_guild_roles(ctx.guild)

        message = f"Role sync complete. Added {result['added']} role(s), removed {result['removed']} role(s)."
        if result['skipped_members']:
            message += f" Could not fully sync {result['skipped_members']} member(s) due to the bot's role being too low."
        await ctx.respond(message)

    @discord.commands.slash_command(description="help msg")
    async def help(self, ctx : discord.ApplicationContext):
        em = discord.Embed(
            title="Help",
            description="list of all commands",
            color=discord.Color.blurple())
        if self.bot.user.avatar.url:
            em.set_thumbnail(
                url=self.bot.user.avatar.url)

        for slash_command in self.walk_commands():
            em.add_field(name=slash_command.name, 
                        value=slash_command.description if slash_command.description else slash_command.name, 
                        inline=False) 
                        # fallbacks to the command name incase command description is not defined

        await ctx.send_response(embed=em)
    #registers player+char to db
    @discord.commands.slash_command(description="Register to the Danisen database!")
    async def register(self, ctx: discord.ApplicationContext,
                       char1: discord.Option(str, autocomplete=character_autocomplete)):
        player_name = ctx.author.name

        if not self.is_valid_char(char1):
            await ctx.respond(f"Invalid char selected {char1}. Please choose a valid char.")
            return

        # Check if the player is already registered with the character
        res = self.database_cur.execute(
            "SELECT * FROM players WHERE discord_id = ? AND character = ?",
            (ctx.author.id, char1)
        ).fetchone()

        if res:
            await ctx.respond(f"You are already registered with the character {char1}.")
            return

        # Insert the new player record
        line = (ctx.author.id, player_name, char1, DEFAULT_DAN, DEFAULT_POINTS)
        self.database_cur.execute(
            "INSERT INTO players (discord_id, player_name, character, dan, points) VALUES (?, ?, ?, ?, ?)", 
            line
        )
        self.database_con.commit()

        role_list = []
        char_role = discord.utils.get(ctx.guild.roles, name=char1)
        if char_role:
            role_list.append(char_role)
        self.logger.info(f"Adding to db {player_name} {char1}")

        dan_role = discord.utils.get(ctx.guild.roles, name="Dan 1")
        if dan_role:
            role_list.append(dan_role)

        bot_member = ctx.guild.get_member(self.bot.user.id)
        can_add_roles = all(self.can_manage_role(bot_member, role) for role in role_list)
        if can_add_roles:
            await ctx.author.add_roles(*role_list)
        else:
            self.logger.warning("Could not add roles due to bot's role being too low")

        await ctx.respond(
            f"You are now registered as {player_name} with the following character/s {char1}\n"
            "If you wish to add more characters, you can register multiple times!\n\n"
            "Welcome to the Danisen!"
        )

    @discord.commands.slash_command(description="unregister to the Danisen database!")
    async def unregister(self, ctx : discord.ApplicationContext, 
                    char1 : discord.Option(str, autocomplete=character_autocomplete)):

        if not self.is_valid_char(char1):
            await ctx.respond(f"Invalid char selected {char1}. Please choose a valid char.")
            return

        # Check if the player is in a match
        if ctx.author.name in self.in_match and self.in_match[ctx.author.name]:
            await ctx.respond("You cannot unregister while in an active match.")
            return

        # Check if the player is in the queue
        if ctx.author.name in self.in_queue and self.in_queue[ctx.author.name][0]:
            await ctx.respond("You cannot unregister while in the queue. Please leave the queue first.")
            return

        res = self.database_cur.execute(
            "SELECT * FROM players WHERE discord_id=? AND character=?",
            (ctx.author.id, char1)
        )
        daniel = res.fetchone()

        if daniel == None:
            await ctx.respond("You are not registered with that character")
            return

        self.logger.info(f"Removing {ctx.author.name} {ctx.author.id} {char1} from db")
        self.database_cur.execute(
            "DELETE FROM players WHERE discord_id=? AND character=?",
            (ctx.author.id, char1)
        )
        self.database_con.commit()

        role_list = []
        char_role = discord.utils.get(ctx.guild.roles, name=char1)
        if char_role:
            role_list.append(discord.utils.get(ctx.guild.roles, name=char1))
        self.logger.info(f"Removing role {char1} from member")

        role = self.dead_role(ctx, daniel)
        if role:
            role_list.append(role)

        bot_member = ctx.guild.get_member(self.bot.user.id)
        can_remove_roles = True
        message_text = ""
        if role_list:
            self.logger.info(f"{role_list}")
            for role in role_list:
                can_remove_roles = can_remove_roles and self.can_manage_role(bot_member,role)
            if can_remove_roles:
                await ctx.author.remove_roles(*role_list)
            else:
                message_text += f"Could not remove roles due to bot's role being too low\n\n"
                self.logger.warning(f"Could not remove roles due to bot's role being too low")
        message_text += f"You have now unregistered {char1}"
        await ctx.respond(message_text)

    #rank command to get discord_name's player rank, (can also ignore 2nd param for own rank)
    @discord.commands.slash_command(description="Get your character rank/Put in a players name to get their character rank!")
    async def rank(self, ctx : discord.ApplicationContext,
                char : discord.Option(str, autocomplete=character_autocomplete),
                discord_name :  discord.Option(str, autocomplete=player_autocomplete)):
        if not self.is_valid_char(char):
            await ctx.respond(f"Invalid char selected {char}. Please choose a valid char.")
            return

        members = ctx.guild.members
        member = None
        for m in members:
            if discord_name.lower() == m.name.lower():
                member = m
                break
        if discord_name:
            if not member:
                await ctx.respond(f"""{discord_name} isn't a member of this server""")
                return
        else:
            member = ctx.author
        id = member.id

        res = self.database_cur.execute(
            "SELECT * FROM players WHERE discord_id=? AND character=?",
            (id, char)
        )
        data = res.fetchone()
        if data:
            await ctx.respond(f"""{data['player_name']}'s rank for {char} is {data['dan']} dan {data['points']} points""")
        else:
            await ctx.respond(f"""{member.name} is not registered as {char} so you have no rank...""")

    #leaves the matchmaking queue
    @discord.commands.slash_command(description="leave the danisen queue")
    async def leave_queue(self, ctx : discord.ApplicationContext):
        discord_id = ctx.author.id
        self.logger.info(f"{ctx.author.name} requested to leave the queue")
        daniel = None 

        for member in self.matchmaking_queue:
            if member and (member['discord_id'] == discord_id):
                self.matchmaking_queue.remove(member)
                daniel = member
                break

        if daniel:
            if daniel in self.dans_in_queue[daniel['dan']]:
                self.dans_in_queue[daniel['dan']].remove(daniel)

            self.in_queue[daniel['discord_id']][0] = False
            await ctx.respond("You have been removed from the queue")
        else:
            await ctx.respond("You are not in queue")

    #joins the matchmaking queue
    @discord.commands.slash_command(description="queue up for danisen games")
    async def join_queue(self, ctx : discord.ApplicationContext,
                    char: discord.Option(str, autocomplete=character_autocomplete),
                    rejoin_queue: discord.Option(bool)):
        await ctx.defer()
        discord_id = ctx.author.id

        if not self.is_valid_char(char):
            await ctx.respond(f"Invalid char selected {char}. Please choose a valid char.")
            return

        #check if q open
        if self.queue_status == False:
            await ctx.respond(f"The matchmaking queue is currently closed")
            return

        #Check if valid character
        res = self.database_cur.execute(
            "SELECT * FROM players WHERE discord_id=? AND character=?",
            (discord_id, char)
        )
        daniel = res.fetchone()
        if daniel == None:
            await ctx.respond(f"You are not registered with that character")
            return

        daniel = DanisenRow(daniel)
        daniel['requeue'] = rejoin_queue

        #Check if in Queue already
        if discord_id in self.in_queue and self.in_queue[discord_id][0]:
            await ctx.respond(f"You are already in the queue")
            return

        #check if in a match already
        if discord_id in self.in_match and self.in_match[discord_id]:
            await ctx.respond(f"You are in an active match and cannot queue up")
            return

        self.in_queue.setdefault(discord_id, [True, deque(maxlen=self.recent_opponents_limit)])
        self.in_match.setdefault(discord_id, False)

        self.dans_in_queue[daniel['dan']].append(daniel)
        self.matchmaking_queue.append(daniel)
        await ctx.respond(f"You've been added to the matchmaking queue with {char}")

        #matchmake
        if (self.cur_active_matches < self.max_active_matches and
            len(self.matchmaking_queue) >= 2):
            await self.matchmake(ctx.interaction)

    def rejoin_queue(self, player):
        if self.queue_status == False:
            return

        res = self.database_cur.execute(
            "SELECT * FROM players WHERE discord_id=? AND character=?",
            (player['discord_id'], player['character'])
        )
        db_player = res.fetchone()
        if not db_player:
            return  # Exit if the player is not found in the database

        player = DanisenRow(db_player)  # Transform the database row into a DanisenRow
        player['requeue'] = True

        # Ensure the player is initialized in self.in_queue
        if player['discord_id'] not in self.in_queue:
            self.in_queue[player['discord_id']] = [False, deque(maxlen=self.recent_opponents_limit)]

        self.in_queue[player['discord_id']][0] = True
        self.dans_in_queue[player['dan']].append(player)  # Append the transformed player
        self.matchmaking_queue.append(player)

    @discord.commands.slash_command(description="view players in the queue")
    async def view_queue(self, ctx : discord.ApplicationContext):
        await ctx.respond(f"Current full MMQ {self.matchmaking_queue}\nCurrent full DanQ {self.dans_in_queue}")

    async def matchmake(self, ctx: discord.Interaction):
        while (self.cur_active_matches < self.max_active_matches and
               len(self.matchmaking_queue) >= 2):
            self.logger.debug(f"Starting matchmaking loop. Current matchmaking_queue: {list(self.matchmaking_queue)}")
            self.logger.debug(f"Current dans_in_queue: { {dan: list(queue) for dan, queue in self.dans_in_queue.items()} }")

            daniel1 = self.matchmaking_queue.popleft()  # Pop from the left of the deque
            self.logger.debug(f"Dequeued daniel1 from matchmaking_queue: {daniel1}")

            if not daniel1:
                self.logger.warning("Dequeued daniel1 is None. Skipping iteration.")
                continue

            self.in_queue[daniel1['discord_id']][0] = False

            same_daniel = self.dans_in_queue[daniel1['dan']].popleft()  # Pop from the left of the deque
            self.logger.debug(f"Dequeued same_daniel from dans_in_queue[{daniel1['dan']}]: {same_daniel}")

            # Sanity check that this is also the latest daniel in the respective dan queue
            if daniel1 != same_daniel:
                self.logger.error(f"Queue desynchronization detected: daniel1={daniel1} same_daniel={same_daniel}")
                self.logger.debug(f"Remaining matchmaking_queue: {list(self.matchmaking_queue)}")
                self.logger.debug(f"Remaining dans_in_queue[{daniel1['dan']}]: {list(self.dans_in_queue[daniel1['dan']])}")
                return

            check_dan = [daniel1['dan']]
            for dan_offset in range(1, self.total_dans):
                cur_dan = check_dan[0] + dan_offset
                if DEFAULT_DAN <= cur_dan <= self.total_dans:
                    check_dan.append(cur_dan)
                cur_dan = check_dan[0] - dan_offset
                if DEFAULT_DAN <= cur_dan <= self.total_dans:
                    check_dan.append(cur_dan)

            old_daniels = []  # List to track multiple old_daniel instances
            matchmade = False
            for dan in check_dan:
                self.logger.debug(f"Checking dan queue for dan {dan}: {list(self.dans_in_queue[dan])}")
                while self.dans_in_queue[dan]:  # Continue checking the same dan queue
                    daniel2 = self.dans_in_queue[dan].popleft()
                    self.logger.debug(f"Dequeued daniel2 from dans_in_queue[{dan}]: {daniel2}")

                    if daniel2['discord_id'] in self.in_queue[daniel1['discord_id']][1]:
                        self.logger.debug(f"Skipping daniel2 {daniel2} as they are in daniel1's recent opponents.")
                        old_daniels.append(daniel2)
                        continue

                    self.in_queue[daniel2['discord_id']] = [False, deque([daniel1['discord_id']], maxlen=self.recent_opponents_limit)]
                    self.in_queue[daniel1['discord_id']][1].append(daniel2['discord_id'])

                    # Clean up the main queue for players that have already been matched
                    for idx in reversed(range(len(self.matchmaking_queue))):
                        player = self.matchmaking_queue[idx]
                        if player and (player['discord_id'] == daniel2['discord_id']):
                            self.logger.debug(f"Removing matched player {player} from matchmaking_queue.")
                            self.matchmaking_queue[idx] = None

                    self.in_match[daniel1['discord_id']] = True
                    self.in_match[daniel2['discord_id']] = True
                    matchmade = True
                    await self.create_match_interaction(ctx, daniel1, daniel2)
                    break

                if matchmade:
                    break

            # Re-add all skipped old_daniels back into the queue
            for old_daniel in old_daniels:
                self.logger.debug(f"Re-adding skipped daniel {old_daniel} back to dans_in_queue[{old_daniel['dan']}].")
                self.dans_in_queue[old_daniel['dan']].appendleft(old_daniel)
                self.in_queue[old_daniel['discord_id']][0] = True

            if not matchmade:
                self.logger.debug(f"No match found for daniel1 {daniel1}. Re-adding to queues.")
                self.matchmaking_queue.appendleft(daniel1)  # Append back to the deque
                self.dans_in_queue[daniel1['dan']].appendleft(daniel1)  # Append back to the deque
                self.in_queue[daniel1['discord_id']][0] = True
                break

    async def create_match_interaction(self, ctx: discord.Interaction, daniel1, daniel2):
        self.cur_active_matches += 1
        view = MatchView(self, daniel1, daniel2)
        id1 = f"<@{daniel1['discord_id']}>"
        id2 = f"<@{daniel2['discord_id']}>"

        channel = self.bot.get_channel(self.ACTIVE_MATCHES_CHANNEL_ID)
        if channel:
            webhook_msg = await channel.send(
                f"{id1} {daniel1['character']} dan {daniel1['dan']} points {daniel1['points']} vs {id2} {daniel2['character']} dan {daniel2['dan']} points {daniel2['points']} "
                "\n Note only players in the match can report it! (and admins)",
                view=view,
            )
            await webhook_msg.pin()

            # deleting the pin added system message (checking last 5 messages incase some other stuff was posted in the channel in the meantime)
            async for message in channel.history(limit=5):
                if message.type == discord.MessageType.pins_add:
                    await message.delete()
        else:
            await ctx.respond(
                f"Could not find channel to send match message to (could be an issue with channel id {self.ACTIVE_MATCHES_CHANNEL_ID} or bot permissions)"
            )

    #report match score
    @discord.commands.slash_command(description="Report a match score")
    @discord.commands.default_permissions(send_polls=True)
    async def report_match(self, ctx: discord.ApplicationContext,
                           player1_name: discord.Option(str, autocomplete=player_autocomplete),
                           char1: discord.Option(str, autocomplete=character_autocomplete),
                           player2_name: discord.Option(str, autocomplete=player_autocomplete),
                           char2: discord.Option(str, autocomplete=character_autocomplete),
                           winner: discord.Option(str, choices=players)):
        if not self.is_valid_char(char1):
            await ctx.respond(f"Invalid char1 selected {char1}. Please choose a valid char1.")
            return
        if not self.is_valid_char(char2):
            await ctx.respond(f"Invalid char2 selected {char2}. Please choose a valid char2.")
            return

        player1 = self.get_player(player1_name, char1)
        player2 = self.get_player(player2_name, char2)

        if not player1:
            await ctx.respond(f"No player named {player1_name} with character {char1}")
            return
        if not player2:
            await ctx.respond(f"No player named {player2_name} with character {char2}")
            return

        self.logger.info(f"Reported match {player1_name} vs {player2_name} as {winner} win")
        if winner == "player1":
            winner_rank, loser_rank = await self.score_update(ctx, player1, player2)
            winner = player1_name
            loser = player2_name
        else:
            loser_rank, winner_rank = await self.score_update(ctx, player2, player1)
            winner = player2_name
            loser = player1_name

        await ctx.respond(
            f"Match has been reported as {winner}'s victory over {loser}\n"
            f"{player1_name}'s {char1} rank is now {winner_rank[0]} dan {winner_rank[1]} points\n"
            f"{player2_name}'s {char2} rank is now {loser_rank[0]} dan {loser_rank[1]} points"
        )

    #report match score for the queue
    async def report_match_queue(self, interaction: discord.Interaction, player1, player2, winner):
        if (winner == "player1") :
            winner_rank, loser_rank = await self.score_update(interaction, player1,player2)
            winner = player1['player_name']
            loser = player2['player_name']
        else:
            loser_rank, winner_rank = await self.score_update(interaction, player2,player1)
            winner = player2['player_name']
            loser = player1['player_name']

        channel = self.bot.get_channel(self.REPORTED_MATCHES_CHANNEL_ID)
        if channel:
            await channel.send(f"Match has been reported as {winner}'s victory over {loser}\n{player1['player_name']}'s {player1['character']} rank is now {winner_rank[0]} dan {winner_rank[1]} points\n{player2['player_name']}'s {player2['character']} rank is now {loser_rank[0]} dan {loser_rank[1]} points")
        else:
            self.logger.warning("No Report Matches Channel")

    @discord.commands.slash_command(description="See players in a specific dan")
    async def dan(self, ctx: discord.ApplicationContext,
                  dan: discord.Option(int, min_value=DEFAULT_DAN, max_value=MAX_DAN_RANK)):
        daniels = self.get_players_by_dan(dan)
        data = [
            {
                "name": f"{daniel['player_name']} {daniel['character']}",
                "value": f"Dan: {daniel['dan']} Points: {daniel['points']}"
            }
            for daniel in daniels
        ]
        embeds = self.create_paginated_embeds(f"Dan {dan}", data, MAX_FIELDS_PER_EMBED, colour=self.dan_colours[dan - 1])
        paginator = pages.Paginator(pages=embeds)

        await paginator.respond(ctx.interaction, ephemeral=False)

    # Add a helper function for paginated embeds
    def create_paginated_embeds(self, title, data, fields_per_page, colour=None):
        """Helper function to create paginated embeds."""
        page_list = []
        total_pages = (len(data) // fields_per_page) + 1
        items_per_page = len(data) // total_pages
        for page in range(total_pages):
            em = discord.Embed(title=f"({title} {page + 1}/{total_pages})", colour=colour)
            page_list.append(em)
            for idx in range(page * items_per_page, min((page + 1) * items_per_page, len(data))):
                em.add_field(name=f"{data[idx]['name']}", value=f"{data[idx]['value']}")
        return page_list

    # Refactor danisen_stats to use the helper function
    @discord.commands.slash_command(description="See various statistics about the danisen")
    async def danisen_stats(self, ctx: discord.ApplicationContext):
        char_count = self.database_cur.execute(
            "SELECT character AS name, COUNT(*) AS value FROM players GROUP BY character ORDER BY character"
        ).fetchall()
        dan_count = self.database_cur.execute(
            "SELECT dan AS name, COUNT(*) AS value FROM players GROUP BY dan ORDER BY dan"
        ).fetchall()

        # reformat dan count as their names are just numbers
        dan_count = [{"name": f"Dan {dan['name']}", "value": dan['value']} for dan in dan_count]
        char_pages = self.create_paginated_embeds("Character Stats", char_count, MAX_FIELDS_PER_EMBED)
        dan_pages = self.create_paginated_embeds("Dan Stats", dan_count, MAX_FIELDS_PER_EMBED, colour=discord.Color.blurple())

        paginator = pages.Paginator(pages=char_pages + dan_pages)
        await paginator.respond(ctx.interaction, ephemeral=False)

    # Refactor leaderboard to use the helper function
    @discord.commands.slash_command(description="See the top players")
    async def leaderboard(self, ctx: discord.ApplicationContext):
        daniels = self.database_cur.execute(
            "SELECT player_name || ' ' || character AS name, 'Dan: ' || dan || ' Points: ' || points AS value "
            "FROM players ORDER BY dan DESC, points DESC"
        ).fetchall()

        leaderboard_pages = self.create_paginated_embeds("Top Daniels", daniels, MAX_FIELDS_PER_EMBED)
        paginator = pages.Paginator(pages=leaderboard_pages)
        await paginator.respond(ctx.interaction, ephemeral=False)

    @discord.commands.slash_command(description="Update max matches for the queue system (Admin Cmd)")
    @discord.commands.default_permissions(manage_messages=True)
    async def update_max_matches(self, ctx : discord.ApplicationContext,
                                 max : discord.Option(int, min_value=1)):
        self.max_active_matches = max
        await ctx.respond(f"Max matches updated to {max}")
        if (self.cur_active_matches < self.max_active_matches and
        len(self.matchmaking_queue) >= 2):
            await self.matchmake(ctx.interaction)

    @discord.commands.slash_command(description="View current bot configuration (admin)")
    @discord.commands.default_permissions(manage_guild=True)
    async def view_config(self, ctx: discord.ApplicationContext):
        """Displays the current configuration loaded from the config file."""
        # Try to load the raw config file so user can see exactly what's persisted
        config = {}
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
        except Exception as e:
            self.logger.warning(f"Failed to load configuration for view: {e}")

        # Fallback to runtime attributes if some keys are missing
        merged = dict(DEFAULT_CONFIG)
        merged.update(config)
        # Also include some runtime-derived values
        merged['max_active_matches'] = self.max_active_matches
        merged['queue_status'] = self.queue_status
        merged['total_dans'] = self.total_dans

        em = discord.Embed(title="Current Configuration", color=discord.Color.blurple())
        for k, v in merged.items():
            em.add_field(name=str(k), value=str(v), inline=False)

        await ctx.respond(embed=em, ephemeral=True)

    @discord.commands.slash_command(description="Set a configuration key (admin)")
    @discord.commands.default_permissions(manage_guild=True)
    async def set_config(self, ctx: discord.ApplicationContext,
                         key: discord.Option(str, choices=[
                             "ACTIVE_MATCHES_CHANNEL_ID", "REPORTED_MATCHES_CHANNEL_ID",
                             "total_dans", "minimum_derank", "maximum_rank_difference",
                             "rank_gap_for_more_points", "point_rollover", "queue_status",
                             "recent_opponents_limit", "max_active_matches", "special_rank_up_rules"
                         ]),
                         value: discord.Option(str)):
        """Update a single configuration key and persist it to disk."""
        # Load existing config
        cfg = dict(DEFAULT_CONFIG)
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                    cfg.update(loaded)
        except Exception as e:
            self.logger.warning(f"Failed to load existing config while setting key: {e}")

        # Determine expected type from DEFAULT_CONFIG when possible
        expected = DEFAULT_CONFIG.get(key, None)

        def parse_to_expected(val_str, expected_default):
            # If DEFAULT_CONFIG provides a default, use its type
            if expected_default is not None:
                target_type = type(expected_default)
            else:
                # Fallback heuristics
                if key.upper().endswith('CHANNEL_ID') or 'CHANNEL' in key.upper():
                    target_type = str
                elif key in ('point_rollover', 'queue_status', 'special_rank_up_rules'):
                    target_type = bool
                else:
                    # default to int for most numeric-like config options
                    target_type = int

            s = val_str.strip()
            # Booleans
            if target_type is bool:
                low = s.lower()
                if low in ('true', '1', 'yes', 'on'):
                    return True
                if low in ('false', '0', 'no', 'off'):
                    return False
                # try JSON parse
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, bool):
                        return parsed
                except Exception:
                    pass
                # fallback: non-empty string => True
                return bool(s)

            # Integers
            if target_type is int:
                try:
                    return int(s)
                except Exception:
                    try:
                        parsed = json.loads(s)
                        if isinstance(parsed, (int, float)):
                            return int(parsed)
                    except Exception:
                        pass
                    # final fallback: 0
                    return 0

            # Strings
            if target_type is str:
                return s

            # Fallback: try json then return raw string
            try:
                return json.loads(s)
            except Exception:
                return s

        parsed_value = parse_to_expected(value, expected)

        # Store the parsed value
        cfg[key] = parsed_value

        # Ensure the config dir exists and write back
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(cfg, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to persist configuration: {e}")
            await ctx.respond(f"Failed to persist configuration: {e}")
            return

        # Reload runtime config
        try:
            self.update_config()
        except Exception as e:
            self.logger.warning(f"update_config failed after setting config: {e}")

        await ctx.respond(f"Configuration key `{key}` updated to `{parsed_value}`", ephemeral=True)

    def get_player(self, player_name, character):
        res = self.database_cur.execute(
            "SELECT * FROM players WHERE player_name=? AND character=?", 
            (player_name, character)
        )
        return res.fetchone()

    def get_players_by_dan(self, dan):
        res = self.database_cur.execute(
            "SELECT * FROM players WHERE dan=?", 
            (dan,)
        )
        return res.fetchall()

    @discord.commands.slash_command(description="View your profile or another player's profile")
    async def profile(self, ctx: discord.ApplicationContext, 
                      discord_name: discord.Option(str, autocomplete=player_autocomplete, default=None)):
        """Lists all registered characters for a player along with their ranks and points."""
        # Determine the target player
        if discord_name:
            members = ctx.guild.members
            member = next((m for m in members if discord_name.lower() == m.name.lower()), None)
            if not member:
                await ctx.respond(f"{discord_name} isn't a member of this server.")
                return
        else:
            member = ctx.author

        # Fetch all characters for the player
        res = self.database_cur.execute(
            "SELECT character, dan, points FROM players WHERE discord_id = ?", 
            (member.id,)
        ).fetchall()

        if not res:
            await ctx.respond(f"{member.name} has no registered characters.")
            return

        # Create an embed to display the profile
        em = discord.Embed(
            title=f"{member.name}'s Profile",
            description="List of registered characters and their ranks",
            color=discord.Color.blurple()
        )
        if member.avatar:
            em.set_thumbnail(url=member.avatar.url)

        for row in res:
            em.add_field(
                name=row["character"], 
                value=f"Dan: {row['dan']}, Points: {row['points']}", 
                inline=False
            )

        await ctx.respond(embed=em)
