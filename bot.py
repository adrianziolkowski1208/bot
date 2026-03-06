import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import quote
from urllib.request import urlopen

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from yt_dlp import YoutubeDL


LoopMode = Literal["off", "track", "queue"]
LOOP_MODE_LABELS = {
    "off": "wyłączone",
    "track": "zapętlaj piosenkę",
    "queue": "zapętlaj playlistę",
}


@dataclass
class Track:
    title: str
    url: str
    webpage_url: str
    requested_by: str
    source_label: str = "YouTube"


YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/(track|album|playlist)/")
APPLE_MUSIC_RE = re.compile(r"https?://music\.apple\.com/")
SOUNDCLOUD_RE = re.compile(r"https?://(www\.)?soundcloud\.com/")


class GuildMusicState:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.next = asyncio.Event()
        self.current: Optional[Track] = None
        self.player_task: Optional[asyncio.Task] = None
        self.loop_mode: LoopMode = "off"


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
states: dict[int, GuildMusicState] = {}
CONTROL_CHANNEL_ID: Optional[int] = None


def get_state(guild_id: int) -> GuildMusicState:
    if guild_id not in states:
        states[guild_id] = GuildMusicState()
    return states[guild_id]


def set_loop_mode(state: GuildMusicState, mode: str) -> LoopMode:
    normalized = mode.strip().lower()
    if normalized not in LOOP_MODE_LABELS:
        raise commands.CommandError("Tryby loop: off, track, queue")
    state.loop_mode = normalized  # type: ignore[assignment]
    return state.loop_mode


def next_loop_mode(current_mode: LoopMode) -> LoopMode:
    if current_mode == "off":
        return "track"
    if current_mode == "track":
        return "queue"
    return "off"


def _extract_with_ydl(query: str) -> dict:
    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info


def _read_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _metadata_from_spotify(url: str) -> str:
    oembed_url = f"https://open.spotify.com/oembed?url={quote(url, safe='')}"
    data = _read_json(oembed_url)
    title = data.get("title")
    author = data.get("author_name")
    if title and author:
        return f"{author} - {title}"
    if title:
        return title
    raise ValueError("Nie udało się pobrać metadanych Spotify")


def _metadata_from_apple_music(url: str) -> str:
    oembed_url = f"https://embed.music.apple.com/oembed?url={quote(url, safe='')}"
    data = _read_json(oembed_url)
    title = data.get("title")
    author = data.get("author_name")
    if title and author:
        return f"{author} - {title}"
    if title:
        return title
    raise ValueError("Nie udało się pobrać metadanych Apple Music")


async def ensure_voice_for_member(member: discord.Member, guild: discord.Guild) -> discord.VoiceClient:
    if not member.voice or not member.voice.channel:
        raise commands.CommandError("Musisz być na kanale głosowym.")

    voice_client = guild.voice_client
    if voice_client is None:
        voice_client = await member.voice.channel.connect()
    elif voice_client.channel != member.voice.channel:
        await voice_client.move_to(member.voice.channel)

    return voice_client


async def create_track(query: str, requested_by: str) -> Track:
    loop = asyncio.get_running_loop()

    source_label = "YouTube"
    resolved_query = query

    if SPOTIFY_RE.match(query):
        source_label = "Spotify"
        metadata_query = await loop.run_in_executor(None, _metadata_from_spotify, query)
        resolved_query = f"ytsearch:{metadata_query} official audio"
    elif APPLE_MUSIC_RE.match(query):
        source_label = "Apple Music"
        metadata_query = await loop.run_in_executor(None, _metadata_from_apple_music, query)
        resolved_query = f"ytsearch:{metadata_query} official audio"
    elif SOUNDCLOUD_RE.match(query):
        source_label = "SoundCloud"

    info = await loop.run_in_executor(None, _extract_with_ydl, resolved_query)

    return Track(
        title=info.get("title", "Nieznany utwór"),
        url=info["url"],
        webpage_url=info.get("webpage_url", query),
        requested_by=requested_by,
        source_label=source_label,
    )


