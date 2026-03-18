import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
from dotenv import load_dotenv
import asyncio
import json
import os
from PIL import Image
from database import User, Character, get_async_db  # + sqlalchemy: select, func
from sqlalchemy import select, func, update
import random

DND_API = os.getenv('DND_API_BASE', 'https://www.dnd5eapi.co/api')

LEVEL_XP = [0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000, 85000, 100000,
            120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000]

# Oben nach imports hinzufügen
class PaginationView(discord.ui.View):
    def __init__(self, items, title="Liste", per_page=10):
        super().__init__(timeout=120)
        self.items = items
        self.page = 0
        self.per_page = per_page
        self.title = title

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await self.update(interaction)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < (len(self.items) - 1) // self.per_page:
            self.page += 1
            await self.update(interaction)

    async def update(self, interaction):
        start = self.page * self.per_page
        end = start + self.per_page
        page_items = self.items[start:end]
        
        text = '\n'.join([f"• {item}" for item in page_items])
        embed = discord.Embed(title=f"{self.title} (Seite {self.page+1}/{ (len(self.items)+self.per_page-1)//self.per_page })", 
                             description=text or "Leer", color=0xf39c12)
        await interaction.response.edit_message(embed=embed, view=self)



load_dotenv()
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)


# ALTER on_ready bleibt (nur Logging)
@bot.event
async def on_ready():
    print(f'{bot.user} logged in! Slash Commands bereit.')


async def get_active_char(db, user_Id):
    user_result = await db.execute(select(User).where(User.discordId == user_Id))
    user = user_result.scalar_one()
    if user.activeCharId:
        char_result = await db.execute(select(Character).where(Character.id == user.activeCharId))
        return char_result.scalar_one()
    return None


from discord import app_commands
from sqlalchemy import select, update
import asyncio

@bot.tree.command(name='purge', description='Löscht Nachrichten (Admin only)')
@app_commands.describe(
    amount='Anzahl Nachrichten (1-100)',
    user='Optional: Nur Nachrichten dieses Users',
    reason='Grund (optional)'
)
@app_commands.checks.has_permissions(manage_messages=True)  # ✅ Admin-Check
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int, user: discord.Member = None, reason: str = None):
    # 1. Validierung
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ Amount: 1-100!", ephemeral=True)
        return
    
    # 2. Defer (wichtig für purge!)
    await interaction.response.defer(ephemeral=True)
    
    # 3. Purge ausführen
    def check(m):
        return user is None or m.author == user
    
    deleted = await interaction.channel.purge(limit=amount, check=check, reason=reason)
    
    # 4. Bestätigung
    embed = discord.Embed(
        title="🧹 Purge abgeschlossen",
        description=f"**{len(deleted)}** Nachrichten gelöscht",
        color=0xe74c3c
    )
    if user:
        embed.add_field(name="User", value=user.mention, inline=True)
    if reason:
        embed.add_field(name="Grund", value=reason, inline=False)
    
    # Auto-delete nach 5s
    msg = await interaction.followup.send(embed=embed, ephemeral=True)
    await asyncio.sleep(5)
    await msg.delete()

# Error-Handler (wenn kein Admin)
@purge.error
async def purge_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ **Manage Messages** Permission erforderlich!", ephemeral=True)



