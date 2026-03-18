import functools

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import random
from database import User, Character, InventoryItem, get_async_db
from sqlalchemy import select, func
import asyncio
import json

DND_API = "http://localhost:4588/api"

async def get_user(db, discordId, interaction):
    result = await db.execute(select(User).where(User.discordId == discordId))
    user = result.scalar_one_or_none()
    if not user:
        user = User(discordId=discordId, username=interaction.user.name, maxSlots=1)
        db.add(user)
        await db.flush()
    return user

async def slot_full(db, user):
    count = await db.execute(select(func.count()).select_from(Character).where(Character.user_Id == user.id))
    if count.scalar() >= user.maxSlots:
        return True
    return False

async def calculate_hp_ac(class_name: str, con_score: int, dex_score: int = 10, wis_score: int = 10, level: int = 1):
    """Präzise 5e HP/AC aus API"""
    if not class_name:
        return 10, 10, 10, "d8"
    
    con_mod = (con_score - 10) // 2
    dex_mod = (dex_score - 10) // 2
    wis_mod = (wis_score - 10) // 2
    
    class_lower = class_name.lower().replace(' ', '-')
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{DND_API}/classes/{class_lower}") as resp:
            if resp.status != 200:
                return 10, 10, 10, "d8"
            data = await resp.json()

    hit_die_num = data['hit_die']
    hit_die_avg = (hit_die_num / 2) + 1
    
    hp_level1 = hit_die_num + con_mod
    hp_max = hp_level1 + int((hit_die_avg + con_mod) * (level - 1))
    hp_current = hp_max
    
    ac = 10 + dex_mod
    if 'barbarian' in class_lower:
        ac = 10 + dex_mod + con_mod
    elif 'monk' in class_lower:
        ac = 10 + dex_mod + wis_mod
    elif class_lower in ['druid', 'ranger']:
        ac = 10 + dex_mod + wis_mod
    
    return hp_max, hp_current, ac, f"d{hit_die_num}"