def queue_preview(state: GuildMusicState) -> str:
    upcoming = list(state.queue._queue)
    if not state.current and not upcoming:
        return f"Kolejka jest pusta. Loop: **{LOOP_MODE_LABELS[state.loop_mode]}**"

    lines = [f"**Loop:** {LOOP_MODE_LABELS[state.loop_mode]}"]
    if state.current:
        lines.append(f"**Teraz gra:** {state.current.title} ({state.current.source_label})")

    if upcoming:
        lines.append("\n**W kolejce:**")
        lines.extend(
            f"{idx + 1}. {track.title} ({track.source_label})"
            for idx, track in enumerate(upcoming[:10])
        )

    return "\n".join(lines)


async def enqueue_track(
    guild: discord.Guild,
    member: discord.Member,
    query: str,
    requested_by: str,
) -> Track:
    await ensure_voice_for_member(member, guild)

    state = get_state(guild.id)
    if state.player_task is None or state.player_task.done():
        state.player_task = bot.loop.create_task(player_loop(guild.id))

    track = await create_track(query, requested_by)
    await state.queue.put(track)
    return track


async def player_loop(guild_id: int):
    await bot.wait_until_ready()
    state = get_state(guild_id)
    pending_track: Optional[Track] = None

    while True:
        state.next.clear()
        if pending_track is None:
            pending_track = await state.queue.get()
        state.current = pending_track

        guild = bot.get_guild(guild_id)
        if guild is None:
            state.current = None
            pending_track = None
            continue

        voice_client = guild.voice_client
        if voice_client is None:
            state.current = None
            pending_track = None
            continue

        source = discord.FFmpegPCMAudio(pending_track.url, **FFMPEG_OPTIONS)

        def after_playing(error: Optional[Exception]):
            if error:
                print(f"Błąd odtwarzania: {error}")
            bot.loop.call_soon_threadsafe(state.next.set)

        voice_client.play(source, after=after_playing)
        await state.next.wait()

        finished_track = pending_track
        state.current = None

        if state.loop_mode == "track":
            pending_track = finished_track
        else:
            if state.loop_mode == "queue":
                await state.queue.put(finished_track)
            pending_track = None