@bot.tree.command(name='class', description='Zeigt D&D Klasse oder alle Klassen')
@app_commands.describe(class_name='Name der Klasse (z.B. fighter) oder leer für Liste')
async def class_slash(interaction: discord.Interaction, class_name: str = None):
    await interaction.response.defer()  # Defer für API-Call
    
    async with aiohttp.ClientSession() as session:
        if class_name:
            # Einzelne Klasse
            async with session.get(f"{DND_API}/classes/{class_name.lower()}") as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Klasse '{class_name}' nicht gefunden! Verwende /class für Liste.")
                    return
                data = await resp.json()
                name = data.get('name', 'Unbekannt')
                desc = data['desc'][0][:150] + '...' if data.get('desc') else 'Keine Beschreibung'
                updated = data.get('updated_at', 'N/A')
                embed = discord.Embed(title=f"📚 {name}", description=desc, color=0x9b59b6)
                embed.add_field(name="Hit Die", value=data.get('hit_die', 'N/A'), inline=True)
                embed.add_field(name="Updated", value=updated, inline=True)
                await interaction.followup.send(embed=embed)
        else:
            async with session.get(f"{DND_API}/classes") as resp:
                data = await resp.json()
                classes_list = [cls['name'] for cls in data['results']]
                view = PaginationView(classes_list, "🏆 Alle Klassen", per_page=12)
                await interaction.followup.send(embed=discord.Embed(title="Klicke zum Blättern!", color=0x3498db), view=view)



@bot.tree.command(name='race', description='Zeigt D&D Rasse oder alle Rassen')
@app_commands.describe(race_name='Name der Rasse (z.B. elf) oder leer für Liste')
async def race_slash(interaction: discord.Interaction, race_name: str = None):
    await interaction.response.defer()
    
    async with aiohttp.ClientSession() as session:
        if race_name:
            # Einzelne Rasse
            async with session.get(f"{DND_API}/races/{race_name.lower()}") as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Rasse '{race_name}' nicht gefunden! Verwende /race für Liste.")
                    return
                data = await resp.json()
                name = data.get('name', 'Unbekannt')
                desc = data['desc'][0][:150] + '...' if data.get('desc') else 'Keine Beschreibung'
                speed = data.get('speed', 'N/A')
                alignment = data.get('alignment', 'N/A')
                embed = discord.Embed(title=f"🏺 {name}", description=desc, color=0x2ecc71)
                embed.add_field(name="Speed", value=speed, inline=True)
                embed.add_field(name="Alignment", value=alignment, inline=True)
                if data.get('traits'):
                    traits = ', '.join([t['name'] for t in data['traits'][:3]])
                    embed.add_field(name="Traits", value=traits or 'Keine', inline=False)
                await interaction.followup.send(embed=embed)
        else:
            async with session.get(f"{DND_API}/races") as resp:
                data = await resp.json()
                races_list = [race['name'] for race in data['results']]
                view = PaginationView(races_list, "🌿 Alle Rassen", per_page=12)
                await interaction.followup.send(embed=discord.Embed(title="Klicke zum Blättern!", color=0x27ae60), view=view)



