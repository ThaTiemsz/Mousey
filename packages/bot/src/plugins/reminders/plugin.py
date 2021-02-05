# -*- coding: utf-8 -*-

"""
Mousey: Discord Moderation Bot
Copyright (C) 2016 - 2021 Lilly Rose Berner

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import asyncio
import datetime

import discord
from discord.ext import commands

from ... import PURRL, NotFound, Plugin, bot_has_permissions, command, group
from ...utils import PaginatorInterface, Plural, TimeConverter, create_task, human_delta, serialize_user
from .converter import reminder_content, reminder_id


class Reminders(Plugin):
    def __init__(self, mousey):
        super().__init__(mousey)

        self._next = None
        self._task = create_task(self._fulfill_reminders())

    def cog_unload(self):
        if not self._task.done():
            self._task.cancel()

    @group(aliases=['reminder', 'remindme'])
    @bot_has_permissions(send_messages=True)
    async def remind(self, ctx, time: TimeConverter, *, message: reminder_content = None):
        """
        Create a reminder at the specified time.

        Time can be given as a duration or ISO8601 date with up to minute precision.

        Example: `{prefix}remind 2 days 16 hr Ask for Nelly's phone number!`
        Example: `{prefix}remind 2021-1-1 8:00 How is the new year, Mousey?`
        \u200b
        """
        # TODO: Support other date formats

        if isinstance(time, datetime.timedelta):
            expires = datetime.datetime.utcnow() + time
            response = f'in {human_delta(time)}'
        else:  # datetime.datetime
            expires = time
            response = 'at ' + time.strftime('%Y-%m-%d %H:%M')

        data = {
            'user': serialize_user(ctx.author),
            'guild_id': ctx.guild.id,
            'channel_id': ctx.channel.id,
            'message_id': ctx.message.id,
            'expires_at': expires.isoformat(),
            'message': message or 'something',
        }

        resp = await self.mousey.api.create_reminder(data)
        idx = resp['id']

        if self._next is None or self._next > expires:
            self._task.cancel()
            self._task = create_task(self._fulfill_reminders())

        about = f'about {message} ' if message else ''
        await ctx.send(f'I will remind you {about}{response}. #{idx}')

    @remind.command('list')
    @bot_has_permissions(add_reactions=True, send_messages=True)
    async def remind_list(self, ctx):
        """
        View all of your upcoming reminders in the current server.

        Example: `{prefix}remind list`
        """

        try:
            resp = await self.mousey.api.get_member_reminders(ctx.guild.id, ctx.author.id)
        except NotFound:
            await ctx.send('You have no upcoming reminders!')
        else:
            prefix = self.mousey.get_cog('Help').clean_prefix(ctx.prefix)
            usage = f'{self.remind_cancel.qualified_name} {self.remind_cancel.signature}'

            paginator = commands.Paginator(
                prefix='Your upcoming reminders:\n',
                suffix=f'\nCancel reminders using `{prefix}{usage}`',
                max_size=500,
            )

            now = datetime.datetime.utcnow()

            for index, data in enumerate(resp, 1):
                idx = data['id']
                message = data['message']

                expires_at = datetime.datetime.fromisoformat(data['expires_at'])
                expires_at = human_delta(expires_at - now)

                paginator.add_line(f'**#{idx}** in `{expires_at}`:\n{message}')

                if not index % 10:  # Display a max of 10 results per page
                    paginator.close_page()

            # TODO: https://github.com/Gorialis/jishaku/issues/87
            await PaginatorInterface(self.mousey, paginator, owner=ctx.author, timeout=600).send_to(ctx.channel)

    @command(hidden=True, help=remind_list.help)
    @bot_has_permissions(send_messages=True)
    async def reminders(self, ctx):
        await ctx.invoke(self.remind_list)

    @remind.command('cancel', aliases=['delete'])
    @bot_has_permissions(send_messages=True)
    async def remind_cancel(self, ctx, reminders: commands.Greedy[reminder_id]):
        """
        Cancel one or more of your own reminders.

        Reminders must be specified using their IDs.
          Reminder IDs are visible on reminder create and using `{prefix}remind list`.

        Example: `{prefix}remind cancel 147`
        """

        deleted = 0

        for idx in reminders:
            try:
                resp = await self.mousey.api.get_reminder(idx)
            except NotFound:
                continue

            if resp['user_id'] != ctx.author.id:
                continue

            try:
                await self.mousey.api.delete_reminder(idx)
            except NotFound:
                pass
            else:
                deleted += 1

        if deleted:
            if self._next is not None:
                self._task.cancel()
                self._task = create_task(self._fulfill_reminders())

            msg = f'Successfully deleted {Plural(deleted):reminder}.'
        else:
            msg = 'Unable to delete reminder, it may already be deleted or not belong to you.'

        await ctx.send(msg)

    async def _fulfill_reminders(self):
        await self.mousey.wait_until_ready()

        while not self.mousey.is_closed():
            try:
                resp = await self.mousey.api.get_reminders(self.mousey.shard_id)
            except NotFound:
                return

            reminder = resp[0]
            expires_at = datetime.datetime.fromisoformat(reminder['expires_at'])

            self._next = expires_at
            await discord.utils.sleep_until(expires_at)

            guild = self.mousey.get_guild(reminder['guild_id'])

            if guild is None:
                await asyncio.shield(self._delete_reminder(reminder['id']))
                continue

            if guild.unavailable:
                # Reschedule until the guild is hopefully available again
                expires_at += datetime.timedelta(minutes=5)
                await asyncio.shield(self._reschedule_reminder(reminder['id'], expires_at))
                continue

            channel = guild.get_channel(reminder['channel_id'])

            if channel is None or not channel.permissions_for(channel.guild.me).send_messages:
                await asyncio.shield(self._delete_reminder(reminder['id']))
                continue

            message_id = reminder['message_id']

            now = datetime.datetime.utcnow()
            created_at = discord.utils.snowflake_time(message_id)

            user_id = reminder['user_id']
            content = reminder['message']
            created = human_delta(now - created_at)

            content = f'Hey <@!{user_id}> {PURRL}! You asked to be reminded about {content} {created} ago.'

            member = channel.guild.get_member(user_id)

            if member is None:
                everyone = False
            else:
                everyone = channel.permissions_for(member).mention_everyone

            # TODO: Allow mentioning roles
            mentions = discord.AllowedMentions(everyone=everyone, users=True, replied_user=True)

            try:
                message = channel.get_partial_message(message_id)
                await message.reply(content, allowed_mentions=mentions)
            except discord.HTTPException:
                # Message we're replying to doesn't exist anymore
                # There's no specific json error code for this sadly
                try:
                    await channel.send(content, allowed_mentions=mentions)
                except discord.HTTPException:
                    pass

            await asyncio.shield(self._delete_reminder(reminder['id']))

    async def _delete_reminder(self, idx):
        self._next = None

        try:
            await self.mousey.api.delete_reminder(idx)
        except NotFound:
            pass

    async def _reschedule_reminder(self, idx, expires_at):
        self._next = None
        expires_at = expires_at.isoformat()

        try:
            await self.mousey.api.update_reminder(idx, expires_at)
        except NotFound:
            pass