class PlayModal(discord.ui.Modal, title="Dodaj utwór"):
    query = discord.ui.TextInput(label="Nazwa piosenki lub link", max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            track = await enqueue_track(interaction.guild, interaction.user, str(self.query), str(interaction.user))
            state = get_state(interaction.guild.id)
            if interaction.guild.voice_client and state.current is None:
                message = f"▶️ Odtwarzam ({track.source_label}): **{track.title}**"
            else:
                message = f"➕ Dodano do kolejki ({track.source_label}): **{track.title}**"
            await interaction.followup.send(message, ephemeral=True)
        except Exception as error:
            await interaction.followup.send(f"❌ Błąd: {error}", ephemeral=True)


class ControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Dodaj / Play", style=discord.ButtonStyle.success, custom_id="music:play")
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PlayModal())

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, custom_id="music:skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.guild.voice_client is None:
            await interaction.response.send_message("Bot nie jest podłączony.", ephemeral=True)
            return
        vc = interaction.guild.voice_client
        if not vc.is_playing():
            await interaction.response.send_message("Nic teraz nie gra.", ephemeral=True)
            return
        vc.stop()
        await interaction.response.send_message("⏭️ Pominięto utwór.", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, custom_id="music:stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
            return

        state = get_state(interaction.guild.id)
        while not state.queue.empty():
            state.queue.get_nowait()
        state.loop_mode = "off"

        vc = interaction.guild.voice_client
        if vc:
            if vc.is_playing():
                vc.stop()
            await vc.disconnect()
        await interaction.response.send_message("⏹️ Zatrzymano odtwarzanie i rozłączono bota.", ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.primary, custom_id="music:queue")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
            return
        state = get_state(interaction.guild.id)
        await interaction.response.send_message(queue_preview(state), ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, custom_id="music:loop")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
            return
        state = get_state(interaction.guild.id)
        state.loop_mode = next_loop_mode(state.loop_mode)
        await interaction.response.send_message(
            f"🔁 Tryb loop: **{LOOP_MODE_LABELS[state.loop_mode]}**",
            ephemeral=True,
        )


@bot.event
async def on_ready():
    bot.add_view(ControlPanel())
    synced = await bot.tree.sync()
    print(f"Zalogowano jako {bot.user}, zsynchronizowano {len(synced)} komend slash.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if (
        CONTROL_CHANNEL_ID
        and message.guild
        and message.channel.id == CONTROL_CHANNEL_ID
        and not message.content.startswith("!")
    ):
        if not isinstance(message.author, discord.Member):
            return
        try:
            track = await enqueue_track(message.guild, message.author, message.content, str(message.author))
            state = get_state(message.guild.id)
            if message.guild.voice_client and state.current is None:
                await message.channel.send(f"▶️ Odtwarzam ({track.source_label}): **{track.title}**")
            else:
                await message.channel.send(f"➕ Dodano do kolejki ({track.source_label}): **{track.title}**")
        except Exception as error:
            await message.channel.send(f"❌ Błąd: {error}")

    await bot.process_commands(message)


@bot.command(name="join")
async def join(ctx: commands.Context):
    await ensure_voice_for_member(ctx.author, ctx.guild)
    await ctx.send("Dołączono do kanału głosowego.")


@bot.command(name="play")
async def play(ctx: commands.Context, *, query: str):
    async with ctx.typing():
        track = await enqueue_track(ctx.guild, ctx.author, query, str(ctx.author))

    state = get_state(ctx.guild.id)
    if ctx.voice_client and not ctx.voice_client.is_playing() and state.current is None:
        await ctx.send(f"▶️ Odtwarzam ({track.source_label}): **{track.title}**")
    else:
        await ctx.send(f"➕ Dodano do kolejki ({track.source_label}): **{track.title}**")


@bot.command(name="skip")
async def skip(ctx: commands.Context):
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("Nic teraz nie gra.")
        return

    ctx.voice_client.stop()
    await ctx.send("⏭️ Pominięto utwór.")


@bot.command(name="queue")
async def queue_cmd(ctx: commands.Context):
    state = get_state(ctx.guild.id)
    await ctx.send(queue_preview(state))


@bot.command(name="loop")
async def loop_cmd(ctx: commands.Context, mode: Optional[str] = None):
    state = get_state(ctx.guild.id)
    if mode is None:
        await ctx.send(f"🔁 Aktualny tryb loop: **{LOOP_MODE_LABELS[state.loop_mode]}**")
        return

    new_mode = set_loop_mode(state, mode)
    await ctx.send(f"🔁 Ustawiono loop: **{LOOP_MODE_LABELS[new_mode]}**")


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    state = get_state(ctx.guild.id)

    while not state.queue.empty():
        state.queue.get_nowait()
    state.loop_mode = "off"

    if ctx.voice_client:
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.voice_client.disconnect()

    await ctx.send("⏹️ Odtwarzanie zatrzymane i bot rozłączony.")


@bot.command(name="leave")
async def leave(ctx: commands.Context):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Rozłączono z kanałem głosowym.")
    else:
        await ctx.send("Bot nie jest połączony z kanałem.")


@bot.tree.command(name="join", description="Dołącz do Twojego kanału głosowego")
async def slash_join(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
        return

    try:
        await ensure_voice_for_member(interaction.user, interaction.guild)
        await interaction.response.send_message("Dołączono do kanału głosowego.")
    except Exception as error:
        await interaction.response.send_message(f"❌ Błąd: {error}", ephemeral=True)


@bot.tree.command(name="leave", description="Rozłącz bota z kanału głosowego")
async def slash_leave(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("Rozłączono z kanałem głosowym.")
    else:
        await interaction.response.send_message("Bot nie jest połączony z kanałem.", ephemeral=True)


@bot.tree.command(name="play", description="Dodaj utwór do kolejki")
@app_commands.describe(query="Nazwa piosenki lub link")
async def slash_play(interaction: discord.Interaction, query: str):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    try:
        track = await enqueue_track(interaction.guild, interaction.user, query, str(interaction.user))
        state = get_state(interaction.guild.id)
        if interaction.guild.voice_client and state.current is None:
            await interaction.followup.send(f"▶️ Odtwarzam ({track.source_label}): **{track.title}**")
        else:
            await interaction.followup.send(f"➕ Dodano do kolejki ({track.source_label}): **{track.title}**")
    except Exception as error:
        await interaction.followup.send(f"❌ Błąd: {error}", ephemeral=True)


@bot.tree.command(name="skip", description="Pomiń aktualny utwór")
async def slash_skip(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.voice_client is None:
        await interaction.response.send_message("Bot nie jest podłączony.", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if not vc.is_playing():
        await interaction.response.send_message("Nic teraz nie gra.", ephemeral=True)
        return

    vc.stop()
    await interaction.response.send_message("⏭️ Pominięto utwór.")


@bot.tree.command(name="queue", description="Pokaż kolejkę")
async def slash_queue(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
        return

    state = get_state(interaction.guild.id)
    await interaction.response.send_message(queue_preview(state), ephemeral=True)


@bot.tree.command(name="loop", description="Ustaw zapętlenie")
@app_commands.describe(mode="Tryb: off / track / queue")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="track (piosenka)", value="track"),
        app_commands.Choice(name="queue (playlista)", value="queue"),
    ]
)
async def slash_loop(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if interaction.guild is None:
        await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
        return

    state = get_state(interaction.guild.id)
    new_mode = set_loop_mode(state, mode.value)
    await interaction.response.send_message(f"🔁 Ustawiono loop: **{LOOP_MODE_LABELS[new_mode]}**")


@bot.tree.command(name="stop", description="Zatrzymaj i wyczyść kolejkę")
async def slash_stop(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("To polecenie działa tylko na serwerze.", ephemeral=True)
        return

    state = get_state(interaction.guild.id)
    while not state.queue.empty():
        state.queue.get_nowait()
    state.loop_mode = "off"

    vc = interaction.guild.voice_client
    if vc:
        if vc.is_playing():
            vc.stop()
        await vc.disconnect()

    await interaction.response.send_message("⏹️ Odtwarzanie zatrzymane i bot rozłączony.")


@bot.tree.command(name="panel", description="Wyślij panel sterowania muzyką")
async def slash_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎵 Panel muzyczny",
        description=(
            "Użyj przycisków poniżej albo wpisz nazwę/link piosenki na tym kanale sterowania.\n"
            "Przycisk Loop przełącza tryby: off → track → queue."
        ),
        color=discord.Color.blurple(),
    )

    if CONTROL_CHANNEL_ID and interaction.guild is not None:
        channel = interaction.guild.get_channel(CONTROL_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            await channel.send(embed=embed, view=ControlPanel())
            await interaction.response.send_message(
                f"Panel wysłany na {channel.mention}.",
                ephemeral=True,
            )
            return

    await interaction.response.send_message(embed=embed, view=ControlPanel())


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"❌ Błąd: {error}")


if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Brak DISCORD_TOKEN w zmiennych środowiskowych.")

    control_channel_raw = os.getenv("CONTROL_CHANNEL_ID")
    if control_channel_raw:
        CONTROL_CHANNEL_ID = int(control_channel_raw)

    bot.run(token)