async def validate_and_get_bonuses(race_name: str, class_name: str):
    """Validiert + Bonis (nur Race-Boni, da 5e keine Class-Boni hat)"""
    bonuses = {"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 0}
    
    async with aiohttp.ClientSession() as session:
        if race_name:
            race_lower = race_name.lower().replace(' ', '-')
            async with session.get(f"{DND_API}/races/{race_lower}") as resp:
                if resp.status != 200:
                    return None, f"❌ Rasse '{race_name}' ungültig!"
                race_data = await resp.json()
                if 'ability_bonuses' in race_data:
                    for bonus in race_data['ability_bonuses']:
                        score_index = bonus['ability_score']['index']
                        bonuses[score_index] += bonus['bonus']
        
        if class_name:
            class_lower = class_name.lower().replace(' ', '-')
            async with session.get(f"{DND_API}/classes/{class_lower}") as resp:
                if resp.status != 200:
                    return None, f"❌ Klasse '{class_name}' ungültig!"

    return bonuses, "✅ OK"

async def load_skills_options(self):
    async with self.cog.session.get(f"{DND_API}/skills") as resp:
        data = await resp.json()
    return [discord.SelectOption(label=s['name'], value=s['index']) for s in data['results']]


class CharacterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.temp_data = {}

    async def cog_unload(self):
        await self.session.close()

    async def load_race_options(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}/races") as resp:
                data = await resp.json()
                return [discord.SelectOption(label=r['name'], value=r['index']) for r in data['results'][:25]]

    async def load_class_options(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}/classes") as resp:
                data = await resp.json()
                return [discord.SelectOption(label=c['name'], value=c['index']) for c in data['results']]

    @app_commands.command(name='register', description='Einmalig registrieren')
    async def register(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        async for db in get_async_db():
            result = await db.execute(select(User).where(User.discordId == discord_id))
            if result.scalar_one_or_none():
                await interaction.followup.send("✅ Bereits registriert!", ephemeral=True)
                return
            user = User(discordId=discord_id, username=interaction.user.name, maxSlots=1)
            db.add(user)
            await db.commit()
        await interaction.followup.send("✅ Registriert! `/charcreate`", ephemeral=True)

    @app_commands.command(name='charcreate')
    async def char_create(self, interaction: discord.Interaction):
        discord_id = str(interaction.user.id)
        
        async for db in get_async_db():
            user = await get_user(db, discord_id, interaction)
            if await slot_full(db, user):
                await interaction.response.send_message(f"❌ Slots voll ({user.maxSlots}/5)!", ephemeral=True)
                return
        
        race_options = await self.load_race_options()
        class_options = await self.load_class_options()
        view = CharSelectView(race_options, class_options)
        embed = discord.Embed(title="1️⃣ Rasse & Klasse", description="Wähle aus!")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class CharSelectView(discord.ui.View):
    def __init__(self, race_options, class_options):
        super().__init__(timeout=300)
        self.selected_race = None
        self.selected_class = None
        
        self.race_menu = discord.ui.Select(
            placeholder="🏺 Rasse wählen...", min_values=1, max_values=1, options=race_options
        )
        self.race_menu.callback = self.race_callback
        
        self.class_menu = discord.ui.Select(
            placeholder="⚔️ Klasse wählen...", min_values=1, max_values=1, options=class_options
        )
        self.class_menu.callback = self.class_callback
        
        self.add_item(self.race_menu)
        self.add_item(self.class_menu)

    async def race_callback(self, interaction: discord.Interaction):
        self.selected_race = self.race_menu.values[0]
        embed = discord.Embed(title="✅ Rasse", description=self.selected_race.upper())
        await interaction.response.edit_message(embed=embed, view=self)

    async def class_callback(self, interaction: discord.Interaction):
        self.selected_class = self.class_menu.values[0]
        if self.selected_race and self.selected_class:
            await interaction.response.defer(ephemeral=True)
            cog = interaction.client.get_cog("CharacterCog")
            view = CharStepView(self.selected_race, self.selected_class, interaction.user.id, cog)
            embed = discord.Embed(title="🧙 Schritt-für-Schritt", description="Klicke Buttons!")
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return
        embed = discord.Embed(title="✅ Klasse", description=self.class_menu.values[0].upper())
        await interaction.response.edit_message(embed=embed, view=self)

class SkillsSelectView(discord.ui.View):
    def __init__(self, skills_options, parent, auto_profs=None):
        super().__init__(timeout=300)
        self.parent = parent
        self.auto_profs = auto_profs or set()  # z.B. {'acrobatics', 'stealth'} aus Race/Background
        self.selected_skills = set(self.auto_profs)  # Auto hinzufügen
        self.skills_options = skills_options  # Von load_skills_options
        
        self.select = discord.ui.Select(
            placeholder="Skills wählen (Auto: geladen)",
            min_values=0, max_values=6,  # Flexibel, abh. Class (z.B. Rogue 4)
            options=self.skills_options
        )
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        self.selected_skills.update(self.select.values)
        # Entferne Duplikate
        self.selected_skills = set(self.selected_skills)
        
        prof_count = len(self.selected_skills)
        embed = discord.Embed(
            title="Skills gespeichert!",
            description=f"{prof_count} Skills: {', '.join(self.selected_skills)[:100]}...\nAuto-Profs: {len(self.auto_profs)}",
            color=0x00ff00
        )
        if prof_count >= 2:  # Mindestanzahl, anpassbar
            self.parent.data['skillsprofs'] = list(self.selected_skills)  # Als Liste für JSON
            embed.add_field(name="Mods (Beispiel Level 1, Prof+2)", 
                           value="Athletics +5 (STR4+2), Stealth +4 (DEX2+2)", inline=False)
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
        else:
            embed.color = 0xffaa00
            await interaction.response.edit_message(embed=embed, view=self)

class BackgroundSelectView(discord.ui.View):
    def __init__(self, options, parent):
        super().__init__(timeout=300)
        self.parent = parent
        self.select = discord.ui.Select(options=options[:25], placeholder="Background wählen")
        self.select.callback = self.callback
        self.add_item(self.select)
    
    async def callback(self, interaction):
        bg_index = self.select.values[0]
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"{DND_API}/backgrounds/{bg_index}")
            bg_data = await resp.json()
        
        # Skills/Tools extrahieren
        skills = [s['name'] for s in bg_data.get('skill_proficiencies', [])]
        tools = [t['name'] for t in bg_data.get('tool_proficiencies', [])]
        langs = bg_data.get('starting_equipment', [{}])[0].get('equipment', {}).get('name', 'Common')
        
        self.parent.data['background_profs'] = {
            'skills': skills, 'tools': tools, 'languages': [langs]
        }
        self.parent.data['background_name'] = bg_data['name']
        
        embed = discord.Embed(title=f"✅ {bg_data['name']}", 
                            description=f"Skills: {', '.join(skills)}\nTools: {', '.join(tools)}")
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

class AlignmentSelectView(discord.ui.View):
    def __init__(self, options, parent):
        super().__init__(timeout=300)
        self.parent = parent
        self.select = discord.ui.Select(options=options, placeholder="Alignment wählen")
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction):
        align_index = self.select.values[0]
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"{DND_API}/alignments/{align_index}")
            align_data = await resp.json()
        
        self.parent.data['alignment'] = {
            'index': align_data['index'],
            'name': align_data['name'],
            'url': align_data['url']
        }
        
        embed = discord.Embed(
            title=f"✅ {align_data['name']}", 
            description=f"**{align_data['index'].replace('-',' ').title()}** gespeichert"
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

class SavingThrowsSelectView(discord.ui.View):
    def __init__(self, saves_options, parent, auto_profs=None):
        super().__init__(timeout=300)
        self.parent = parent
        self.auto_profs = auto_profs or set()  # z.B. {'str', 'con'} aus Class
        self.selected_saves = set(self.auto_profs)
        self.saves_options = saves_options
        
        self.select = discord.ui.Select(
            placeholder="Saving Throws (Auto: geladen)",
            min_values=0, max_values=2,  # Typisch 2 Profs
            options=self.saves_options
        )
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        self.selected_saves.update(self.select.values)
        self.selected_saves = set(self.selected_saves)  # Dedupe
        
        count = len(self.selected_saves)
        embed = discord.Embed(
            title="Saving Throws gespeichert!",
            description=f"{count} Profs: {', '.join(self.selected_saves).upper()}\nAuto: {len(self.auto_profs)}",
            color=0x00ff00
        )
        if count >= 2:
            self.parent.data['saveprofs'] = list(self.selected_saves)
            # Beispiel-Mods (Level 1 Prof+2)
            stats = self.parent.data.get('stats', {})
            mods_str = ', '.join([f"{s.upper()}: +{calculate_save_mod(s, stats)}" for s in self.selected_saves])
            embed.add_field(name="Mods (Beispiel)", value=mods_str, inline=False)
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
        else:
            embed.color = 0xffaa00
            embed.description += "\nWähle mind. 2 (Class-Standard)"
            await interaction.response.edit_message(embed=embed, view=self)

class PersonalitySelectView(discord.ui.View):
    def __init__(self, bg_index, parent):
        super().__init__(timeout=300)
        self.parent = parent
        self.bg_index = bg_index  # Aus Background-Select
        self.data = {'traits': [], 'ideal': '', 'bond': '', 'flaw': ''}

    async def load_bg_personality(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}backgrounds/{self.bg_index}") as resp:
                if resp.status == 200:
                    bg_data = await resp.json()
                    return {
                        'traits': bg_data.get('personality_traits_options', []),
                        'ideals': bg_data.get('ideal_options', []),
                        'bonds': bg_data.get('bond_options', []),
                        'flaws': bg_data.get('flaw_options', [])
                    }
        return {'traits': [], 'ideals': [], 'bonds': [], 'flaws': []}

    @discord.ui.button(label="Traits wählen (2)", style=discord.ButtonStyle.primary, row=0)
    async def traits_step(self, interaction: discord.Interaction, button):
        options = await self.load_bg_personality()
        trait_opts = [discord.SelectOption(label=t['desc'][:50], value=t['index']) for t in options['traits'][:25]]
        modal = TraitModal(self, trait_opts, 'traits')
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Ideal/Bond/Flaw", style=discord.ButtonStyle.secondary, row=0)
    async def ideals_step(self, interaction: discord.Interaction, button):
        modal = PersonalityModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Speichern", style=discord.ButtonStyle.green, row=1)
    async def save_personality(self, interaction: discord.Interaction, button):
        if len(self.data['traits']) < 2:
            return await interaction.response.send_message("Wähle 2 Traits!", ephemeral=True)
        self.parent.data['personality'] = self.data
        embed = discord.Embed(title="Personality OK!", 
                             description=f"Traits: {', '.join(self.data['traits'])}\nIdeal: {self.data['ideal'][:50]}", 
                             color=0x00ff00)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

class TraitModal(discord.ui.Modal, title="Traits wählen"):
    def __init__(self, view, options, field):
        super().__init__()
        self.view = view
        self.field = field
        self.select = discord.ui.Select(options=options[:25], min_values=1, max_values=2)
        self.add_item(self.select)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.data[self.field] = self.select.values
        embed = discord.Embed(title=f"{self.field.title()} gespeichert", color=0x00ff00)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class PersonalityModal(discord.ui.Modal, title="Ideal/Bond/Flaw"):
    ideal = discord.ui.TextInput(label="Ideal", placeholder="z.B. Beauty", max_length=200)
    bond = discord.ui.TextInput(label="Bond", placeholder="Mein Dorf", max_length=200)
    flaw = discord.ui.TextInput(label="Flaw", placeholder="Arrogant", max_length=200)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        self.view.data['ideal'] = self.ideal.value
        self.view.data['bond'] = self.bond.value
        self.view.data['flaw'] = self.flaw.value
        embed = discord.Embed(title="Personality Details OK!", color=0x00ff00)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def load_saves_options(self):
    # Hardcode 6 Saves (kein API-Endpoint nötig, standardisiert)
    saves = [
        {'index': 'str', 'name': 'Strength'},
        {'index': 'dex', 'name': 'Dexterity'},
        {'index': 'con', 'name': 'Constitution'},
        {'index': 'int', 'name': 'Intelligence'},
        {'index': 'wis', 'name': 'Wisdom'},
        {'index': 'cha', 'name': 'Charisma'}
    ]
    return [discord.SelectOption(label=s['name'], value=s['index']) for s in saves]

async def savesstep(self, interaction, button):
    if 'stats' not in self.data or len(self.data['stats']) != 6:
        return await interaction.response.send_message("Zuerst Stats!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    
    # Auto aus Class laden (parse /api/classes/{self.class}/saving_throws)
    auto_profs = await self.load_class_save_profs()  # Siehe unten
    options = await self.load_saves_options()
    view = SavingThrowsSelectView(options, self, auto_profs)
    embed = discord.Embed(title="Saving Throws wählen", description="Class-Profs auto + anpassen")
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def load_class_save_profs(self):
    async with self.cog.session.get(f"{DND_API}/classes/{self.class_}") as resp:
        if resp.status == 200:
            data = await resp.json()
            return {save['index'] for save in data.get('saving_throws', [])}
    return set()  # Fallback

def calculate_save_mod(save_ability, stats, prof_bonus=2, profs_set=None):
    mod = (stats.get(save_ability, 10) - 10) // 2
    if profs_set and save_ability in profs_set:
        mod += prof_bonus
    return mod


class SpellSlotsView(discord.ui.View):
    def __init__(self, slots_data, parent):
        super().__init__(timeout=300)
        self.parent = parent
        self.slots = slots_data  # {1: {'max':2, 'current':2}, ...}

    @discord.ui.select(placeholder="Level wählen")
    async def level_select(self, interaction: discord.Interaction, select):
        # Dynamisch Options setzen (oder pre-load)
        options = [discord.SelectOption(label=f"L{lvl}: {data['current']}/{data['max']}", value=str(lvl)) 
                  for lvl, data in self.slots.items()]
        select.options = options[:25]  # Max 25
        self.level = int(select.values[0]) if select.values else 1
        await self.update_embed(interaction)  # Deine update Funktion


    @discord.ui.button(label="Short Rest", style=discord.ButtonStyle.blurple)
    async def short_rest(self, interaction: discord.Interaction, button):
        # Warlock: Full refresh; andere: keine Änderung
        if self.parent.data['apiClass'] == 'warlock':
            self.slots[self.level]['current'] = self.slots[self.level]['max']
        await self.update_embed(interaction)

    @discord.ui.button(label="Long Rest", style=discord.ButtonStyle.green)
    async def long_rest(self, interaction: discord.Interaction, button):
        for lvl in self.slots:
            self.slots[lvl]['current'] = self.slots[lvl]['max']
        self.parent.data['spellslots'] = self.slots  # Persist
        await self.update_embed(interaction)

    async def update_embed(self, interaction):
        slots_str = f"Level {self.level}: {self.slots[self.level]['current']}/{self.slots[self.level]['max']}"
        embed = discord.Embed(title=f"Spell Slots (Level {self.parent.data['level']})", 
                             description=slots_str, color=0x9900ff)
        embed.add_field(name="Alle Levels", 
                       value='\n'.join([f"L{lvl}: {data['current']}/{data['max']}" for lvl, data in self.slots.items()]), 
                       inline=True)
        await interaction.response.edit_message(embed=embed, view=self)

class AttacksSelectView(discord.ui.View):
    def __init__(self, parent, equip_data, spells_data, stats, prof_bonus=2):
        super().__init__(timeout=300)
        self.parent = parent
        self.stats = stats  # {'str':15, 'dex':14...}
        self.prof_bonus = prof_bonus
        self.attacks = self.generate_attacks(equip_data, spells_data)

    def generate_attacks(self, equip, spells):
        attacks = []
        # Weapons aus Equipment
        for item in equip:
            if 'equipment' in item and 'index' in item['equipment']:
                w_index = item['equipment']['index']
                # Async API-Call in real: damage dice/type, properties (finesse?)
                w_data = {'name': item.get('name', 'Weapon'), 'dice': '1d8', 'dmg_type': 'piercing', 
                         'ability': 'str', 'prof': True}  # Finesse -> dex
                to_hit = self.prof_bonus + ((self.stats[w_data['ability']] - 10) // 2)
                dmg_bonus = (self.stats[w_data['ability']] - 10) // 2
                attacks.append({
                    'name': w_data['name'],
                    'to_hit': f"d20 + {to_hit}",
                    'damage': f"{w_data['dice']} + {dmg_bonus} {w_data['dmg_type']}"
                })

        # Spell Attacks (nur Attack Rolls, z.B. Fire Bolt)
        for spell_index in spells:
            s_data = {'name': spell_index.replace('-', ' ').title(), 'dice': '2d10', 'dmg_type': 'fire', 
                     'ability': 'cha'}  # Spell Stat (cha für Sorcerer etc.)
            spell_stat_mod = (self.stats[s_data['ability']] - 10) // 2
            to_hit = self.prof_bonus + spell_stat_mod
            attacks.append({
                'name': f"Spell: {s_data['name']}",
                'to_hit': f"d20 + {to_hit}",
                'damage': f"{s_data['dice']} {s_data['dmg_type']}"  # Oft keine Mod auf Damage
            })
        return attacks[:10]  # Limit

    @discord.ui.button(label="Attacks anzeigen", style=discord.ButtonStyle.primary)
    async def show_attacks(self, interaction: discord.Interaction, button):
        attacks_str = '\n'.join([f"**{a['name']}**: {a['to_hit']} | {a['damage']}" for a in self.attacks])
        embed = discord.Embed(title="Deine Attacks (Level 1)", description=attacks_str, color=0xff6600)
        embed.add_field(name="Formel", value="**To Hit**: d20 + Prof(2) + Mod\n**Dmg**: Dice + Mod", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Speichern", style=discord.ButtonStyle.green)
    async def save_attacks(self, interaction: discord.Interaction, button):
        self.parent.data['attacks'] = self.attacks
        embed = discord.Embed(title="Attacks gespeichert!", description="Verwende /charinfo", color=0x00ff00)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

class CombatTrackerView(discord.ui.View):
    def __init__(self, combat_data, parent):  # {'inspiration': False, 'temp_hp': 0, 'hit_dice_used': 0, 'death_saves': {'success':0, 'fail':0}}
        super().__init__(timeout=None)  # Persistent!
        self.parent = parent
        self.data = combat_data

    @discord.ui.button(label="Inspiration", style=discord.ButtonStyle.blurple, emoji="⭐")
    async def toggle_inspiration(self, interaction: discord.Interaction, button):
        self.data['inspiration'] = not self.data['inspiration']
        button.label = "Inspiration" + (" ON" if self.data['inspiration'] else " OFF")
        button.style = discord.ButtonStyle.green if self.data['inspiration'] else discord.ButtonStyle.blurple
        self.parent.data['combat'] = self.data  # Persist
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="+Temp HP", style=discord.ButtonStyle.secondary, emoji="❤️")
    async def add_temp_hp(self, interaction: discord.Interaction, button):
        self.data['temp_hp'] += 5  # Oder Modal für Wert
        await self.update_embed(interaction)

    @discord.ui.button(label="-Temp HP", style=discord.ButtonStyle.danger, emoji="💔")
    async def sub_temp_hp(self, interaction: discord.Interaction, button):
        self.data['temp_hp'] = max(0, self.data['temp_hp'] - 5)
        await self.update_embed(interaction)

    @discord.ui.button(label="Hit Die (Spend)", style=discord.ButtonStyle.grey, emoji="🎲")
    async def spend_hit_die(self, interaction: discord.Interaction, button):
        if self.data['hit_dice_used'] < 1:  # Max 1 L1
            self.data['hit_dice_used'] += 1
        await self.update_embed(interaction)

    @discord.ui.button(label="Death Save +", style=discord.ButtonStyle.success, emoji="✅", row=1)
    async def death_success(self, interaction: discord.Interaction, button):
        self.data['death_saves']['success'] = min(3, self.data['death_saves']['success'] + 1)
        if self.data['death_saves']['success'] == 3:
            button.disabled = True
        await self.update_embed(interaction)

    @discord.ui.button(label="Death Save Fail", style=discord.ButtonStyle.danger, emoji="❌", row=1)
    async def death_fail(self, interaction: discord.Interaction, button):
        self.data['death_saves']['fail'] = min(3, self.data['death_saves']['fail'] + 1)
        if self.data['death_saves']['fail'] == 3:
            button.disabled = True
        await self.update_embed(interaction)

    @discord.ui.button(label="Reset Combat", style=discord.ButtonStyle.red, row=1)
    async def reset(self, interaction: discord.Interaction, button):
        self.data = {'inspiration': False, 'temp_hp': 0, 'hit_dice_used': 0, 'death_saves': {'success':0, 'fail':0}}
        self.parent.data['combat'] = self.data
        await self.update_embed(interaction)

    async def update_embed(self, interaction):
        embed = discord.Embed(title="Combat Tracker", color=0x00ff00)
        embed.add_field(name="Inspiration", value="✅" if self.data['inspiration'] else "❌", inline=True)
        embed.add_field(name="Temp HP", value=self.data['temp_hp'], inline=True)
        embed.add_field(name="Hit Dice Used", value=f"{self.data['hit_dice_used']}/1", inline=True)
        embed.add_field(name="Death Saves", value=f"S: {self.data['death_saves']['success']}/3 | F: {self.data['death_saves']['fail']}/3", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

class ToolsLanguagesSelectView(discord.ui.View):
    def __init__(self, parent, tool_options, lang_options, auto_tools=None, auto_langs=None):
        super().__init__(timeout=300)
        self.parent = parent
        self.auto_tools = auto_tools or set()  # {'thieves-tools', 'navigators-tools'}
        self.auto_langs = auto_langs or set()  # {'common', 'elvish'}
        self.selected_tools = set(self.auto_tools)
        self.selected_langs = set(self.auto_langs)
        self.tool_options = tool_options
        self.lang_options = lang_options

    @discord.ui.button(label="Tools wählen", style=discord.ButtonStyle.primary, row=0)
    async def tools_step(self, interaction: discord.Interaction, button):
        select = discord.ui.Select(placeholder="Tools (max 2 extra)", options=self.tool_options[:25], min_values=0, max_values=2)
        await interaction.response.send_message("Wähle Tools:", view=ToolSelectView(select, self), ephemeral=True)

    @discord.ui.button(label="Languages wählen", style=discord.ButtonStyle.secondary, row=0)
    async def langs_step(self, interaction: discord.Interaction, button):
        select = discord.ui.Select(placeholder="Languages (1 extra)", options=self.lang_options[:25], min_values=0, max_values=1)
        await interaction.response.send_message("Wähle Language:", view=LangSelectView(select, self), ephemeral=True)

    @discord.ui.button(label="Speichern", style=discord.ButtonStyle.green, row=1)
    async def save_profs(self, interaction: discord.Interaction, button):
        self.parent.data['toolprofs'] = list(self.selected_tools)
        self.parent.data['langprofs'] = list(self.selected_langs)
        embed = discord.Embed(
            title="Tools & Languages OK!",
            description=f"Tools: {len(self.selected_tools)} ({', '.join(self.selected_tools)[:50]})\nLangs: {len(self.selected_langs)} ({', '.join(self.selected_langs)})",
            color=0x00ff00
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

class ToolSelectView(discord.ui.View):
    def __init__(self, select, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.add_item(select)
        select.callback = self.callback

    async def callback(self, interaction):
        self.parent_view.selected_tools.update(select.values)
        embed = discord.Embed(title="Tools gespeichert!", color=0x00ff00)
        await interaction.response.edit_message(embed=embed, view=None)

class LangSelectView(discord.ui.View):  # Ähnlich für Languages
    def __init__(self, select, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.add_item(select)
        select.callback = self.callback

    async def callback(self, interaction):
        self.parent_view.selected_langs.update(select.values)
        embed = discord.Embed(title="Langs gespeichert!", color=0x00ff00)
        await interaction.response.edit_message(embed=embed, view=None)

async def load_tool_options(self):
    async with self.cog.session.get(f"{DND_API}proficiencies?type=tool") as resp:  # Oder equipment-categories/tool
        data = await resp.json()
    return [discord.SelectOption(label=p['name'], value=p['index']) for p in data.get('results', [])[:25]]

async def load_lang_options(self):
    async with self.cog.session.get(f"{DND_API}languages") as resp:
        data = await resp.json()
    return [discord.SelectOption(label=l['name'], value=l['index']) for l in data.get('results', [])]

async def load_auto_profs(self):
    # Merge aus Race/Class/Background (schon teilweise in Code)
    auto_tools = self.data.get('backgroundprofs', {}).get('tools', [])
    auto_langs = self.data.get('backgroundprofs', {}).get('languages', [])
    return auto_tools, auto_langs


class CharStepView(discord.ui.View):
    def __init__(self, race, class_, user_id, cog):
        super().__init__(timeout=1800)
        self.race = race
        self.class_ = class_
        self.user_id = user_id
        self.cog = cog
        self.data = {
            'name': None, 'gender': None, 'age': 25, 
            'background': 'Adventurer', 'description': 'Neuer Held',
            'stats': {}, 'prepared_spells': None
        }
        self.data['equipment'] = []
        self.data['skillsprofs'] = {}
        self.data['combat'] = {'inspiration': False, 'temp_hp': 0, 'hit_dice_used': 0, 'death_saves': {'success':0, 'fail':0}}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Nur der Ersteller!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="1️⃣ Name", style=discord.ButtonStyle.primary, row=0)
    async def name_step(self, interaction: discord.Interaction, button):
        modal = NameDetailsModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="2️⃣ Stats 1/2", style=discord.ButtonStyle.secondary, row=0)
    async def stats1_step(self, interaction: discord.Interaction, button):
        # if not self.data['name']:
        #     await interaction.response.send_message("❌ Schritt 1 zuerst!", ephemeral=True)
        #     return
        modal = StatsModal1(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="3️⃣ Stats 2/2", style=discord.ButtonStyle.secondary, row=0)
    async def stats2_step(self, interaction: discord.Interaction, button):
        # if len(self.data['stats']) < 3:
        #     await interaction.response.send_message("❌ Stats 1/3 zuerst!", ephemeral=True)
        #     return
        modal = StatsModal2(self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="4️⃣ Details", style=discord.ButtonStyle.secondary, row=0)
    async def details_step(self, interaction: discord.Interaction, button):
        # if not self.data['stats']:
        #     await interaction.response.send_message("❌ Zuerst Stats!", ephemeral=True)
        #     return
        modal = DetailsModal(self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="5️⃣ API-Spells", style=discord.ButtonStyle.blurple, row=1)
    async def spells_step(self, interaction: discord.Interaction, button):
        # if len(self.data['stats']) != 6:
        #     await interaction.response.send_message("❌ Zuerst alle 6 Stats!", ephemeral=True)
        #     return
        
        await interaction.response.defer(ephemeral=True)  # IMMER defer bei async API
        
        try:
            spells_data = await self.load_api_spells()
            view = SpellSelectView(spells_data, self)
            embed = discord.Embed(
                title="✨ API-Spells laden", 
                description=f"**{len(spells_data['cantrips'])} Cantrips + {len(spells_data['l1_spells'])} L1**\nWähle {spells_data['known_count']}"
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ API-Fehler: {e}\nSkip Spells (setze 0)", ephemeral=True)


    async def load_api_spells(self):
        """Lädt class-spezifische Spells komplett aus API (mit Fehlerhandling)"""
        cantrips = []
        spells_l1 = []
        spells_known_num = 0
        
        async with self.cog.session as session:
            try:
                # Level 1 Daten laden
                levels_url = f"{DND_API}/classes/{self.class_}/levels/1"
                async with session.get(levels_url) as resp:
                    if resp.status == 200:
                        levels_data = await resp.json()
                        spellcasting = levels_data.get('spellcasting', {})
                        spells_known_num = spellcasting.get('spells_known', 0)
                        
                        # Features nach Spells durchsuchen (SAFE!)
                        features = levels_data.get('features', [])
                        for feature in features:
                            feature_url = feature.get('url') if isinstance(feature, dict) else feature
                            if not isinstance(feature_url, str) or not feature_url.startswith('http'):
                                continue  # Skip None/null/nicht-URL
                            
                            try:
                                async with session.get(feature_url) as f_resp:
                                    if f_resp.status == 200:
                                        f_data = await f_resp.json()
                                        if 'spell' in f_data.get('index', ''):
                                            spell_index = f_data['index']
                                            spell_level = f_data.get('level', 1)
                                            if spell_level == 'cantrip' or spell_level == 0:
                                                cantrips.append(spell_index)
                                            elif spell_level == 1:
                                                spells_l1.append(spell_index)
                            except Exception:
                                continue  # Skip fehlerhafte Features
            except Exception as e:
                print(f"Level API Error: {e}")  # Debug
            
            # Fallback: Direkte Spell-Listen (schneller + robust)
            try:
                # Cantrips (level=0)
                async with session.get(f"{DND_API}/spells?level=0") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        cantrips.extend([s['index'] for s in data['results'][:8]])
                
                # L1 Spells
                async with session.get(f"{DND_API}/spells?level=1") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        spells_l1.extend([s['index'] for s in data['results'][:12]])
            except Exception as e:
                print(f"Fallback API Error: {e}")
        
        # Dedupe + Limit
        cantrips = list(set(cantrips))[:8]
        spells_l1 = list(set(spells_l1))[:12]
        
        return {
            'cantrips': cantrips,
            'l1_spells': spells_l1,
            'known_count': max(1, spells_known_num or 2)  # Default 2 wenn 0
        }
    
    async def load_spell_slots(self):
        # Holt levels/1 für Level 1
        levels_url = f"{DND_API}/classes/{self.class_}/levels/1"
        async with self.cog.session.get(levels_url) as resp:
            if resp.status == 200:
                level_data = await resp.json()
                spellcasting = level_data.get('spellcasting', {})
                slots = {}
                for slot_level, count in spellcasting.get('spell_slots', {}).items():
                    slots[int(slot_level)] = {'max': count, 'current': count}
                return slots
        return {1: {'max': 0, 'current': 0}}  # Non-Caster

    
    @discord.ui.button(label="6️⃣ Equipment", style=discord.ButtonStyle.blurple, row=1)
    async def equipment_step(self, interaction: discord.Interaction, button):
        await interaction.response.defer(ephemeral=True)
        equip_data = await self.load_api_equipment()
        
        if not equip_data['options'] and not equip_data['fixed']:
            await interaction.followup.send("❌ Kein Equipment gefunden", ephemeral=True)
            return
        
        view = EquipmentSelectView(equip_data, self)
        embed = discord.Embed(title="🛡️ Starting Equipment", description=f"{len(equip_data['fixed'])} Fix + {len(equip_data['options'])} Wahlen")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)



    # Neue load_api_equipment Methode (ähnlich load_api_spells):
    async def load_api_equipment(self):
        async with self.cog.session as session:
            class_url = f"{DND_API}/classes/{self.class_}"
            print(f"Loading equipment from {class_url}")
            
            async with session.get(class_url) as resp:
                if resp.status != 200: return {'fixed': [], 'options': []}
                class_data = await resp.json()
            
            fixed = []
            for item in class_data.get('starting_equipment', []):
                if isinstance(item, dict) and 'equipment' in item:
                    fixed.append({'name': item['equipment']['name'], 'index': item['equipment']['index']})
            
            options = class_data.get('starting_equipment_options', [])
            
            def find_all_categories(opt_from):
                """BRUTEFORCE - findet JEDES equipment_category, egal wie tief."""
                categories = []
                
                def search(node):
                    if not isinstance(node, dict): return
                    if 'equipment_category' in node:
                        cat = node['equipment_category']
                        if isinstance(cat, dict) and cat.get('index'):
                            categories.append(cat)
                    for v in node.values():
                        search(v)
                
                search(opt_from)
                return categories
            
            # 🔥 HARDCODE Patterns für die 3 fehlenden Warlock OPTs
            for i, opt in enumerate(options):
                print(f"\n--- OPT {i}: '{opt.get('desc','?')[:60]}' ---")
                
                # 1. Normale Suche
                categories = find_all_categories(opt.get('from', {}))
                
                # 2. HARDCODE für bekannte Patterns
                from_node = opt.get('from', {})
                if from_node.get('options'):
                    for opt_item in from_node['options']:
                        # Warlock OPT0/1: options[1].choice.from.equipment_category
                        if opt_item.get('choice'):
                            choice_from = opt_item['choice'].get('from', {})
                            cat = choice_from.get('equipment_category')
                            if cat and cat.get('index'):
                                categories.append(cat)
                                print(f"  HARDCODE: Found {cat['index']} in choice")
                
                print(f"  Total {len(categories)} categories:")
                for cat in categories:
                    print(f"    → {cat['index']}")
                
                # Laden...
                opt['choices'] = []
                opt['choose'] = opt.get('choose', 1)
                seen = set()
                
                for cat in categories:
                    cat_index = cat['index']
                    try:
                        async with session.get(f"{DND_API}/equipment-categories/{cat_index}") as cat_resp:
                            if cat_resp.status == 200:
                                cat_data = await cat_resp.json()
                                for eq in cat_data.get('equipment', [])[:8]:
                                    name = eq['name']
                                    if name not in seen:
                                        opt['choices'].append(name)
                                        seen.add(name)
                                print(f"  Loaded {len(opt['choices'])} from {cat_index}")
                    except:
                        opt['choices'].append(f"[Failed {cat_index}]")
                
                opt['choices'] = opt['choices'][:25]
                print(f"  ✅ {len(opt['choices'])} Choices")
            
            print(f"\n🎯 DONE: {len(fixed)} fixed, {sum(len(o['choices']) for o in options)} total")
            return {'fixed': fixed, 'options': options}






    @discord.ui.button(label="7️⃣ Background", style=discord.ButtonStyle.secondary, row=1)
    async def background_step(self, interaction: discord.Interaction, button):
        # API: https://DND_API/backgrounds
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}/backgrounds") as resp:
                data = await resp.json()
                bg_options = [discord.SelectOption(label=b['name'], value=b['index']) for b in data['results']]
        
        view = BackgroundSelectView(bg_options, self)
        embed = discord.Embed(title="📜 Background (Skills/Tools)", description="Gibt Profs!")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


    @discord.ui.button(label="🧭 8️⃣ Alignment", style=discord.ButtonStyle.secondary, row=1)
    async def alignment_step(self, interaction: discord.Interaction, button):
        """API-Alignment Auswahl (dynamisch!)"""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}/alignments") as resp:
                data = await resp.json()
                align_options = [discord.SelectOption(label=a['name'], value=a['index']) 
                            for a in data['results'][:9]]  # Nur 9 Standard
        
        view = AlignmentSelectView(align_options, self)
        embed = discord.Embed(title="🧭 Alignment wählen", description="API-Daten geladen!")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


    @discord.ui.button(label="9 Skills", style=discord.ButtonStyle.secondary, row=2)
    async def skillsstep(self, interaction: discord.Interaction, button):
        if len(self.data['stats']) != 6:
            await interaction.response.send_message("Zuerst Stats!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        skills_options = await load_skills_options(self)
        view = SkillsSelectView(skills_options, self)
        embed = discord.Embed(title="Skills wählen", description="Auto-Profs geladen + wähle frei")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🔟 Saving Throws", style=discord.ButtonStyle.secondary, row=2)
    async def savestep(self, interaction: discord.Interaction, button):
        if len(self.data['stats']) != 6:
            await interaction.response.send_message("Zuerst Stats!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        auto_profs = await self.load_class_save_profs()
        options = await self.load_saves_options()
        view = SavingThrowsSelectView(options, self, auto_profs)
        embed = discord.Embed(title="Saving Throws wählen", description="Class-Profs auto + anpassen")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="11 Personality", style=discord.ButtonStyle.secondary, row=2)
    async def personalitystep(self, interaction, button):
        if 'background' not in self.data:
            return await interaction.response.send_message("Zuerst Background!", ephemeral=True)
        bg_index = self.data['background'].get('index', 'adventurer')  # Aus Background data
        await interaction.response.defer(ephemeral=True)
        view = PersonalitySelectView(bg_index, self)
        embed = discord.Embed(title="Personality Traits (aus Background)", description="Klicke Buttons!")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="12 Spell Slots", style=discord.ButtonStyle.blurple, row=2)
    async def spellslotsstep(self, interaction, button):
        if self.data.get('spellclass') is None:
            return await interaction.response.send_message("Nur für Caster!", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        slots = await self.load_spell_slots()
        view = SpellSlotsView(self, slots)
        embed = discord.Embed(title="Spell Slots laden", description="Track & Rest!")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="13 Attacks", style=discord.ButtonStyle.primary, row=3)
    async def attacksstep(self, interaction, button):
        if not all(k in self.data for k in ['stats', 'equipment', 'preparedspells']):
            return await interaction.response.send_message("Zuerst Stats/Equip/Spells!", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        equip = json.loads(self.data['equipment']) if self.data['equipment'] else []
        spells = json.loads(self.data['preparedspells']) if self.data['preparedspells'] else []
        prof_bonus = 2  # Level 1
        view = AttacksSelectView(self, equip, spells, self.data['stats'], prof_bonus)
        embed = discord.Embed(title="Attacks generieren", description="Aus Equipment + Spells")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="14 Combat Tracker", style=discord.ButtonStyle.red, row=3)
    async def combattrackerstep(self, interaction, button):
        combat_data = self.data.get('combat', {'inspiration': False, 'temp_hp': 0, 'hit_dice_used': 0, 'death_saves': {'success':0, 'fail':0}})
        view = CombatTrackerView(combat_data, self)
        embed = discord.Embed(title="Combat Tracker aktivieren", description="Persistent für Sessions!")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)  # Nicht ephemeral, sharebar

    @discord.ui.button(label="15 Tools/Languages", style=discord.ButtonStyle.secondary, row=3)
    async def toolslangsstep(self, interaction, button):
        tool_opts = await self.load_tool_options()
        lang_opts = await self.load_lang_options()
        auto_tools, auto_langs = await self.load_auto_profs()
        view = ToolsLanguagesSelectView(tool_opts, lang_opts, auto_tools, auto_langs, self)
        embed = discord.Embed(title="Tools & Languages", description="Auto + Extra wählen")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


    @discord.ui.button(label="✅ Fertig", style=discord.ButtonStyle.green, row=3)
    async def finish(self, interaction: discord.Interaction, button):
        if not all([self.data['name'], len(self.data['stats']) == 6, 'equipment' in self.data]):
            await interaction.response.send_message("❌ Name + Stats + Equipment!", ephemeral=True)
            return
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await self.create_character(interaction)

    @discord.ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.danger, row=4)
    async def cancel(self, interaction: discord.Interaction, button):
        embed = discord.Embed(title="❌ Abgebrochen", color=0xff0000)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.stop()

    async def create_character(self, interaction):
        discordId = str(interaction.user.id)
        bonuses, valid_msg = await validate_and_get_bonuses(self.race, self.class_)
        if not bonuses:
            await interaction.followup.send(valid_msg, ephemeral=True)
            return

        # Final Stats
        raw_stats = self.data['stats']
        final_stats = {}
        for ab in ['str', 'dex', 'con', 'int', 'wis', 'cha']:
            base = raw_stats.get(ab, 10)
            final_stats[f"{ab}_score"] = base + bonuses.get(ab, 0)

        # API Daten laden
        async with aiohttp.ClientSession() as session:
            class_url = f"{DND_API}/classes/{self.class_}"
            race_url = f"{DND_API}/races/{self.race}"
            
            class_resp = await session.get(class_url)
            race_resp = await session.get(race_url)
            
            class_data = await class_resp.json() if class_resp.status == 200 else {}
            race_data = await race_resp.json() if race_resp.status == 200 else {}

            languages = []
            if race_data.get('languages'):
                for lang_url in race_data['languages'][:3]:
                    lang_resp = await self.cog.session.get(lang_url)
                    if lang_resp.status == 200:
                        languages.append((await lang_resp.json())['name'])
            # + Background langs → char.languages = json.dumps(languages)


        # Proficiencies (cog session)
        proficiencies = []
        armor_profs = []
        weapon_profs = []
        if 'proficiencies' in class_data:
            for p_url in class_data['proficiencies'][:6]:
                try:
                    p_resp = await self.cog.session.get(p_url)
                    if p_resp.status == 200:
                        p_data = await p_resp.json()
                        prof_name = p_data.get('name', '').lower()
                        proficiencies.append(p_data['name'])
                        if any(word in prof_name for word in ['armor', 'shield']):
                            armor_profs.append(p_data['name'])
                        elif any(word in prof_name for word in ['weapon', 'simple', 'martial']):
                            weapon_profs.append(p_data['name'])
                except Exception:
                    continue
        
        # Race Features
        race_features = []
        if race_data.get('traits'):
            for trait_url in race_data['traits'][:3]:
                try:
                    trait_resp = await self.cog.session.get(trait_url)
                    trait_data = await trait_resp.json() if trait_resp.status == 200 else {}
                    if 'spell' in trait_data.get('index', '').lower():
                        race_features.append(trait_data['name'])
                except:
                    pass

        # Spell Class
        spell_class = self.class_ if class_data.get('spellcasting') else None
        spells_known = 0  # Wird via prepared_spells gehandhabt

        # HP/AC/Speed
        hp_max, hp_current, ac_base, hit_die = await calculate_hp_ac(
            self.class_, final_stats['con_score'], final_stats['dex_score'], final_stats['wis_score']
        )
        speed = race_data.get('speed', 30)

        # Prepared Spells handhaben
        prepared_spells_raw = self.data.get('prepared_spells')
        if prepared_spells_raw:
            spells_list = [s.strip() for s in prepared_spells_raw.split(',') if s.strip()]
            safe_spells = [s[:30] for s in spells_list if len(s) <= 30][:8]  # Max 8
            
            prepared_spells = json.dumps(safe_spells)  # ["light","fire-bolt"]
            spells_known = len(safe_spells)
        
        equipment_json = self.data.get('equipment', '[]')  # Bereits JSON-String
        skillsprofs_json = json.dumps(self.data.get('skillsprofs', []))
        saveprofs_json = json.dumps(self.data.get('saveprofs', []))
        personality_json = json.dumps(self.data.get('personality', {}))
        spellslots_json = json.dumps(self.data.get('spellslots', {}))
        attacks_json = json.dumps(self.data.get('attacks', []))
        combat_json = json.dumps(self.data.get('combat', {}))
        toolprofs_json = json.dumps(self.data.get('toolprofs', []))
        langprofs_json = json.dumps(self.data.get('langprofs', []))

        # DB Speichern
        async for db in get_async_db():
            user = await get_user(db, discordId, interaction)
            if await slot_full(db, user):
                await interaction.followup.send("❌ Slots voll!", ephemeral=True)
                return
            
            char = Character(
                user_Id=user.id,
                name=self.data['name'],
                gender=self.data['gender'],
                age=self.data['age'],
                background=self.data['background'],
                description=self.data['description'],
                apiRace=self.race, 
                apiClass=self.class_,
                level=1,
                **final_stats,
                hp_max=hp_max, hp_current=hp_current,
                ac_base=ac_base, hit_die=hit_die,
                xp=0, xp_next=300,
                features="[]",
                image_path=None,
                is_active=0,
                alignment=self.data.get('alignment', {}).get('index', 'neutral'),
                speed=speed,
                initiative=(final_stats['dex_score']-10)//2,
                passive_perc=10+(final_stats['wis_score']-10)//2,
                spell_class=spell_class,
                spells_known=spells_known,
                spell_slots=spellslots_json,
                prepared_spells=prepared_spells,
                starting_equipment=equipment_json,
                skillsprofs = skillsprofs_json,
                saveprofs = saveprofs_json,
                personalitystr = personality_json,
                attacksstr=attacks_json,
                combatstr=combat_json,
                toolprofstr=toolprofs_json,
                langprofstr=langprofs_json,
                armor_prof=str(armor_profs)[:100],
                weapon_prof=str(weapon_profs)[:100],
                **{f"{s}_save_prof": 0 for s in ['str','dex','con','int','wis','cha']},
                **{f"{s}_prof": 0 for s in ['athletics','acrobatics','sleight_of_hand','stealth','arcana',
                                            'history','investigation','nature','religion','animal_handling',
                                            'insight','medicine','perception','survival','deception',
                                            'intimidation','performance','persuasion']}
            )
            db.add(char)
            await db.commit()

            equipment_json = self.data.get('equipment', '[]')
            if equipment_json != '[]':
                equip_list = json.loads(equipment_json)
                async for db in get_async_db():  # Neuer DB-Kontext für Inventory
                    equip_indices = []
                    for equip in equip_list:
                        eq_data = equip.get('equipment', {})
                        if isinstance(eq_data, dict) and 'index' in eq_data:
                            equip_indices.append(eq_data['index'])

                    weights = {}
                    if equip_indices:
                        async with self.cog.session as session:
                            tasks = [session.get(f"{DND_API}/equipment/{idx}") for idx in equip_indices]
                            responses = await asyncio.gather(*tasks, return_exceptions=True)
                            
                            for i, resp in enumerate(responses):
                                if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                                    try:
                                        data = await resp.json()
                                        weights[equip_indices[i]] = float(data.get('weight') or 0.0)
                                    except (ValueError, KeyError, json.JSONDecodeError):
                                        weights[equip_indices[i]] = 0.0

                    # Inventory erstellen (mit Weights)
                    total_weight = 0.0
                    for equip in equip_list:
                        item_name = equip.get('name') or equip.get('equipment', {}).get('name', 'Unknown')
                        qty = equip.get('quantity', 1)
                        
                        # Weight aus Cache oder Fallback
                        eq_index = equip.get('equipment', {}).get('index')
                        weight = weights.get(eq_index, 0.0)
                        total_weight += weight * qty
                        
                        inv_item = InventoryItem(
                            characterId=char.id,
                            name=item_name,
                            quantity=qty,
                            weight=weight,  # Einzel-Item-Gewicht
                            equipped=False,
                            properties=json.dumps({
                                **equip, 
                                'total_weight': weight * qty,  # Für Queries
                                'api_index': eq_index
                            })
                        )
                        db.add(inv_item)

                    
                    await db.commit()  # Inventory commit

        # Success Embed
        embed = discord.Embed(title=f"🎉 {self.data['name']} erstellt!", color=0x00ff00)
        embed.add_field(name="📈 Stats", value=" ".join([f"{k[:-6].upper()}{v}" for k,v in final_stats.items()]), inline=False)
        embed.add_field(name="❤️ Vitals", value=f"HP {hp_max} | AC {ac_base} | Speed {speed}", inline=True)
        embed.add_field(name="🛡️ Profs", value=f"Armor: {', '.join(armor_profs[:3]) or 'Keine'}\\nWeapons: {', '.join(weapon_profs[:3]) or 'Keine'}", inline=True)
        if prepared_spells:
            embed.add_field(name="✨ Spells", value=f"{spells_known} known: {prepared_spells}", inline=True)
        if equipment_json != '[]':
            equip_preview = json.loads(equipment_json)[:5]
            embed.add_field(name="🛡️ Equipment", value=', '.join([e.get('name','?') for e in equip_preview]), inline=True)
        if 'alignment' in self.data:
            align = self.data['alignment']
            embed.add_field(
                name="🧭 Alignment", 
                value=f"{align['name']} [{align['index'].replace('-',' ').title()}]", 
                inline=True
            )
        embed.add_field(name="⚔️ Commands", value="`/charinfo` `/charswitch` `/tutorial start`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()

class EquipmentSelectView(discord.ui.View):
    def __init__(self, equip_data, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.equip_data = equip_data
        self.selected_equip = []
        for f in equip_data['fixed']:
            self.selected_equip.append(f)
        self.completed = set()
        
        for i, opt in enumerate(equip_data['options']):
            desc = opt.get('desc', f'Option {i+1}')
            choices = opt.get('choices', [])
            
            if choices:
                # ✅ KEIN functools - einfache Lambda mit default arg
                select = discord.ui.Select(
                    placeholder=f"{desc[:40]} ({opt.get('choose',1)}x)",
                    options=[discord.SelectOption(label=c, value=c) for c in choices[:25]],
                    min_values=1,
                    max_values=min(opt.get('choose',1), len(choices))
                )
                select.callback = lambda interaction, opt_idx=i: self.select_callback(interaction, opt_idx)
                self.add_item(select)
            else:
                button = discord.ui.Button(label=desc[:25], style=discord.ButtonStyle.secondary)
                button.callback = lambda interaction, opt_idx=i: self.button_callback(interaction, opt_idx)
                self.add_item(button)
        
        self.add_item(SaveButton(self.parent_view, self.selected_equip))
    
    async def select_callback(self, interaction: discord.Interaction, opt_idx: int):
        print(f"Select callback: opt {opt_idx}")  # Debug
        if opt_idx in self.completed:
            await interaction.response.send_message("✅ Bereits gewählt!", ephemeral=True)
            return
        
        opt = self.equip_data['options'][opt_idx]
        selected = interaction.data['values']
        
        for name in selected:
            self.selected_equip.append({'name': name, 'quantity': 1})
        
        self.completed.add(opt_idx)
        await self.update_embed(interaction)
    
    async def button_callback(self, interaction: discord.Interaction, opt_idx: int):
        print(f"Button callback: opt {opt_idx}")
        opt = self.equip_data['options'][opt_idx]
        # Pack-Option A als Default
        self.selected_equip.append({'name': "Scholar's Pack", 'quantity': 1})  # OPT 2 Default
        self.completed.add(opt_idx)
        await self.update_embed(interaction)
    
    async def update_embed(self, interaction):
        status = []
        total_opts = len(self.equip_data['options'])
        for i in range(total_opts):
            mark = "✅" if i in self.completed else "⏳"
            opt_desc = self.equip_data['options'][i].get('desc', '?')[:20]
            status.append(f"{mark} {opt_desc}")
        
        recent_items = ', '.join([e['name'] for e in self.selected_equip[-4:]])
        embed = discord.Embed(
            title=f"🛡️ Equipment ({len(self.completed)}/{total_opts})",
            description=f"**Status:**\n" + "\n".join(status) + 
            f"\n\n**{len(self.selected_equip)} Items:**\n{recent_items}"
        )
        await interaction.response.edit_message(embed=embed, view=self)

class SaveButton(discord.ui.Button):
    def __init__(self, parent_view, selected_equip):
        super().__init__(label="💾 Speichern", style=discord.ButtonStyle.green)
        self.parent_view = parent_view
        self.selected_equip = selected_equip  # Reference
    
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.data['equipment'] = json.dumps(self.selected_equip)
        embed = discord.Embed(title="✅ Equipment gespeichert!", color=0x00ff00)
        embed.add_field(name="Items", value=f"{len(self.selected_equip)} total", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)





# Separater Save-Button (damit er korrekt funktioniert)
class SaveButton(discord.ui.Button):
    def __init__(self, parent_view, selected_equip):
        super().__init__(label="✅ Speichern", style=discord.ButtonStyle.green)
        self.parent_view = parent_view
        self.selected_equip = selected_equip
    
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.data['equipment'] = json.dumps(self.selected_equip)
        embed = discord.Embed(title="✅ Equipment gespeichert!", description=f"{len(self.selected_equip)} Items")
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()



# SpellSelectView (NEU!)
class SpellSelectView(discord.ui.View):
    def __init__(self, spells_data, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.spells_data = spells_data
        self.selected_spells = set()
        
        # Cantrips
        cantrip_opts = [discord.SelectOption(label=s.replace('-',' ').title(), value=s) 
                       for s in spells_data['cantrips'][:25]]
        self.cantrip_select = discord.ui.Select(
            placeholder="🎭 Cantrips wählen (0)", 
            options=cantrip_opts, min_values=0, max_values=4
        )
        self.cantrip_select.callback = self._spell_callback
        
        # Level 1 Spells
        l1_opts = [discord.SelectOption(label=s.replace('-',' ').title(), value=s) 
                  for s in spells_data['l1_spells'][:25]]
        self.l1_select = discord.ui.Select(
            placeholder="🔮 Level 1 Spells", 
            options=l1_opts, min_values=0, max_values=6
        )
        self.l1_select.callback = self._spell_callback
        
        self.add_item(self.cantrip_select)
        self.add_item(self.l1_select)

    async def _spell_callback(self, interaction: discord.Interaction):
        # Alle selections sammeln
        self.selected_spells.update(self.cantrip_select.values)
        self.selected_spells.update(self.l1_select.values)
        
        needed = self.spells_data['known_count']
        if len(self.selected_spells) >= needed:
            # Genug! Speichern
            spell_list = list(self.selected_spells)[:needed]  # Max needed
            self.parent_view.data['prepared_spells'] = ','.join(spell_list)
            
            embed = discord.Embed(
                title="✅ Spells gespeichert!", 
                description=f"**{len(spell_list)}/{needed}:** {', '.join([s.replace('-',' ').title() for s in spell_list])}",
                color=0x00ff00
            )
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
        else:
            embed = discord.Embed(
                title="🔮 Auswahl...",
                description=f"Wähle noch **{needed - len(self.selected_spells)}** mehr ({len(self.selected_spells)}/{needed})"
            )
            await interaction.response.edit_message(embed=embed, view=self)

# Modals (unverändert + angepasst)
class NameDetailsModal(discord.ui.Modal, title="1️⃣ Name"):
    name = discord.ui.TextInput(label="Name *", placeholder="Aragorn", max_length=30)
    gender = discord.ui.TextInput(label="Gender", placeholder="männlich", max_length=20)
    age = discord.ui.TextInput(label="Alter", placeholder="25", max_length=3)

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction):
        if not self.age.value.isdigit() or int(self.age.value) <= 0:
            await interaction.response.send_message("❌ Ungültiges Alter!", ephemeral=True)
            return
        self.view.data['name'] = self.name.value
        self.view.data['gender'] = self.gender.value or None
        self.view.data['age'] = int(self.age.value) if self.age.value.isdigit() else 0
        
        embed = discord.Embed(title="✅ Name gesetzt", description=self.view.data['name'])
        await interaction.response.send_message(embed=embed, ephemeral=True)

class StatsModal1(discord.ui.Modal, title="📊 Stats 1/2"):
    def __init__(self, view):
        super().__init__()
        self.view = view

    str_score = discord.ui.TextInput(label="STR", placeholder="Unique aus 15,14,13,12,10,8", max_length=2)
    dex_score = discord.ui.TextInput(label="DEX", placeholder="Unique aus 15,14,13,12,10,8", max_length=2)
    con_score = discord.ui.TextInput(label="CON", placeholder="Unique aus 15,14,13,12,10,8", max_length=2)

    async def on_submit(self, interaction):
        try:
            stats = [int(self.str_score.value), int(self.dex_score.value), int(self.con_score.value)]
            used = set(stats)
            if len(used) != 3 or any(s not in [8,10,12,13,14,15] for s in stats):
                await interaction.response.send_message(
                    "❌ **Ungültig!** 3 **UNIQUE** aus [15,14,13,12,10,8]", ephemeral=True
                )
                return
            
            self.view.data['stats'].update({
                'str': stats[0], 'dex': stats[1], 'con': stats[2]
            })
            embed = discord.Embed(title="✅ STR/DEX/CON", description=f"STR{stats[0]} DEX{stats[1]} CON{stats[2]}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            await interaction.response.send_message("❌ Nur Zahlen 8-15!", ephemeral=True)

class StatsModal2(discord.ui.Modal, title="📊 Stats 2/3"):
    def __init__(self, view):
        super().__init__()
        self.view = view

    int_score = discord.ui.TextInput(label="INT", placeholder="Unique aus 15,14,13,12,10,8", max_length=2)
    wis_score = discord.ui.TextInput(label="WIS", placeholder="Unique aus 15,14,13,12,10,8", max_length=2)
    cha_score = discord.ui.TextInput(label="CHA", placeholder="Unique aus 15,14,13,12,10,8", max_length=2)

    async def on_submit(self, interaction):
        try:
            stats = [int(self.int_score.value), int(self.wis_score.value), int(self.cha_score.value)]
            used = set(stats)
            all_used = list(used) + list(self.view.data['stats'].values())
            
            if (len(used) != 3 or any(s not in [8,10,12,13,14,15] for s in stats) or 
                len(set(all_used)) != 6 or sorted(all_used) != [8,10,12,13,14,15]):
                await interaction.response.send_message(
                    f"❌ Alle 6 UNIQUE [15,14,13,12,10,8]!\\nBereits: {sorted(self.view.data['stats'].values())}", 
                    ephemeral=True
                )
                return
            
            self.view.data['stats'].update({
                'int': stats[0], 'wis': stats[1], 'cha': stats[2]
            })
            stat_str = " ".join([f"{k.upper()}{v}" for k,v in self.view.data['stats'].items()])
            embed = discord.Embed(title="✅ Alle Stats!", description=stat_str)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            await interaction.response.send_message("❌ Nur Zahlen 8-15!", ephemeral=True)

class DetailsModal(discord.ui.Modal, title="4️⃣ Details"):
    def __init__(self, view):
        super().__init__()
        self.view = view

    background = discord.ui.TextInput(label="Background", placeholder="Noble", max_length=45)
    description = discord.ui.TextInput(label="Beschreibung", style=discord.TextStyle.paragraph, max_length=200)

    async def on_submit(self, interaction):
        self.view.data['background'] = self.background.value or "Adventurer"
        self.view.data['description'] = self.description.value or "Bereit für Abenteuer"
        embed = discord.Embed(title="✅ Details OK", description="Jetzt Spells oder Fertig!")
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(CharacterCog(bot))
    print("✅ CharacterCog COMPLETE mit API-Spells!")