@bot.tree.command(name='monster', description='Zeigt Monster-Details oder suche per Name')
@app_commands.describe(monster_name='Monster-Name (z.B. "goblin", "adult") oder leer für Liste')
async def monster_slash(interaction: discord.Interaction, monster_name: str = None):
    await interaction.response.defer()
    
    async with aiohttp.ClientSession() as session:
        if monster_name:
            # 1. Exakte Suche versuchen (direkt index)
            exact_index = monster_name.lower().replace(' ', '-')
            async with session.get(f"{DND_API}/monsters/{exact_index}") as resp:
                if resp.status == 200:
                    # Exakter Treffer gefunden!
                    data = await resp.json()
                    name = data.get('name', 'Unbekannt')
                    cr = data.get('cr', 'N/A')
                    ac = data.get('armor_class', 'N/A')
                    hp = f"{data.get('hit_points', 'N/A')} ({data.get('hit_dice', 'N/A')})"
                    desc = data.get('desc', 'Keine Beschreibung')[:150] + '...'
                    embed = discord.Embed(title=f"👹 {name}", description=desc, color=0x8b0000)
                    embed.add_field(name="CR", value=cr, inline=True)
                    embed.add_field(name="AC", value=ac, inline=True)
                    embed.add_field(name="HP", value=hp, inline=True)
                    if data.get('actions'):
                        action = data['actions'][0].get('name', 'Keine') if data['actions'] else 'Keine'
                        embed.add_field(name="Action", value=action, inline=False)
                    await interaction.followup.send(embed=embed)
                    return  # Fertig!
            
            # 2. Fallback: Suche nach Teilname
            search_query = monster_name.replace(' ', '+')
            async with session.get(f"{DND_API}/monsters?name={search_query}") as resp:
                data = await resp.json()
                results = data.get('results', [])
                
                if len(results) == 0:
                    await interaction.followup.send(f"❌ Kein Monster mit '{monster_name}' gefunden! Versuche /monster goblin.")
                    return
                
                # Best-Match: Erstes mit exaktem Namen (case-insensitive)
                exact_match = next((m for m in results if m['name'].lower() == monster_name.lower()), None)
                if exact_match:
                    # Details für Best-Match laden
                    async with session.get(f"{DND_API}/monsters/{exact_match['index']}") as detail_resp:
                        detail_data = await detail_resp.json()
                        name = detail_data.get('name')
                        cr = detail_data.get('cr', 'N/A')
                        ac = detail_data.get('armor_class', 'N/A')
                        hp = f"{detail_data.get('hit_points', 'N/A')} ({detail_data.get('hit_dice', 'N/A')})"
                        desc = detail_data.get('desc', 'Keine')[:150] + '...'
                        embed = discord.Embed(title=f"👹 {name}", description=desc, color=0x8b0000)
                        embed.add_field(name="CR", value=cr, inline=True)
                        embed.add_field(name="AC", value=ac, inline=True)
                        embed.add_field(name="HP", value=hp, inline=True)
                        await interaction.followup.send(embed=embed)
                else:
                    # Mehrere Treffer → Liste (max 10)
                    monster_text = '\n'.join([f"• {m['name']}" for m in results[:10]])
                    embed = discord.Embed(title=f"🔍 '{monster_name}' ({len(results)} Treffer)", 
                                        description=monster_text, color=0x34495e)
                    embed.set_footer(text="Verwende exakten Namen: /monster goblin")
                    await interaction.followup.send(embed=embed)
        else:
            async with session.get(f"{DND_API}/monsters") as resp:
                data = await resp.json()
                monsters_list = [m['name'] for m in data['results'][:50]]  # Top 50 (API-Limit)
                view = PaginationView(monsters_list, "🐉 Alle Monster", per_page=15)
                await interaction.followup.send(embed=discord.Embed(title="Klicke zum Blättern!", color=0x34495e), view=view)



@bot.tree.command(name='abilities', description='Zeigt alle 6 Kern-Abilities oder Details')
@app_commands.describe(ability_name='Ability (str, dex, con, int, wis, cha) oder leer')
async def abilities_slash(interaction: discord.Interaction, ability_name: str = None):
    await interaction.response.defer()
    
    async with aiohttp.ClientSession() as session:
        if ability_name:
            # Einzelne Ability (index = str, dex etc.)
            async with session.get(f"{DND_API}/ability-scores/{ability_name.lower()}") as resp:
                if resp.status != 200:
                    await interaction.followup.send("❌ Ability nicht gefunden! Verwende: str, dex, con, int, wis, cha")
                    return
                data = await resp.json()
                name = data.get('name', 'Unbekannt')  # 'str' statt 'full_name'
                full_name = data.get('full_name', name.upper())
                desc = ' '.join(data.get('desc', []))[:200] + '...' if data.get('desc') else 'Keine Beschreibung'
                skills = data.get('skills', [])
                skill_text = ', '.join([s['name'] for s in skills[:4]]) + ('...' if len(skills) > 4 else '')
                embed = discord.Embed(title=f"💪 {full_name}", description=desc, color=0xf39c12)
                embed.add_field(name="Skills", value=skill_text or 'Keine', inline=False)
                await interaction.followup.send(embed=embed)
        # Ersetze den else:-Block:
        else:
            async with session.get(f"{DND_API}/ability-scores") as resp:
                data = await resp.json()
                abilities = [a['full_name'] for a in data['results']]
                view = PaginationView(abilities, "⚡ Kern-Abilities", per_page=6)
                await interaction.followup.send(embed=discord.Embed(title="Klicke zum Blättern!", color=0xf1c40f), view=view)


