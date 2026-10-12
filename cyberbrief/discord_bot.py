import asyncio
import logging
import os
from io import BytesIO
from typing import Literal, Optional
import discord
from discord.ext import commands
from .engine import build
from .preferences import get_format, set_format
from .reports import text_messages, pdf_report

logger = logging.getLogger(__name__)
build_lock = asyncio.Lock()


class BriefingBot(commands.Bot):
    async def setup_hook(self):
        # Sync once per startup, rather than on every gateway reconnect.
        await self.tree.sync()


bot = BriefingBot(command_prefix='!', intents=discord.Intents.default())


@bot.tree.command(name='briefing_settings', description='Save your preferred report format')
async def briefing_settings(interaction: discord.Interaction, format: Literal['text', 'pdf']):
    try:
        await asyncio.to_thread(set_format, interaction.user.id, format)
        await interaction.response.send_message(f'Your default report format is now {format.upper()}.', ephemeral=True)
    except Exception:
        logger.error('Could not save report preference')
        await interaction.response.send_message('Could not save your preference. Please try again.', ephemeral=True)


@bot.tree.command(name='briefing', description='Get a daily or weekly cyber briefing in your preferred format')
async def briefing(interaction: discord.Interaction, period: Literal['daily', 'weekly'] = 'daily', format: Optional[Literal['text', 'pdf']] = None):
    if build_lock.locked():
        await interaction.response.send_message('A briefing is building. Please try again shortly.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    async with build_lock:
        try:
            selected = format or await asyncio.to_thread(get_format, interaction.user.id)
            data = await asyncio.to_thread(build, period, False, os.getenv('OPENAI_API_KEY'), os.getenv('OPENAI_MODEL'))
            if selected == 'pdf':
                report = await asyncio.to_thread(pdf_report, data)
                name = f"signal-{period}-{data['generated'][:10]}.pdf"
                await interaction.followup.send('Your cyber briefing is attached.', file=discord.File(BytesIO(report), filename=name), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                messages = text_messages(data)
                # Discord limits user-installed apps to five followups. Attach
                # overflow as text instead of failing halfway through a report.
                if len(messages) > 5:
                    payload = '\n\n'.join(messages).encode('utf-8')
                    await interaction.followup.send('Your full text briefing is attached.', file=discord.File(BytesIO(payload), filename=f'signal-{period}.txt'), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
                else:
                    for message in messages:
                        await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.error('Briefing generation or delivery failed')
            await interaction.followup.send('Briefing unavailable; please try again later.', ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


if __name__ == '__main__':
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        raise SystemExit('Set DISCORD_BOT_TOKEN securely before starting the bot.')
    bot.run(token)
