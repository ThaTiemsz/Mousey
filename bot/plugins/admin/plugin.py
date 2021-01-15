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

from ... import Plugin, bot_has_permissions, group
from .enums import PruneStrategy


# Moderator permissions - ignore these users unconditionally
PERMISSIONS = discord.Permissions(administrator=True, ban_members=True, kick_members=True, manage_messages=True)


def has_any_role(role_ids):
    def check(member):
        return any(x.id in role_ids for x in member.roles)

    return check


def can_be_pruned(bot_role):
    def check(member):
        return not member.bot and member.top_role < bot_role and not member.guild_permissions.value & PERMISSIONS.value

    return check


def should_prune_seen(start):
    def check(status):
        return status.seen is None or status.seen < start

    return check


def should_prune_status(start):
    seen = should_prune_seen(start)

    def check(status):
        return seen(status) and status.status is None or status.status < start

    return check


class Admin(Plugin):
    def cog_check(self, ctx):
        return ctx.author.guild_permissions.administrator

    @group(default_greedy=True)
    @bot_has_permissions(kick_members=True, send_messages=True)
    async def prune(self, ctx, roles: commands.Greedy[discord.Role], days: int):
        """
        Prune members not seen on Discord in a specified amount of days.
        You will be prompted to confirm after the bot has calculated who will be pruned.

        By default members without roles are pruned.
        Including roles only removes members having one of them.

        Roles can be specified using their mention, ID, or name.
        Days must be specified as a positive integer greater than seven.

        Example: `{prefix}prune 45`
        Example: `{prefix}prune Unverified 15`
        """

        await self._prune_command(ctx, PruneStrategy.status, roles, days)

    @prune.command('local', default_greedy=True)
    @bot_has_permissions(kick_members=True, send_messages=True)
    async def prune_local(self, ctx, roles: commands.Greedy[discord.Role], days: int):
        """
        Prune members not seen on the server in a specified amount of days.
        You will be prompted to confirm after the bot has calculated who will be pruned.

        By default members without roles are pruned.
        Including roles only removes members having one of them.

        Roles can be specified using their mention, ID, or name.
        Days must be specified as a positive integer greater than seven.

        Example: `{prefix}prune local 45`
        Example: `{prefix}prune local Unverified 15`
        """

        await self._prune_command(ctx, PruneStrategy.seen, roles, days)

    async def _prune_command(self, ctx, strategy, roles, days):
        now = datetime.datetime.utcnow()

        delta = now - ctx.guild.me.joined_at
        spent = int(delta.total_seconds() / 86400)

        if days > spent:
            await ctx.send(f'Unable to prune as I\'ve only been on this server for `{spent}` days, not `{days}`.')
            return

        if not roles:
            members = ctx.guild.members
        else:
            role_ids = set(x.id for x in roles)
            members = filter(has_any_role(role_ids), ctx.guild.members)

        check = can_be_pruned(ctx.me.top_role)
        members = list(filter(check, members))

        tracking = self.mousey.get_cog('Tracking')
        statuses = await tracking.bulk_last_status(members)

        start = now - datetime.timedelta(days=days)

        if strategy is PruneStrategy.seen:
            check = should_prune_seen(start)
        else:
            check = should_prune_status(start)

        count = sum(map(check, statuses))

        msg = await ctx.send(
            f'Pruning members not seen in the past `{days}` days will remove `{count}` members, continue?'
        )

        choices = {
            '\N{CROSS MARK}': False,
            '\N{WHITE HEAVY CHECK MARK}': True,
        }

        for choice in reversed(choices):
            await msg.add_reaction(choice)

        def reaction_check(data):
            return data.channel_id == ctx.channel.id and data.user_id == ctx.author.id and data.emoji.name in choices

        try:
            payload = await self.mousey.wait_for('raw_reaction_add', check=reaction_check, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send('Cancelled prune due to inactivity.')
            return

        do_prune = choices[payload.emoji.name]

        if not do_prune:
            return

        guild = ctx.guild
        events = self.mousey.get_cog('Events')

        for member, status in zip(members, statuses):
            if check(status):
                reason = f'Prune initiated by {ctx.author}'
                events.ignore(guild, 'member_kick', guild, member)

                try:
                    await member.kick(reason=reason)
                except discord.HTTPException:
                    pass
                else:
                    self.mousey.dispatch('mouse_member_kick', guild, member, guild.me, reason)