@bot.tree.command(name='skills', description='Alle Skills oder Details zu einem Skill')
@app_commands.describe(skill_name='Skill (z.B. acrobatics) oder leer für Liste')
async def skills_slash(interaction: discord.Interaction, skill_name: str = None):
    await interaction.response.defer()
    
    async with aiohttp.ClientSession() as session:
        if skill_name:
            # Einzelner Skill
            async with session.get(f"{DND_API}/skills/{skill_name.lower()}") as resp:
                if resp.status != 200:
                    await interaction.followup.send("❌ Skill nicht gefunden! /skills für Liste.")
                    return
                data = await resp.json()
                name = data['name']
                desc = data['desc'][:200] + '...'
                ability = data['ability_score']['full_name']
                embed = discord.Embed(title=f"🎯 {name}", description=desc, color=0x3498db)
                embed.add_field(name="Ability", value=ability, inline=True)
                await interaction.followup.send(embed=embed)
        else:
            async with session.get(f"{DND_API}/skills") as resp:
                data = await resp.json()
                skills_list = [s['name'] for s in data['results']]
                view = PaginationView(skills_list, "📚 Alle Skills", per_page=18)
                await interaction.followup.send(embed=discord.Embed(title="Klicke zum Blättern!", color=0x9b59b6), view=view)




@bot.tree.command(name='features', description='Class Features für Level')
@app_commands.describe(class_name='Klasse (wizard)', level='Level 1-20')
async def features_slash(interaction: discord.Interaction, class_name: str, level: int = 1):
    await interaction.response.defer()
    
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{DND_API}/classes/{class_name.lower()}") as resp:
            if resp.status != 200:
                await interaction.followup.send(f"❌ Klasse '{class_name}' nicht gefunden! /class für Liste.")
                return
            class_data = await resp.json()
            
            async with session.get(f"{DND_API}/classes/{class_name.lower()}/levels/{level}") as level_resp:
                level_data = await level_resp.json()
                features = [f["name"] for f in level_data.get("features", [])]
                feature_text = '\n'.join([f"• {f}" for f in features[:8]]) or "Keine Features"
                
                embed = discord.Embed(title=f"🎭 {class_data['name']} Level {level}", 
                                    description=feature_text, color=0xe67e22)
                embed.set_footer(text=f"/features {class_name} 3 für mehr | /spell für Zauber")
                await interaction.followup.send(embed=embed)

@bot.tree.command(name='spell', description='Spell-Details oder Liste')
@app_commands.describe(spell_name='Spell-Name (fireball) oder leer')
async def spell_slash(interaction: discord.Interaction, spell_name: str = None):
    await interaction.response.defer()
    
    async with aiohttp.ClientSession() as session:
        if spell_name:
            async with session.get(f"{DND_API}/spells/{spell_name.lower().replace(' ', '-')}") as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Spell '{spell_name}' nicht gefunden! /spell für Liste.")
                    return
                data = await resp.json()
                name = data['name']
                level = data['level']
                school = data['school']['name']
                cast_time = data['casting_time']
                range_ = data['range']
                duration = data['duration']
                classes = [c['name'] for c in data['classes']]
                embed = discord.Embed(title=f"✨ {name}", color=0x9b59b6)
                embed.add_field(name="Level", value=f"{level} ({school})", inline=True)
                embed.add_field(name="Cast Time", value=cast_time, inline=True)
                embed.add_field(name="Range", value=range_, inline=True)
                embed.add_field(name="Duration", value=duration, inline=True)
                embed.add_field(name="Classes", value=', '.join(classes[:3]), inline=False)
                await interaction.followup.send(embed=embed)
        else:
            async with session.get(f"{DND_API}/spells") as resp:
                data = await resp.json()
                spells_list = [s['name'] for s in data['results'][:100]]  # Top 100
                view = PaginationView(spells_list, "✨ Alle Spells", per_page=15)
                await interaction.followup.send(embed=discord.Embed(title="Klicke zum Blättern!", color=0x8e44ad), view=view)





