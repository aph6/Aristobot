import discord
from discord.ext import commands
from trueskill import *
import trueskill
import itertools
import math
import time
import datetime
import csv
import logging
import psycopg2
import os

description = '''A Discord bot by Aristoza that utilizes the TrueSkill Ranking System: 
https://www.microsoft.com/en-us/research/project/trueskill-ranking-system/ '''

bot = commands.Bot(command_prefix='-', description=description)

DATABASE_URL = os.environ['DATABASE_URL']
conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cur = conn.cursor()

player = dict()
cur.execute("SELECT name, mu, sigma FROM players")
fetch = cur.fetchall()  # fetch all players from the database
for x in fetch:
    player[x[0]] = Rating(mu=x[1], sigma=x[2])  # copy from database into dict

env = TrueSkill()


logging.basicConfig(
    level=logging.INFO,
    style='{',
    datefmt="%d.%m.%Y %H:%M:%S",
    format="\n{asctime} [{levelname:<8}] {name}:\n{message}"
)


def rerate(embed, new, t):
    for i, member in enumerate(t):
        new_rating = new[i]
        diff = expose(new_rating) - expose(player[member.name])
        if diff < 0:
            result = 'lost '
        else:
            result = 'gained '
        embed.add_field(name=member.name, value=(result + str(round(diff, 2))) + ' points', inline=False)
        player[member.name] = new_rating
        sql_update()
    return


def logdata():
    ts = time.time()
    st = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    for k, v in player.items():
        cur.execute("""INSERT INTO logs (name, mu, sigma, time) VALUES (%s, %s, %s, %s)""", (k, v.mu, v.sigma, st))
        conn.commit()


def sql_update():
    for k, v in player.items():
        cur.execute("UPDATE players SET mu = %s, sigma = %s WHERE name = %s", (v.mu, v.sigma, k))
    conn.commit()
    return


