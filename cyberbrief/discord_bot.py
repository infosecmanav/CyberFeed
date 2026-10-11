import asyncio, os
import discord
from discord.ext import commands
from .engine import build
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
@bot.event
async def on_ready():
    await bot.tree.sync()
@bot.tree.command(name="briefing", description="Read the daily or weekly security briefing")
async def briefing(interaction: discord.Interaction, period: str = "daily"):
    if period not in ("daily", "weekly"):
        await interaction.response.send_message("Choose daily or weekly.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        data = await asyncio.to_thread(build, period, False, os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL"))
        if not data["stories"]: await interaction.followup.send("No recent stories available. Some feeds may be unavailable.", ephemeral=True)
        for s in data["stories"]:
            embed = discord.Embed(title=s["title"][:256], url=s["url"], description=s["summary"][:2000], color=0x70ddbb)
            embed.set_footer(text=s["source"] + " • " + s["published"][:10])
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        await interaction.followup.send("Briefing unavailable; try again later.", ephemeral=True)
if __name__ == "__main__": bot.run(os.environ["DISCORD_BOT_TOKEN"])