@bot.tree.command(name='roll', description='Würfelt Würfel (z.B. 2d20+5)')
@app_commands.describe(dice='Würfelnotation (default: 1d20)')
async def roll_slash(interaction: discord.Interaction, dice: str = '1d20'):
    await interaction.response.defer()
    try:
        num, sides = map(int, dice.split('d'))
        mod = int(dice.split('+')[-1]) if '+' in dice else 0
        rolls = [__import__('random').randint(1, sides) for _ in range(num)]
        total = sum(rolls) + mod
        embed = discord.Embed(title=f"🎲 {dice}", description=f"Rolls: {rolls}\n**Total: {total}**", color=0xe74c3c)
        await interaction.followup.send(embed=embed)
    except:
        await interaction.followup.send("❌ Falsche Notation! z.B. '2d6+3'")



@bot.tree.command(name='charinfo', description='Zeigt deinen Charakter')
async def char_info(interaction: discord.Interaction, char_id: int = None):
    discordId = str(interaction.user.id)
    async for db in get_async_db():
        result = await db.execute(select(User).where(User.discordId == discordId))
        user = result.scalar_one_or_none()
        if not user:
            await interaction.response.send_message("❌ /charcreate zuerst!", ephemeral=True)
            return
        
        if char_id:
            char_result = await db.execute(select(Character).where(Character.id == char_id, Character.user_Id == user.id))
            char = char_result.scalar_one_or_none()
        else:
            char_result = await db.execute(select(Character).where(Character.user_Id == user.id).limit(1))
            char = char_result.scalar_one_or_none()
        if not char:
            await interaction.response.send_message("❌ Kein Charakter!", ephemeral=True)
            return
        
        char = await get_active_char(db, discordId)
        if not char:
            return await interaction.response.send_message("❌ Kein aktiver Char! /chars", ephemeral=True)
        
        # RACIAL BONIS nachholen (aus API)
        race_bonuses = {}
        if char.apiRace:
            race_lower = char.apiRace.lower().replace(' ', '-')
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{DND_API}/races/{race_lower}") as resp:
                        if resp.status == 200:
                            race_data = await resp.json()
                            if 'ability_bonuses' in race_data:
                                for bonus in race_data['ability_bonuses']:
                                    score_key = bonus['ability_score']['index'] + '_score'
                                    race_bonuses[score_key] = bonus['bonus']
            except:
                pass  # Graceful fallback
        
        # Ability Scores + Mods
        def get_mod(score): return (score - 10) // 2
        def get_bonus(score_key): return race_bonuses.get(score_key, 0)
        
        stats_text = (
            f"**STR** {char.str_score} ({get_mod(char.str_score):+d}) [+{get_bonus('str_score')}]  "
            f"**DEX** {char.dex_score} ({get_mod(char.dex_score):+d}) [+{get_bonus('dex_score')}]"
        )
        stats_text += f"\n**CON** {char.con_score} ({get_mod(char.con_score):+d}) [+{get_bonus('con_score')}]  "
        stats_text += f"**INT** {char.int_score} ({get_mod(char.int_score):+d}) [+{get_bonus('int_score')}]"
        stats_text += f"\n**WIS** {char.wis_score} ({get_mod(char.wis_score):+d}) [+{get_bonus('wis_score')}]  "
        stats_text += f"**CHA** {char.cha_score} ({get_mod(char.cha_score):+d}) [+{get_bonus('cha_score')}]"
        
        # Slots
        count_result = await db.execute(select(func.count()).select_from(Character).where(Character.user_Id == user.id))
        slots_free = user.maxSlots - count_result.scalar()
        
        embed = discord.Embed(title=f"📖 {char.name}", color=0x9b59b6)
        embed.description = f"*{char.gender or 'N/A'}* | {char.apiRace or '?'} {char.apiClass or '?'} | **Lvl {char.level}**"
        # Neue Felder anzeigen
        if char.age or char.gender or char.background:
            info_text = f"{char.age or '?'} Jahre, {char.gender or ''}, {char.background or ''}".strip(", ")
            embed.add_field(name="Info", value=info_text, inline=True)

        if char.description:
            embed.add_field(name="Beschreibung", value=char.description[:300] + "..." if len(char.description) > 300 else char.description, inline=False)

        embed.add_field(name="Abilities (+Rasse Bonis)", value=stats_text, inline=False)
        # Nach Abilities
        embed.add_field(
            name="Vitals", 
            value=f"**HP**: {char.hp_current}/{char.hp_max} ({char.hit_die})\n**AC**: {char.ac_base}",
            inline=True
        )
        # Nach Vitals
        if char.features:
            feat_list = json.loads(char.features)
            embed.add_field(name="Features", value=' • '.join(feat_list[-5:]), inline=False)  # Letzte 5

        embed.add_field(name="Progress", value=f"Level {char.level}/20 | XP: {char.xp}/{char.xp_next}", inline=True)


        # Optional: CON für HP-Ref
        con_mod = get_mod(char.con_score)
        embed.add_field(name="CON Mod", value=f"+{con_mod} (für HP)", inline=True)

        embed.add_field(name="Slots frei", value=f"{slots_free}/{user.maxSlots}", inline=True)
        if race_bonuses:
            bonus_list = ', '.join([f"+{v}{k[:-6].upper()}" for k,v in race_bonuses.items()])
            embed.add_field(name="Rasse Bonis", value=bonus_list, inline=True)
        
        # In /charinfo Embed
        if char.image_path and os.path.exists(char.image_path):
            filename = os.path.basename(char.image_path)
            embed.set_image(url=f"attachment://{filename}")
            file = discord.File(char.image_path)
            await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name='chars', description='Alle Charaktere')