class Commands:
    
    chk = False

    @commands.command()
    async def countdown(self, ctx, seconds: int):
        """counts down from x in seconds with a maximum of 20 seconds"""

        if seconds > 20:
            await ctx.send('Error: must be 20 seconds or lower')
            return

        if self.chk is True:
            await ctx.send('Timer is already running')
            return
        else:
            self.chk = True

        a = await ctx.send('```' +'Server online in ' + str(seconds) + ' seconds' + '```')

        while seconds > 0:
            time.sleep(1)
            seconds -= 1
            print(seconds)
            await a.edit(content='```' +'Server online in ' + str(seconds) + ' seconds' + '```')
        await a.edit(content='```diff' + u"\u000A" + '+ Server is online' + u"\u000A" + '```')
        time.sleep(5)
        await a.delete()
        self.chk = False

    @commands.command()
    async def register(self, ctx, member: discord.Member = None):  # todo make separate command for registering users
        """registers yourself for ranked games and assigns you the default rating of zero"""
        if member is None:
            member = ctx.author

        if member.name in player:
            await ctx.send('Error: {0} is already a registered member'.format(member))
        else:
            m = member.name
            player[m] = Rating()  # gives the new player the default rating
            cur.execute("""INSERT INTO players (name, mu, sigma) VALUES (%s, %s, %s)""", (m, player[m].mu, player[m].sigma))
            conn.commit()
            await ctx.send('{0} has been registered'.format(member))

    @register.error
    async def register_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send('Error: Bad Argument. Please use @ mentions')

    @commands.command()
    async def matchreport(self, ctx):
        """reports the result of a match. It will ask you for the names of the winning team and losing team. You can
        choose any number of players per team (2v2, 2v1, 5v2 etc...) """
        #logdata()

        def check(n):
            return n.author == ctx.message.author

        await ctx.send('@mention the names of the winning team or type cancel to exit...')
        msg = await bot.wait_for('message', timeout=30.0, check=check)
        if msg.content.casefold() == 'cancel':
            await ctx.send('Aborted.')
            return
        if '@' not in msg.content:
            await ctx.send('Please use @mentions to add members...aborting match report')
            return
        t1 = msg.mentions  # split up the string into elements
        t1_ratings = []
        for m in t1:
            if m.name in player.keys():
                t1_ratings.append(player[m.name])
            else:
                await ctx.send('{} is not a registered player'.format(m))
                return

        await ctx.send('@mention the names of the losing team or type cancel to exit...')
        msg = await bot.wait_for('message', timeout=30.0, check=check)
        if msg.content.casefold() == 'cancel':
            await ctx.send('Aborted.')
            return
        if '@' not in msg.content:
            await ctx.send('Please use @mentions to add members...aborting match report')
            return
        t2 = msg.mentions  # split up the string into elements
        t2_ratings = []
        for m in t2:
            if m.name in player.keys():
                t2_ratings.append(player[m.name])
            else:
                await ctx.send('{} is not a registered player'.format(m))
                return

        (new1, new2) = rate([t1_ratings, t2_ratings], ranks=[0, 1])
        embed = discord.Embed(title='Match Result', description='-----------------', color=33023)
        rerate(embed, new1, t1)
        rerate(embed, new2, t2)

        await ctx.send(embed=embed)

    @commands.command(aliases=['lb', 'ldb'])
    async def leaderboard(self, ctx):
        """shows the current leaderboard"""
        rank = 1
        strlist = []
        for k, v in sorted(player.items(), key=lambda x: expose(x[1]), reverse=True):  # operator.itemgetter(1)
            position = str(rank) + '. ' + k
            while len(position) < 25:
                position += '\u00A0'
            position += ' | ' + str(round(expose(v), 2)) + u"\u000A"
            strlist.append(position)
            rank += 1
        table = '\u200b'.join(strlist)
        header = ('\u00A0' * 3) + 'User' + ('\u00A0' * 20) + 'Rating' + u"\u000A"
        divider = '_' * 33 + u"\u000A"

        await ctx.send('```' + u"\u000A" + header + divider + table + divider + u"\u000A" '```')

    @commands.command()
    async def compare(self, ctx, member1: discord.Member, member2: discord.Member):
        # todo allow people to compare teams instead of just 1v1
        """compares two players and calculates a win probability"""
        p1 = member1.name
        p2 = member2.name
        team1 = [player[p1]]
        team2 = [player[p2]]
        delta_mu = sum((r.mu for r in team1)) - sum((r.mu for r in team2))
        sum_sigma = sum((r.sigma ** 2 for r in itertools.chain(team1, team2)))
        size = len(team1) + len(team2)
        denom = math.sqrt((size * (BETA * BETA)) + sum_sigma)
        ts = trueskill.global_env()
        await ctx.send(
            'There is a ' + str(round(ts.cdf(delta_mu / denom), 3) * 100) + '% chance that ' + p2 + ' will get rekt')

    @compare.error
    async def compare_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('Missing Argument: enter two registered members to compare')
        if isinstance(error, commands.BadArgument):
            await ctx.send('Error: Bad Argument. Please use @ mentions')

    @commands.command()
    async def teams(self, ctx, teamsize: int, *member: discord.Member):
        """builds all possible teams out of the given team size and calculates the quality for each match up"""
        d = dict()
        for x in member:
            if x.name in player.keys():
                d[x.name] = player[x.name]
            else:
                await ctx.send('{} is not a registered player'.format(x.name))
                return

        teams = itertools.combinations(d.items(), teamsize)  # divide the players up into all possible teams: AB, AC...
        matchups = itertools.combinations(teams, 2)  # all possible match ups from all possible teams

        report = '```md' + u"\u000A"

        for x in matchups:
            t1 = x[0]  # first team
            t2 = x[1]  # second team
            t1_ratings, t2_ratings, t1_names, t2_names = ([] for n in range(4))
            for i in range(teamsize):
                t1_ratings.append(t1[i][1])
                t2_ratings.append(t2[i][1])
                t1_names.append(t1[i][0])
                t2_names.append(t2[i][0])
            qual = quality([t1_ratings, t2_ratings])
            # ignore all match ups with players on the same team...
            if len(set(t1_names).intersection(set(t2_names))) == 0:
                report += ', '.join(t1_names) + ' <vs> ' + ', '.join(t2_names) + u"\u000A" + '> Quality: ' + str(
                    round(qual, 3)) + u"\u000A"

        await ctx.send(report + u"\u000A" + '```')

    @teams.error
    async def teams_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('Missing Argument: enter the team size followed by a list of members')
        if isinstance(error, commands.BadArgument):
            await ctx.send('Error: Bad Argument. Please use @ mentions')

    @commands.command()
    async def user(self, ctx, member: discord.Member = None):
        """shows you information about the player"""
        rank = 1
        if member is None:
            member = ctx.author
        x = member.name
        for (k, v) in sorted(player.items(), key=lambda x: expose(x[1]), reverse=True):
            if k == x:
                start = '```md' + u"\u000A" + '-'*68 + u"\u000A" + u"\u000A" + 'User Info: '
                middle = u"\u000A" + u"\u000A" + '[rank][#' + str(rank) + '] [rating]['
                end = str(round(expose(v), 2)) + '] [skill][' + str(round(v.mu, 2)) + '] [uncertainty]['
                await ctx.send(start + k + middle + end + str(round(v.sigma, 2)) + ']' + u"\u000A" + u"\u000A" + '-'*68 + u"\u000A" + '```')
                if rank == 1:
                    await ctx.send(k + ' is the G O A T 🐐')
                break
            rank += 1

    @user.error
    async def user_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send('Error: Bad Argument. Check your spelling or use @ mentions')

    @commands.command()
    async def report1v1(self, ctx, winner: discord.Member, loser: discord.Member):
        """used as a shortcut to report 1v1 matches."""
        #logdata()

        def check(m):
            return m.author == ctx.message.author

        p1 = winner.name
        p2 = loser.name
        r1 = player[p1]
        r2 = player[p2]
        await ctx.send(
            'Please type confirm to report a win for ' + p1 + ' over ' + p2 + "\u000A" + 'Otherwise type cancel')
        msg = await bot.wait_for('message', timeout=30.0, check=check)
        if msg.content.casefold() == 'cancel':
            await ctx.send('Aborted.')
            return
        elif msg.content.casefold() == 'confirm':
            pass
        (new_r1, new_r2) = rate_1vs1(r1, r2)
        player[p1] = new_r1
        player[p2] = new_r2
        sql_update()
        gained_r1 = (new_r1.mu - (3 * new_r1.sigma)) - (r1.mu - (3 * r1.sigma))
        await ctx.send(((((p1 + ' won against ') + p2) + ' and gained ') + str(round(gained_r1, 2))) + ' points!')

    @report1v1.error
    async def report1v1_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('Missing Argument: Please enter two players. One winner and one loser')
        if isinstance(error, commands.BadArgument):
            await ctx.send('Error: Bad Argument. Check your spelling or use @ mentions')

    @commands.command()
    async def mathlesson(self, ctx):
        """gives you a math lesson"""
        await ctx.send('https://www.youtube.com/watch?v=WFoC3TR5rzI')

    @commands.command()
    async def info(self, ctx):
        """general info about the bot"""
        openfile = open("info.txt", "r")
        embed = discord.Embed(title='Aristobot', description='This is a bot made by Aristoza that uses the TrueSkill '
                                                            'python package (http://trueskill.org/) which is based on '
                                                            'the '
                                                            'TrueSkill rating system developed by Microsoft.',
                              color=33023)
        embed.add_field(name='How it works', value=openfile.read(), inline=False)
        await ctx.send(embed=embed)


