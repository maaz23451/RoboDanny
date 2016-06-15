from discord.ext import commands
import discord
import datetime
from .utils import checks, config
import json

class Stars:
    """A starboard to upvote posts obviously.

    To make use of this feature it is required that Developer Mode
    is enabled in the setting so you can copy message IDs. To do so,
    enable it under Settings > Appearance > Developer Mode
    """

    def __init__(self, bot):
        self.bot = bot

        # config format: (yeah, it's not ideal or really any good but whatever)
        # <guild_id> : <data> where <data> is
        # channel: <starboard channel id>
        # message_id: [bot_message, [starred_user_ids]]
        self.stars = config.Config('stars.json')

        # cache message objects to save Discord some HTTP requests.
        self._message_cache = {}

    def star_emoji(self, stars):
        if 5 >= stars >= 0:
            return '\N{WHITE MEDIUM STAR}'
        elif 10 >= stars >= 6:
            return '\N{GLOWING STAR}'
        elif 25 >= stars >= 11:
            return '\N{DIZZY SYMBOL}'
        else:
            return '\N{SPARKLES}'

    def emoji_message(self, msg, starrers):
        # we should hope that the message length is not too big for this to work.
        emoji = self.star_emoji(starrers)
        content = msg.clean_content
        if msg.attachments:
            attachments = '(attachment: {[url]})'.format(msg.attachments[0])
            if content:
                content = content + ' ' + attachments
            else:
                content = attachments

        # <emoji> <star> <content> - <time> by <user> in <channel>
        if starrers > 1:
            base = '{0} **{1}**'
        else:
            base = '{0}'

        fmt = base + ' {2} - {3.timestamp:%Y-%m-%d %H:%M UTC} by {3.author} in {3.channel.mention} (ID: {3.id})'
        return fmt.format(emoji, starrers, content, msg)

    @commands.command(pass_context=True, no_pm=True)
    @checks.admin_or_permissions(administrator=True)
    async def starboard(self, ctx, *, name: str = 'starboard'):
        """Sets up the starboard for this server.

        This creates a new channel with the specified name
        and makes it into the server's "starboard". If no
        name is passed in then it defaults to "starboard".
        If the channel is deleted then the starboard is
        deleted as well.

        You must have Manage Channel permissions to use this
        command or the Bot Admin role.
        """

        server = ctx.message.server

        stars = self.stars.get(server.id, {})
        old_starboard = self.bot.get_channel(stars.get('channel'))
        if old_starboard is not None:
            fmt = 'This channel already has a starboard ({.mention})'
            await self.bot.say(fmt.format(old_starboard))
            return

        # an old channel might have been deleted and thus we should clear all its star data
        stars = {}

        my_permissions = ctx.message.channel.permissions_for(server.me)
        args = [server, name]

        if my_permissions.manage_roles:
            mine = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            everyone = discord.PermissionOverwrite(send_messages=False)
            args.append((server.me, mine))
            args.append((server.default_role, everyone))

        try:
            channel = await self.bot.create_channel(*args)
        except discord.Forbidden:
            await self.bot.say('\N{NO ENTRY SIGN} I do not have permissions to create a channel.')
        except discord.HTTPException:
            await self.bot.say('\N{PISTOL} This channel name is bad or an unknown error happened.')
        else:
            stars['channel'] = channel.id
            await self.stars.put(server.id, stars)
            await self.bot.say('\N{GLOWING STAR} Starboard created at ' + channel.mention)

    async def get_message(self, channel, mid):
        cached = self._message_cache.get(mid)
        if cached is not None:
            return cached

        before = discord.Object(id=str(int(mid) + 1))
        async for m in self.bot.logs_from(channel, limit=1, before=before):
            self._message_cache[mid] = m
            return m
        return None

    # a custom on_message_edit
    async def on_socket_raw_receive(self, data):
        # no binary frames
        if isinstance(data, bytes):
            return

        data = json.loads(data)
        event = data.get('t')
        payload = data.get('d')
        if event not in ('MESSAGE_UPDATE', 'MESSAGE_DELETE'):
            return

        # check if it's _D and not _U
        is_message_delete = event[8] == 'D'

        if not is_message_delete and 'payload' not in payload:
            # embed only edit..
            return

        channel = self.bot.get_channel(payload.get('channel_id'))
        if channel is None or channel.is_private:
            return

        server = channel.server
        db = self.stars.get(server.id)
        if db is None:
            return

        starboard = self.bot.get_channel(db.get('channel'))
        msg_id = payload['id']

        if is_message_delete:
            if channel.id != starboard.id:
                return # not a delete we're interested in

            # see if the message being deleted is in the starboard
            exists = discord.utils.find(lambda k: db[k][0] == msg_id, db)
            if exists:
                db.pop(exists)
                await self.stars.put(server.id, db)

            return

        # at this point we're in MESSAGE_UPDATE
        # check if the edited message is even being tracked
        stars = db.get(msg_id)
        if stars is None:
            return


        # V O I D W A R R A N T Y
        new_msg = discord.Message(channel=channel, **payload)
        star_msg = await self.get_message(starboard, stars[0])

        # edit the message with the new info
        try:
            await self.bot.edit_message(star_msg, self.emoji_message(new_msg, len(stars[1])))
        except:
            pass # the content was probably too big so just ignore this edit.

    @commands.command(pass_context=True, no_pm=True)
    async def star(self, ctx, message: int):
        """Stars a message via message ID.

        To star a message you should click on the cog
        on a message and then click "Copy ID". You must have
        Developer Mode enabled to get that functionality.

        You can only star a message once. You cannot star
        messages older than 7 days.
        """

        starrer = ctx.message.author
        guild_id = ctx.message.server.id
        db = self.stars.get(guild_id, {})
        message = str(message)
        starboard = self.bot.get_channel(db.get('channel'))
        if starboard is None:
            await self.bot.say('\N{WARNING SIGN} Starboard channel not found.')
            return

        stars = db.get(message, [None, []]) # ew, I know.
        starrers = stars[1]

        if starrer.id in starrers:
            await self.bot.say('\N{NO ENTRY SIGN} You already starred this message.')
            return

        msg = await self.get_message(ctx.message.channel, message)
        if msg is None:
            await self.bot.say('\N{BLACK QUESTION MARK ORNAMENT} This message could not be found.')
            return

        if starrer.id == msg.author.id:
            await self.bot.say('\N{NO ENTRY SIGN} You cannot star your own message.')
            return

        if msg.channel.id == starboard.id:
            await self.bot.say('\N{NO ENTRY SIGN} You cannot star messages in the starboard.')
            return

        # check if the message is older than 7 days
        seven_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        if msg.timestamp < seven_days_ago:
            await self.bot.say('\N{NO ENTRY SIGN} This message is older than 7 days.')
            return

        # at this point we can assume that the user did not star the message
        # and that it is relatively safe to star
        to_send = self.emoji_message(msg, len(starrers) + 1)
        if len(to_send) > 2000:
            await self.bot.say('\N{NO ENTRY SIGN} This message is too big to be starred.')
            return

        # try to remove the star message since it's 'spammy'
        try:
            await self.bot.delete_message(ctx.message)
        except:
            pass

        starrers.append(starrer.id)
        db[message] = stars

        # freshly starred
        if stars[0] is None:
            sent = await self.bot.send_message(starboard, to_send)
            stars[0] = sent.id
            await self.stars.put(guild_id, db)
            return

        bot_msg = await self.get_message(starboard, stars[0])
        if bot_msg is None:
            await self.bot.say('\N{BLACK QUESTION MARK ORNAMENT} Expected to be {0.mention} but is not.'.format(starboard))

            # remove the entry from the starboard cache since someone deleted it.
            # i.e. they did a 'clear' on the stars.
            # they can go through this process again if they *truly* want to star it.
            db.pop(message, None)
            await self.stars.put(guild_id, db)
            return

        await self.bot.edit_message(bot_msg, to_send)
        await self.stars.put(guild_id, db)

    @star.error
    async def star_error(self, error, ctx):
        if type(error) is commands.BadArgument:
            await self.bot.say('That is not a valid message ID. Use Developer Mode to get the Copy ID option.')


def setup(bot):
    bot.add_cog(Stars(bot))