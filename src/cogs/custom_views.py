import discord
import logging
class MatchSelect(discord.ui.Select):
    def __init__(self, bot, p1, p2):
        self.p1 = p1
        self.p2 = p2
        self.bot = bot

        # Create and configure logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        options = [
            discord.SelectOption(
                label=f"{p1['player_name']} {p1['character']}",
                description=f"{p1['player_name']} victory!"
            ),
            discord.SelectOption(
                label=f"{p2['player_name']} {p2['character']}",
                description=f"{p2['player_name']} victory!"
            ),
            discord.SelectOption(
                label="Cancel",
                description="Cancel the match"
            )
        ]
    
        super().__init__(
            placeholder = "Report match winner",
            min_values = 1,
            max_values = 1,
            options = options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        valid_ids = [self.p1['discord_id'], self.p2['discord_id']]

        if interaction.user.id not in valid_ids and not interaction.user.guild_permissions.administrator:
            return
        self.disabled=True
        self.view.disable_all_items()
        self.view.stop()

        self.logger.info("Match has been reported")

        self.bot.cur_active_matches -= 1
        self.logger.info(f"cur_active_matches reduced {self.bot.cur_active_matches}")

        #remove players from match dict
        self.bot.in_match[self.p1['discord_id']] = False
        self.bot.in_match[self.p2['discord_id']] = False

        if self.values[0] == "Cancel":
            self.logger.info(f"Match has been cancelled between {self.p1['player_name']} and {self.p2['player_name']}")
            await interaction.respond(f"Match has been cancelled <@{self.p1['discord_id']}> <@{self.p2['discord_id']}> will be not readded to queue")
            await interaction.message.delete()
            return
        elif self.values[0] == f"{self.p1['player_name']} {self.p1['character']}":
            await self.bot.report_match_queue(interaction, self.p1, self.p2, "player1")
        else:
            await self.bot.report_match_queue(interaction, self.p1, self.p2, "player2")
        
        if self.p1['requeue']:
            self.bot.rejoin_queue(self.p1)
        if self.p2['requeue']:
            self.bot.rejoin_queue(self.p2)

        await self.bot.matchmake(interaction)


        await interaction.message.delete()

class MatchView(discord.ui.View):
    def __init__(self, bot, p1, p2):
        super().__init__(timeout=None)
        self.p1 = p1
        self.p2 = p2
        self.add_item(MatchSelect(bot, p1, p2))