class Admin:
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def resetall(self, ctx):
        """resets everything by deleting every registered player"""
        await ctx.send('Are you sure you want to reset everything? Type yes or cancel...')
        msg = await bot.wait_for('message', timeout=30.0)
        if msg.content.casefold() == 'yes':
            player.clear()
            cur.execute("DELETE FROM players")
            conn.commit()
            await ctx.send('Reset successful.')
        elif msg.content.casefold() == 'cancel':
            await ctx.send('Aborted.')
            pass

    @resetall.error
    async def resetall_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send('You are not allowed to do that...')

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def resetratings(self, ctx):
        """resets ratings of all players back to default"""
        await ctx.send('Are you sure you want to reset all player ratings? Type yes or cancel...')
        msg = await bot.wait_for('message', timeout=30.0)
        if msg.content.casefold() == 'yes':
            for k, v in player.items():
                player[k] = Rating()
            sql_update()
            await ctx.send('Reset successful.')
        elif msg.content.casefold() == 'cancel':
            await ctx.send('Aborted.')
            pass

    @resetratings.error
    async def resetratings_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send('You are not allowed to do that...')

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setrating(self, ctx, member: discord.Member, newmu: int, newsigma: int):
        """change the rating of a player"""
        for k, v in player.items():
            if k == member.name:
                player[k] = Rating(mu=newmu, sigma=newsigma)
                sql_update()
                await ctx.send(k + ' has been assigned a new rating of ' + str(expose(player[k])))
                break

    @setrating.error
    async def setrating_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send('You are not allowed to do that...')

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def deluser(self, ctx, member: discord.Member):
        """removes a player permanently from the league"""
        for k, v in player.items():
            if k == member.name:
                del player[k]
                cur.execute("DELETE FROM players WHERE name=%s", [k])
                conn.commit()
                await ctx.send(k + ' has been removed from the player-base')
                break

    @deluser.error
    async def deluser_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send('You are not allowed to do that...')

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def logs(self, ctx):
        """Uploads logs of previous ratings"""
        openfile = open("logs.csv", "rb")
        logfile = discord.File(fp=openfile, filename='logs')
        await ctx.send(file=logfile)


@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')


bot.add_cog(Commands())
bot.add_cog(Admin())
bot.run(os.getenv('TOKEN'))
