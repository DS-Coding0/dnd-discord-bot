import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import random
from database import User, Character, InventoryItem, get_async_db
from sqlalchemy import select, func
import asyncio
import json

DND_API = "https://www.dnd5eapi.co/api"

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
        if not self.data['name']:
            await interaction.response.send_message("❌ Schritt 1 zuerst!", ephemeral=True)
            return
        modal = StatsModal1(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="3️⃣ Stats 2/2", style=discord.ButtonStyle.secondary, row=0)
    async def stats2_step(self, interaction: discord.Interaction, button):
        if len(self.data['stats']) < 3:
            await interaction.response.send_message("❌ Stats 1/3 zuerst!", ephemeral=True)
            return
        modal = StatsModal2(self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="4️⃣ Details", style=discord.ButtonStyle.secondary, row=1)
    async def details_step(self, interaction: discord.Interaction, button):
        if not self.data['stats']:
            await interaction.response.send_message("❌ Zuerst Stats!", ephemeral=True)
            return
        modal = DetailsModal(self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="5️⃣ API-Spells", style=discord.ButtonStyle.blurple, row=1)
    async def spells_step(self, interaction: discord.Interaction, button):
        if len(self.data['stats']) != 6:
            await interaction.response.send_message("❌ Zuerst alle 6 Stats!", ephemeral=True)
            return
        
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
    
    @discord.ui.button(label="6️⃣ Equipment", style=discord.ButtonStyle.blurple, row=1)
    async def equipment_step(self, interaction: discord.Interaction, button):
        if len(self.data['stats']) != 6:
            await interaction.response.send_message("❌ Zuerst alle Stats!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        try:
            equip_data = await self.load_api_equipment()
            view = EquipmentSelectView(equip_data, self)
            embed = discord.Embed(
                title="🛡️ Starting Equipment",
                description=f"**Fix: {len(equip_data['fixed'])} Items**\n**Optionen: {sum(o.get('choose',0) for o in equip_data['options'])} Wahlen**"
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Equipment API: {e}\nSkip (leeres Array)", ephemeral=True)

    

    # Neue load_api_equipment Methode (ähnlich load_api_spells):
    async def load_api_equipment(self):
        async with self.cog.session as session:
            class_url = f"{DND_API}/classes/{self.class_}"
            async with session.get(class_url) as resp:
                if resp.status != 200:
                    return {'fixed': [], 'options': []}
                class_data = await resp.json()
            
            fixed = class_data.get('starting_equipment', [])
            options = class_data.get('starting_equipment_options', [])
            
            # Für jede Option: Lade Category-Items falls nötig (vereinfacht, top 10)
            for opt in options:
                if opt.get('from', {}).get('equipment_category'):
                    cat_index = opt['from']['equipment_category']['index']
                    cat_url = f"{DND_API}/equipment-categories/{cat_index}"
                    try:
                        async with session.get(cat_url) as cat_resp:
                            if cat_resp.status == 200:
                                cat_data = await cat_resp.json()
                                opt['choices'] = [eq['name'] for eq in cat_data.get('equipment', [])[:10]]
                    except:
                        opt['choices'] = ['Fallback Item']
            
            return {'fixed': fixed, 'options': options}

    @discord.ui.button(label="7️⃣ Background", style=discord.ButtonStyle.secondary, row=2)
    async def background_step(self, interaction: discord.Interaction, button):
        # API: https://DND_API/backgrounds
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{DND_API}/backgrounds") as resp:
                data = await resp.json()
                bg_options = [discord.SelectOption(label=b['name'], value=b['index']) for b in data['results']]
        
        view = BackgroundSelectView(bg_options, self)
        embed = discord.Embed(title="📜 Background (Skills/Tools)", description="Gibt Profs!")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)




    @discord.ui.button(label="✅ Fertig", style=discord.ButtonStyle.green, row=2)
    async def finish(self, interaction: discord.Interaction, button):
        if not all([self.data['name'], len(self.data['stats']) == 6, 'equipment' in self.data]):
            await interaction.response.send_message("❌ Name + Stats + Equipment!", ephemeral=True)
            return
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await self.create_character(interaction)

    @discord.ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.danger, row=2)
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
                alignment="neutral",
                speed=speed,
                initiative=(final_stats['dex_score']-10)//2,
                passive_perc=10+(final_stats['wis_score']-10)//2,
                spell_class=spell_class,
                spells_known=spells_known,
                prepared_spells=prepared_spells,
                starting_equipment=equipment_json,
                spell_slots=None,
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
        embed.add_field(name="⚔️ Commands", value="`/charinfo` `/charswitch` `/tutorial start`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()

class EquipmentSelectView(discord.ui.View):
    def __init__(self, equip_data, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        self.equip_data = equip_data
        self.selected_equip = self.equip_data['fixed'].copy()  # Fixe immer mitnehmen
        
        # Dynamische Selects für jede Option (max 3-4 pro View)
        for i, opt in enumerate(equip_data['options'][:3]):  # Limit für UX
            choices = opt.get('choices', ['Keine Optionen'])
            select_opts = [discord.SelectOption(label=c, value=c) for c in choices[:25]]
            select = discord.ui.Select(
                placeholder=f"Wähle {opt.get('choose',1)} aus {opt.get('type','?')}",
                options=select_opts, min_values=1, max_values=min(opt.get('choose',1), len(choices))
            )
            select.callback = lambda inter, idx=i: self.equip_callback(inter, idx)
            self.add_item(select)
        
        if not equip_data['options']:
            embed = discord.Embed(title="✅ Keine Wahlen nötig", description="Nur fixe Items!")
            # Auto-speichern

    async def equip_callback(self, interaction: discord.Interaction, opt_idx):
        opt = self.equip_data['options'][opt_idx]
        choose_count = opt.get('choose', 1)
        selected = interaction.data['values'][:choose_count]
        
        for _ in range(choose_count):
            if selected:
                self.selected_equip.append({'name': selected.pop(0), 'quantity': 1})
        
        # Update Embed mit aktueller Liste
        equip_list = ', '.join([f"{e.get('equipment','').get('name','?') or e.get('name','?')} (x{e.get('quantity',1)})" 
                               for e in self.selected_equip[:10]])
        embed = discord.Embed(title="🛡️ Equipment", description=equip_list)
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Check if all options done
        if len(self.selected_equip) >= len(self.equip_data['fixed']) + sum(o.get('choose',0) for o in self.equip_data['options']):
            self.parent_view.data['equipment'] = json.dumps(self.selected_equip)
            embed.title = "✅ Equipment gespeichert!"
            embed.color = 0x00ff00
            await interaction.edit_original_response(embed=embed, view=None)
            self.stop()

    @discord.ui.button(label="✅ Speichern", style=discord.ButtonStyle.green)
    async def save_equip(self, interaction: discord.Interaction, button):
        self.parent_view.data['equipment'] = json.dumps(self.selected_equip)
        embed = discord.Embed(title="✅ Equipment OK!", description=f"{len(self.selected_equip)} Items")
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


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

class StatsModal1(discord.ui.Modal, title="📊 Stats 1/3"):
    def __init__(self, view):
        super().__init__()
        self.view = view

    str_score = discord.ui.TextInput(label="STR", placeholder="15", max_length=2)
    dex_score = discord.ui.TextInput(label="DEX", placeholder="14", max_length=2)
    con_score = discord.ui.TextInput(label="CON", placeholder="13", max_length=2)

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

    int_score = discord.ui.TextInput(label="INT", placeholder="12", max_length=2)
    wis_score = discord.ui.TextInput(label="WIS", placeholder="10", max_length=2)
    cha_score = discord.ui.TextInput(label="CHA", placeholder="8", max_length=2)

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
