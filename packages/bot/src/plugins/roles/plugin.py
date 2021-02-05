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

import discord
from discord.ext import commands

from ... import HTTPException, NotFound, Plugin, bot_has_guild_permissions, bot_has_permissions, command
from ... import group as command_group
from ...utils import PaginatorInterface, code_safe
from .converter import Group, group_description


class Roles(Plugin):
    @command()
    @bot_has_permissions(send_messages=True)
    @bot_has_guild_permissions(manage_roles=True)
    async def join(self, ctx, *, group: Group):
        """
        Join a self-assignable group role.

        Group must be the full or partial name of the role.

        Example: `{prefix}join event announcements`
        """

        if group not in ctx.author.roles:
            events = self.mousey.get_cog('Events')
            events.ignore(ctx.guild, 'role_add', ctx.author, group)

            reason = 'Self-assigned role'

            await ctx.author.add_roles(group, reason=reason)
            self.mousey.dispatch('mouse_role_add', ctx.author, group, ctx.me, reason)

        await ctx.send(f'You\'ve been added to the `{code_safe(group)}` group role.')

    @command()
    @bot_has_permissions(send_messages=True)
    @bot_has_guild_permissions(manage_roles=True)
    async def leave(self, ctx, *, group: Group):
        """
        Leave a self-assignable group role.

        Group must be the full or partial name of the role.

        Example: `{prefix}leave event announcements`
        """

        if group in ctx.author.roles:
            events = self.mousey.get_cog('Events')
            events.ignore(ctx.guild, 'role_remove', ctx.author, group)

            reason = 'Self-assigned role'

            await ctx.author.remove_roles(group, reason=reason)
            self.mousey.dispatch('mouse_role_remove', ctx.author, group, ctx.me, reason)

        await ctx.send(f'You\'ve been removed from the `{code_safe(group)}` group role.')

    @command_group(aliases=['group'])
    @bot_has_permissions(add_reactions=True, send_messages=True)
    async def groups(self, ctx):
        """
        Lists all available self-assignable group roles.

        Example: `{prefix}groups`
        """

        try:
            resp = await self.mousey.api.get_groups(ctx.guild.id)
        except NotFound:
            await ctx.send('There are not self-assignable group roles set up.')
            return

        prefix = self.mousey.get_cog('Help').clean_prefix(ctx.prefix)

        join = f'{self.join.qualified_name} {self.join.signature}'
        leave = f'{self.leave.qualified_name} {self.leave.signature}'

        paginator = commands.Paginator(
            max_size=500,
            prefix='Self-assignable group roles:\n',
            suffix=f'\nUse `{prefix}{join}` and `{prefix}{leave}` to manage roles',
        )

        groups = []

        for data in resp:
            role = ctx.guild.get_role(data['role_id'])

            if role is None:
                continue

            if not data['description']:
                description = ''
            else:
                description = ' - ' + data['description']

            groups.append(role.mention + description)

        groups.sort(key=str.lower)

        for group in groups:
            paginator.add_line(group)

        # TODO: https://github.com/Gorialis/jishaku/issues/87
        await PaginatorInterface(self.mousey, paginator, owner=ctx.author, timeout=600).send_to(ctx.channel)

    @groups.command('create')
    @bot_has_permissions(send_messages=True)
    @commands.has_permissions(manage_roles=True)
    async def groups_create(self, ctx, role: discord.Role, *, description: group_description = None):
        """
        Allow users to manage their role membership for a role using the `join` and `leave` commands.

        Role must be specified as a mention, ID, or name.
        Description can be any string up to 250 characters or will default to being empty.

        Example: `{prefix}groups create "LF Campaign" Receive notifications about new campaigns`
        """

        data = {'description': description}

        try:
            await self.mousey.api.create_group(ctx.guild.id, role.id, data)
        except HTTPException as e:
            await ctx.send(f'Failed to create group. {e.message}')  # Lists privileged permissions,,
        else:
            if description is None:
                extra = ''
            else:
                extra = f'with description `{code_safe(description)}`'

            await ctx.send(f'Created group `{code_safe(role)}`{extra}.')

    @groups.command('remove')
    @bot_has_permissions(send_messages=True)
    @commands.has_permissions(manage_roles=True)
    async def groups_remove(self, ctx, *, group: Group):
        """
        Remove a role from the self-assignable group role list.

        Group must be the full or partial name of the role.

        Example: `{prefix}group remove LF Campaign`
        """

        await self.mousey.api.delete_group(ctx.guild.id, group.id)
        await ctx.send(f'Successfully removed group `{code_safe(group)}`.')
