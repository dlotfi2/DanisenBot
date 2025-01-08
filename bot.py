import discord
import sqlite3
from cogs.danisen import *
import json
import os

def create_bot():
    config_path = "config.json"

    intents = discord.Intents.default()
    intents.members = True

    bot = discord.Bot(intents=intents)
    con = sqlite3.connect("danisen.db")

    bot.add_cog(Danisen(bot,con,config_path))

    @bot.event
    async def on_ready():
        print(f'We have logged in as {bot.user}')
    
    return bot

def update_bot_config(bot):
    danisen = bot.get_cog("Danisen")
    danisen.update_config()