async def chars_list(interaction: discord.Interaction):
    discordId = str(interaction.user.id)
    async for db in get_async_db():
        result = await db.execute(select(User.id).where(User.discordId == discordId))
        user_id = result.scalar_one_or_none()  # Holt die eine ID oder None
        if not user_id:
            await interaction.response.send_message("❌ /charcreate zuerst!", ephemeral=True)
            return
        print(f"DEBUG: User ID für chars: {user_id}")
        
        chars_result = await db.execute(select(Character).where(Character.user_Id == user_id))
        char_list = chars_result.scalars().all()
        
        if char_list:
            embed = discord.Embed(title="Deine Charaktere")
            for char in char_list:
                status = "🖼️" if char.image_path else "📝"
                embed.add_field(
                    name=f"{status} {char.name} (Lvl {char.level})",
                    value=f"HP {char.hp_current}/{char.hp_max} | `/charinfo {char.id}`",
                    inline=False
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Du hast noch keine Charaktere! /charcreate", ephemeral=True)

 


import os
import aiohttp
from PIL import Image  # pip install Pillow (optional für Resize)

@bot.tree.command(name='charimage', description='Bild für Charakter setzen')
@app_commands.describe(image='PNG/JPG Attachment')
async def char_image(interaction: discord.Interaction, image: discord.Attachment):
    if not image.content_type.startswith('image/'):
        return await interaction.response.send_message("❌ Nur Bilder!", ephemeral=True)
    
    char = await get_active_char(db, discordId)
    if not char:
        return await interaction.response.send_message("❌ Kein aktiver Char! /chars", ephemeral=True)
    
    discordId = str(interaction.user.id)
    folder = "../character_images"  # Root!
    os.makedirs(folder, exist_ok=True)
    
    async for db in get_async_db():
        user_result = await db.execute(select(User).where(User.discordId == discordId))
        user = user_result.scalar_one_or_none()
        if not user:
            return await interaction.response.send_message("❌ Erstelle Char: /charcreate", ephemeral=True)
        
        # Letzten Char holen (oder Multi-Select später)
        char_result = await db.execute(
            select(Character).where(Character.user_Id == user.id).order_by(Character.id.desc()).limit(1)
        )
        char = char_result.scalar_one_or_none()
        if not char:
            return await interaction.response.send_message("❌ Kein Charakter!", ephemeral=True)
        
        # ↓ WICHTIG: ID vor Download holen!
        char_id = char.id
        filename = f"{char_id}.png"
        filepath = os.path.join(folder, filename)
        
        # Download
        async with aiohttp.ClientSession() as session:
            async with session.get(image.url) as resp:
                if resp.status == 200:
                    img_data = await resp.read()
                    with open(filepath, 'wb') as f:
                        f.write(img_data)
        
        # Pfad speichern
        char.image_path = filepath
        await db.commit()
    
    embed = discord.Embed(title=f"✅ Bild für '{char.name}' gespeichert!", color=0x00ff00)
    embed.set_image(url=image.url)
    await interaction.response.send_message(embed=embed)




@bot.tree.command(name='levelup', description='Level deinen Char up (+Features!)')
async def level_up(interaction: discord.Interaction):
    discordId = str(interaction.user.id)
    async for db in get_async_db():
        result = await db.execute(select(User).where(User.discordId == discordId))
        user = result.scalar_one_or_none()
        if not user: 
            await interaction.response.send_message("❌ Kein User!", ephemeral=True)
            return
        
        char_result = await db.execute(select(Character).where(Character.user_Id == user.id).order_by(Character.level.desc()).limit(1))
        char = char_result.scalar_one_or_none()
        if not char:
            await interaction.response.send_message("❌ Kein Char!", ephemeral=True)
            return
        
        char = await get_active_char(db, discordId)
        if not char:
            return await interaction.response.send_message("❌ Kein aktiver Char! /createchar oder /charswitch", ephemeral=True)
        
        if char.level >= 20:
            await interaction.response.send_message("🏆 Max Level 20!", ephemeral=True)
            return
        
        new_level = char.level + 1
        
        # 1. XP updaten
        char.xp += 100 + (new_level * 50)  # Beispiel-Gain
        char.xp_next = LEVEL_XP[new_level]
        
        # 2. HP + Hit Die AVG + CON
        # Ability Scores + Mods
        def get_mod(score): return (score - 10) // 2
        con_mod = get_mod(char.con_score)
        hit_die_num = int(char.hit_die[1:]) if char.hit_die else 8  # "d10" → 10
        hit_die_avg = (hit_die_num / 2) + 1  # 6
        hp_gain = hit_die_avg + con_mod
        char.hp_max += hp_gain
        char.hp_current = char.hp_max  # Full Heal on Level-Up
        
        # 3. Features aus API
        class_lower = char.apiClass.lower().replace(' ', '-')
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}/classes/{class_lower}/levels/{new_level}") as resp:
                if resp.status == 200:
                    level_data = await resp.json()
                    new_features = [f["name"] for f in level_data.get("features", [])]
                    char.features = json.dumps(new_features)  # JSON-String
                else:
                    new_features = ["Level-Up!"]
        
        char.level = new_level
        await db.commit()
    
    # Embed Response
    embed = discord.Embed(title=f"🎉 Level {new_level} erreicht!", color=0x00ff00)
    embed.add_field(name="HP Gain", value=f"+{hp_gain} (jetzt {char.hp_max})", inline=True)
    embed.add_field(name="Neue Features", value=', '.join(new_features[:3]), inline=False)
    embed.add_field(name="XP", value=f"{char.xp}/{char.xp_next}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


class CharSwitchView(discord.ui.View):
    def __init__(self, char_options):
        super().__init__(timeout=60)
        
        self.select_menu = discord.ui.Select(
            placeholder="Charakter wählen...", 
            min_values=1, max_values=1,
            options=char_options
        )
        self.select_menu.callback = self.select_callback  # Nur interaction
        self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):  # ✅ 1 Parameter!
        selected_char_id = int(interaction.data['values'][0])  # ✅ Standard!        
        async for db in get_async_db():
            user_result = await db.execute(
                select(User).where(User.discordId == str(interaction.user.id))
            )
            user = user_result.scalar_one_or_none()
            
            char_result = await db.execute(
                select(Character).where(Character.id == selected_char_id)
            )
            selected_char = char_result.scalar_one_or_none()
            
            if not user or not selected_char or selected_char.user_Id != user.id:
                await interaction.response.send_message("❌ Ungültiger Char!", ephemeral=True)
                return
            
            # Wechsel-Logik
            await db.execute(
                update(Character)
                .where((Character.user_Id == user.id) & (Character.is_active == True))
                .values(is_active=False)
            )
            await db.execute(
                update(User)
                .where(User.id == user.id)
                .values(activeCharId=selected_char.id)
            )
            selected_char.is_active = True
            await db.commit()
        
        await interaction.response.send_message(
            f"✅ **{selected_char.name}** ({selected_char.apiClass.title()}) ist jetzt aktiv!",
            ephemeral=True
        )




