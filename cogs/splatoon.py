from discord.ext import commands
from .utils import config, checks, maps, fuzzy
from .utils.formats import Plural

from urllib.parse import quote as urlquote
from collections import namedtuple

import random
import asyncio
import discord

GameEntry = namedtuple('GameEntry', ('stage', 'mode'))

def is_valid_entry(result, entry):
    # no dupes
    if entry in result:
        return False

    # make sure the map isn't played in the last 2 games
    last_two_games = result[-2:]
    for prev in last_two_games:
        if prev.stage == entry.stage:
            return False

    return True

def get_random_scrims(modes, maps, count):
    result = []
    current_mode_index = 0
    for index in range(count):
        while True:
            entry = GameEntry(stage=random.choice(maps), mode=modes[current_mode_index])
            if is_valid_entry(result, entry):
                result.append(entry)
                current_mode_index += 1
                if current_mode_index >= len(modes):
                    current_mode_index = 0
                break

    return result

# There's going to be some code duplication here because it's more
# straightforward than trying to be clever, I guess.
# I hope at one day to fix this and make it not-so-ugly.
# Hopefully by completely dropping Splatoon 1 support in
# the future.

class Splatoon:
    """Splatoon related commands."""

    def __init__(self, bot):
        self.bot = bot
        self.splat1_data = config.Config('splatoon.json', loop=bot.loop)
        self.splat2_data = config.Config('splatoon2.json', loop=bot.loop)
        self.map_data = []
        self.map_updater = bot.loop.create_task(self.update_maps())

    def __unload(self):
        self.map_updater.cancel()

    async def update_splatnet_cookie(self):
        username = self.splat1_data.get('username')
        password = self.splat1_data.get('password')
        await maps.get_new_splatnet_cookie(self.bot.session, username, password)

    async def update_maps(self):
        try:
            await self.update_splatnet_cookie()
            while not self.bot.is_closed():
                await self.update_schedule()
                await asyncio.sleep(120) # task runs every 2 minutes
        except asyncio.CancelledError:
            pass

    async def update_schedule(self):
        try:
            schedule = await maps.get_splatnet_schedule(self.bot.session)
        except:
            # if we get an exception, keep the old data
            # make sure to remove the old data that already ended
            self.map_data = [data for data in self.map_data if not data.is_over]
        else:
            self.map_data = []
            for entry in schedule:
                if entry.is_over:
                    continue
                self.map_data.append(entry)

    def get_weapons_named(self, name, *, splatoon2=True):
        data = self.splat2_data if splatoon2 else self.splat1_data
        data = data.get('weapons', [])
        name = name.lower()

        choices = {w['name'].lower(): w for w in data}
        results = fuzzy.extract_or_exact(name, choices, scorer=fuzzy.token_sort_ratio, score_cutoff=60)
        return [v for k, _, v in results]

    @commands.group(aliases=['sp1', 'splatoon1'])
    async def splat1(self, ctx):
        """Commands for Splatoon 1, rather than Splatoon 2."""
        if ctx.invoked_subcommand is None:
            return await ctx.send("That doesn't seem like a valid Splatoon command.")

    @splat1.command(name='maps', aliases=['rotation'])
    async def splat1_maps(self, ctx):
        """Shows the current maps in the Splatoon schedule."""
        try:
            await ctx.send(self.map_data[0])
        except IndexError:
            await ctx.send('No map data found. Try again later.')

    @splat1.command(name='schedule')
    async def splat1_schedule(self, ctx):
        """Shows the current Splatoon schedule."""
        if self.map_data:
            await ctx.send('\n'.join(str(x) for x in self.map_data))
        else:
            await ctx.send('No map data found. Try again later.')

    def weapon_to_string(self, weapon):
        return f'**{weapon["name"]}**\nSub: {weapon["sub"]}, Special: {weapon["special"]}'

    @splat1.command(name='weapon')
    async def splat1_weapon(self, ctx, *, query: str):
        """Displays Splatoon weapon info from a query.

        The query must be at least 3 characters long, otherwise it'll tell you it failed.
        """
        query = query.strip().lower()
        weapons = self.splat1_data.get('weapons', [])
        if len(query) < 3:
            return await ctx.send('The query must be at least 3 characters long.')

        def predicate(weapon):
            lowered = [weapon.lower() for weapon in weapon.values()]
            return any(query in wep for wep in lowered)

        results = list(filter(predicate, weapons))
        if not results:
            return await ctx.send('No results found.')

        output = [f'Found {Plural(weapon=len(results))}:']
        output.extend(self.weapon_to_string(weapon) for weapon in results)

        if len(results) > 10:
            await ctx.author.send('\n'.join(output))
        else:
            await ctx.send('\n'.join(output))

    async def generate_scrims(self, ctx, maps, games, mode):
        modes = ['Rainmaker', 'Splat Zones', 'Tower Control']
        game_count = max(min(games, len(maps)), 3)

        if mode is not None:
            mode = mode.lower()

            # shortcuts that can't be detected by fuzzy matching:
            shortcuts = {
                'rm': 'Rainmaker',
                'sz': 'Splat Zones',
                'tc': 'Tower Control',
                'tw': 'Turf War'
            }

            real_mode = shortcuts.get(mode)
            if real_mode is None:
                real_mode = fuzzy.extract_one(mode, modes + ['Turf War'], scorer=fuzzy.partial_ratio, score_cutoff=50)
                if real_mode is not None:
                    real_mode = real_mode[0]
                else:
                    return await ctx.send('Could not figure out what mode you meant.')

            result = [f'The following games will be played in {real_mode}.']
            for index, stage in enumerate(random.sample(maps, game_count), 1):
                result.append(f'Game {index}: {stage}')
        else:
            random.shuffle(modes)
            scrims = get_random_scrims(modes, maps, game_count)
            result = [f'Game {game}: {scrim.mode} on {scrim.stage}' for game, scrim in enumerate(scrims, 1)]

        await ctx.send('\n'.join(result))

    @splat1.command(name='scrim')
    async def splat1_scrim(self, ctx, games=5, *, mode: str = None):
        """Generates Splatoon scrim map and mode combinations.

        The mode combinations do not have Turf War.

        The mode is rotated unless you pick a mode to play, in which all map
        combinations will use that mode instead.
        """

        maps = self.splat1_data.get('maps', [])
        await self.generate_scrims(ctx, maps, games, mode)

    @splat1.command(name='brand', invoke_without_command=True)
    async def splat1_brand(self, ctx, *, query : str):
        """Shows Splatoon brand info based on either the name or the ability given.

        If the query is an ability then it attempts to find out what brands
        influence that ability, otherwise it just looks for the brand being given.

        The query must be at least 4 characters long.
        """
        query = query.strip().lower()

        if len(query) < 4:
            return await ctx.send('The query must be at least 5 characters long.')

        brands = self.splat1_data.get('brands', [])

        # First, attempt to figure out if it's a brand name.
        def first_check(data):
            lowered = data['name'].lower()
            return fuzzy.partial_ratio(query, lowered) >= 60

        def second_check(data):
            buffed = data['buffed']
            nerfed = data['nerfed']
            if buffed is None or nerfed is None:
                return False
            return fuzzy.partial_ratio(query, buffed.lower()) >= 60 or \
                   fuzzy.partial_ratio(query, nerfed.lower()) >= 60

        result = list(filter(first_check, brands))
        output = []
        fmt = 'The brand "{}" has buffed {} and nerfed {} probabilities.'
        if result:
            # brands found
            output.append('Found the following brands:')
            for entry in result:
                name = entry['name']
                buffed = entry['buffed']
                nerfed = entry['nerfed']

                if buffed is None or nerfed is None:
                    output.append(f'The brand "{name}" is neutral.')
                    continue

                output.append(fmt.format(name, buffed, nerfed))
            output.append('')

        abilities = list(filter(second_check, brands))
        if abilities:
            output.append('Found the following relevant abilities:')
            for entry in abilities:
                output.append(fmt.format(entry['name'], entry['buffed'], entry['nerfed']))

        if not output:
            await ctx.send('Your query returned nothing.')
        else:
            await ctx.send('\n'.join(output))

    @commands.command(hidden=True)
    async def marie(self, ctx):
        """A nice little easter egg."""
        await ctx.send('http://i.stack.imgur.com/0OT9X.png')

    @commands.command()
    async def splatwiki(self, ctx, *, title: str):
        """Returns a Inkipedia page."""
        url = f'http://splatoonwiki.org/wiki/Special:Search/{urlquote(title)}'

        async with ctx.session.get(url) as resp:
            if 'Special:Search' in resp.url.path:
                await ctx.send(f'Could not find your page. Try a search:\n{resp.url.human_repr()}')
            elif resp.status == 200:
                await ctx.send(resp.url)
            elif resp.status == 502:
                await ctx.send('It seems that Inkipedia is taking too long to respond. Try again later.')
            else:
                await ctx.send(f'An error has occurred of status code {resp.status} happened.')

    @commands.command()
    async def schedule(self, ctx):
        """Shows the current Splatoon 2 schedule."""
        await ctx.send(f'This command is coming soon! Try "{ctx.prefix}sp1 schedule" for Splatoon 1 instead.')

    @commands.command()
    async def maps(self, ctx):
        """Shows the current maps in the Splatoon 2."""
        await ctx.send(f'This command is coming soon! Try "{ctx.prefix}sp1 maps" for Splatoon 1 instead.')

    @commands.command()
    async def weapon(self, ctx, *, query: str):
        """Displays Splatoon 2 weapon info from a query.

        The query must be at least 3 characters long, otherwise it'll tell you it failed.
        """
        query = query.strip().lower()
        weapons = self.splat2_data.get('weapons', [])
        if len(query) < 3:
            return await ctx.send('The query must be at least 3 characters long.')

        def predicate(weapon):
            lowered = [weapon.lower() for weapon in weapon.values()]
            return any(query in wep for wep in lowered)

        results = list(filter(predicate, weapons))
        if not results:
            return await ctx.send('No results found.')

        e = discord.Embed(colour=discord.Colour.blurple())
        e.title = f'Found {Plural(weapon=len(results))}'

        subs = '\n'.join(w['sub'] for w in results)
        names = '\n'.join(w['name'] for w in results)
        special = '\n'.join(w['special'] for w in results)

        e.add_field(name='Name', value=names)
        e.add_field(name='Sub', value=subs)
        e.add_field(name='Special', value=special)
        await ctx.send(embed=e)

    @commands.command()
    async def scrim(self, ctx, games=5, *, mode: str = None):
        """Generates Splatoon 2 scrim map and mode combinations.

        The mode combinations do not have Turf War.

        The mode is rotated unless you pick a mode to play, in which all map
        combinations will use that mode instead.
        """
        maps = self.splat2_data.get('maps', [])
        await self.generate_scrims(ctx, maps, games, mode)

    @commands.group(invoke_without_command=True)
    async def brand(self, ctx, *, query: str):
        """Shows Splatoon 2 brand info

        This is based on either the name or the ability given.

        If the query is an ability then it attempts to find out what brands
        influence that ability, otherwise it just looks for the brand being given.

        The query must be at least 4 characters long.
        """
        await ctx.send(f'This command is coming soon! Try "{ctx.prefix}sp1 brand" for Splatoon 1 instead.')

def setup(bot):
    bot.add_cog(Splatoon(bot))