@bot.tree.command(name='charswitch', description='Charaktere switchen')
async def chars_list(interaction: discord.Interaction):  # Name zu chars_switch?
    discordId = str(interaction.user.id)
    async for db in get_async_db():
        user_result = await db.execute(select(User).where(User.discordId == discordId))
        user = user_result.scalar_one_or_none()
        if not user:
            return await interaction.response.send_message("❌ Erst `/register`!", ephemeral=True)
        
        chars_result = await db.execute(
            select(Character).where(Character.user_Id == user.id).order_by(Character.name)
        )
        chars = chars_result.scalars().all()
        
        if not chars:
            return await interaction.response.send_message("❌ Keine Charaktere! `/charcreate`", ephemeral=True)
    
    # ✅ SelectOption-Liste bauen!
    char_options = [
        discord.SelectOption(
            label=f"{c.name} ({c.apiClass.title()})", 
            value=str(c.id),
            description=f"Lvl {c.level} {'✅' if c.is_active else ''}"
        )
        for c in chars
    ]
    
    embed = discord.Embed(title=f"🔄 {len(chars)} Charaktere", color=0x3498db)
    active = next((c for c in chars if c.is_active), None)
    if active:
        embed.description = f"**Aktiv**: {active.name}"
    
    # ✅ NUR char_options übergeben!
    view = CharSwitchView(char_options)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)








import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from character_creation import setup


# Nach setup_slash_commands Definition:
async def setup_hook():
    from character_creation import setup
    await setup(bot)  # Cogs vor Sync!
    from tutorial import setup
    await setup(bot)
    # Slash Commands (GLOBAL)
    synced = await bot.tree.sync()
    print(f'🌍 Global sync: {len(synced)} commands')
    
    # GUILD Sync (schneller)
    guild_id = os.getenv('GUILD_ID')
    if guild_id:
        bot.tree.copy_global_to(guild=discord.Object(id=int(guild_id)))
        guild_synced = await bot.tree.sync(guild=discord.Object(id=int(guild_id)))
        print(f'✅ Guild {guild_id} synced: {len(guild_synced)}')
    
    # DB
    from database import Base, sync_engine
    Base.metadata.create_all(bind=sync_engine)
    
    print("✅ CharacterCog + Commands ready!")


bot.setup_hook = setup_hook

# Rest bleibt gleich...
bot.run(os.getenv('DISCORD_TOKEN'))
