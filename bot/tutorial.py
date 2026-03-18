import discord
from discord import app_commands, ui
from discord.ext import commands
import aiohttp
import asyncio
import json
import random
from database import User, Character, get_async_db
from sqlalchemy import select

DND_API = "https://www.dnd5eapi.co/api"

async def getactivechar(db, discordId):
    user_result = await db.execute(select(User).where(User.discordId == discordId))
    user = user_result.scalar_one_or_none()
    if user and user.activeCharId:
        char_result = await db.execute(select(Character).where(Character.id == user.activeCharId))
        return char_result.scalar_one_or_none()
    return None

def get_mod(score):
    return (score - 10) // 2

async def get_spell_effect(spell_index: str, session):
    """100% API-driven Spell Effects"""
    try:
        url = f"{DND_API}/spells/{spell_index}"
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception("API Error")
            data = await resp.json()
        
        spell_name = data.get('name', spell_index.title())
        
        # 1. DAMAGE parsing (robust für acid-splash etc.)
        if data.get('damage'):
            damage_data = data['damage']
            if damage_data.get('damage_at_slot_level', {}).get('1'):
                dice = damage_data['damage_at_slot_level']['1']
            elif damage_data.get('damage_dice'):
                dice = damage_data['damage_dice']
            else:
                dice = '1d6'  # Cantrip fallback
            
            return {
                'type': 'damage',
                'name': spell_name,
                'dice': dice,
                'desc': data.get('desc', ['Damage!'])[0][:80] + '...',
                'mod': data.get('spellcasting_ability', {}).get('index', 'int')[:3]  # int/wis/cha
            }
        
        # 2. HEALING (parse desc oder damage_type=healing)
        desc_lower = ' '.join(d.lower() for d in data.get('desc', []))
        if any(word in desc_lower for word in ['hit point', 'heal', 'restore']):
            dice = data['damage']['damage_at_slot_level']['1'] if data.get('damage') else '1d8'
            return {
                'type': 'heal',
                'name': spell_name,
                'dice': dice,
                'desc': data.get('desc', ['Heals HP!'])[0][:80],
                'mod': 'wis'  # Divine fallback
            }
        
        # 3. UTILITY (restliche Spells)
        return {
            'type': 'utility',
            'name': spell_name,
            'dice': '1d4',  # Dummy
            'desc': data.get('desc', ['Effect!'])[0][:80] + '...',
            'mod': 'int'
        }
        
    except Exception as e:
        print(f"API Error {spell_index}: {e}")
        return {
            'type': 'damage',
            'name': spell_index.title(),
            'dice': '1d6',
            'desc': 'API Magic!',
            'mod': 'int'
        }


class SpellModal(ui.Modal):
    def __init__(self, view, spells_preview: str):
        super().__init__(title=f"🔮 {spells_preview} | Spell casten")
        self.view = view
        self.spell_input = ui.TextInput(
            label="Spell Index",
            placeholder=f"charm-person, acid-splash, fire-bolt... (bekannt: {spells_preview})",
            max_length=30
        )
        self.add_item(self.spell_input)

    async def on_submit(self, interaction: discord.Interaction):
        spell_index = self.spell_input.value.strip().lower().replace(' ', '-')
        
        async with aiohttp.ClientSession() as session:
            effect = await get_spell_effect(spell_index, session)
        
        embed = discord.Embed(title=f"🔮 **{effect['name']}** casted!", color=0x9b59b6)
        
        # API Damage/Heal/Utility (vollständig dynamisch)
        if effect['type'] == 'damage':
            dice_parts = effect['dice'].split('d')
            num_dice = int(dice_parts[0]) if len(dice_parts) > 1 and dice_parts[0] != '' else 1
            dice_sides = int(dice_parts[-1]) if len(dice_parts) > 1 else 10
            base_dmg = sum(random.randint(1, dice_sides) for _ in range(num_dice))
            
            # Dynamic Ability Mod
            scores = {'str': self.view.char.str_score, 'dex': self.view.char.dex_score, 
                     'con': self.view.char.con_score, 'int': self.view.char.int_score,
                     'wis': self.view.char.wis_score, 'cha': self.view.char.cha_score}
            mod_val = get_mod(scores.get(effect['mod'], 10))
            total_dmg = base_dmg + mod_val
            
            self.view.state['goblin_hp'] -= total_dmg
            embed.description = f"**{effect['dice']}+{mod_val}** → **{total_dmg}** DMG!\n👹 {max(0,self.view.state['goblin_hp'])}/7 HP"
            
        elif effect['type'] == 'heal':
            # Parse dice + wis mod
            num_dice, dice_sides = map(int, effect['dice'].split('d'))
            heal = sum(random.randint(1, dice_sides) for _ in range(num_dice)) + get_mod(self.view.char.wis_score)
            self.view.state['player_hp'] = min(self.view.char.hp_max, self.view.state['player_hp'] + heal)
            embed.description = f"**+{heal} HP**\n❤️ {self.view.state['player_hp']}/{self.view.char.hp_max}"
            
        else:  # utility
            self.view.state['goblin_penalty'] = 4
            embed.description = f"**{effect['type'].title()} Effect**\n🌀 Goblin next attack -4!"
        
        # Game Flow
        self.view.state['spell_slots_left'] -= 1
        self.view.state['step'] = 2  # <- GOBLIN TURN!
        
        goblin_dead = self.view.state['goblin_hp'] <= 0
        if goblin_dead:
            embed.color = 0x00ff00
            embed.add_field(name="🏆 Victory!", value="**+50 XP!**", inline=False)
            self.view.disable_all_except_end()
        else:
            self.view.update_buttons()
        
        #




class SpellSelectView(ui.View):
    def __init__(self, parent_view, spell_options):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        
        self.spell_menu = ui.Select(
            placeholder="🔮 Spell wählen...",
            options=spell_options,
            min_values=1, max_values=1
        )
        self.spell_menu.callback = self.cast_selected_spell
        self.add_item(self.spell_menu)

    async def cast_selected_spell(self, interaction: discord.Interaction):
        spell_index = self.spell_menu.values[0]
        
        async with aiohttp.ClientSession() as session:
            effect = await get_spell_effect(spell_index, session)
        
        embed = discord.Embed(title=f"🔮 **{effect['name']}**", color=0x9b59b6)
        
        # API Damage (wie vorher)
        if effect['type'] == 'damage':
            dice_parts = effect['dice'].split('d')
            num_dice = int(dice_parts[0]) if len(dice_parts) > 1 and dice_parts[0] else 1
            dice_sides = int(dice_parts[-1]) if len(dice_parts) > 1 else 10
            base_dmg = sum(random.randint(1, dice_sides) for _ in range(num_dice))
            
            scores = {'str': self.parent_view.char.str_score, 'dex': self.parent_view.char.dex_score,
                     'con': self.parent_view.char.con_score, 'int': self.parent_view.char.int_score,
                     'wis': self.parent_view.char.wis_score, 'cha': self.parent_view.char.cha_score}
            mod_val = get_mod(scores.get(effect['mod'], 10))
            total_dmg = base_dmg + mod_val
            
            self.parent_view.state['goblin_hp'] -= total_dmg
            embed.description = f"**{effect['dice']}+{mod_val}** = **{total_dmg}** DMG!\n👹 {max(0,self.parent_view.state['goblin_hp'])}/7"
            
        elif effect['type'] == 'heal':
            # Heal logic...
            pass
        else:
            self.parent_view.state['goblin_penalty'] = 4
            embed.description = "**Utility!** Goblin -4 attack 🌀"
        
        # GAME FLOW!
        self.parent_view.state['spell_slots_left'] -= 1
        self.parent_view.state['step'] = 2
        goblin_dead = self.parent_view.state['goblin_hp'] <= 0
        
        if goblin_dead:
            embed.color = 0x00ff00
            embed.add_field(name="🏆 Sieg!", value="+50 XP!", inline=False)
            self.parent_view.disable_all_except_end()
        else:
            self.parent_view.update_buttons()
        
        # Ephemeral Feedback + Haupt-Embed update
        await interaction.response.edit_message(embed=embed, view=None)
        
        # Update TUTORIAL Haupt-Message
        await self.parent_view.message.edit(embed=embed, view=self.parent_view)
        
        self.stop()




class TutorialView(ui.View):
    def __init__(self, cog, user_id, char):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = user_id
        self.char = char
        self.state = self.cog.active_tutorials.setdefault(user_id, {
            'goblin_hp': 7, 'player_hp': char.hp_current, 
            'player_init': 0, 'goblin_init': 0, 
            'step': 0, 'round': 1, 'last_action': None,
            'spell_slots_left': 2,
            'goblin_penalty': 0
        })
        self.spell_info = {'known': 0, 'slots': 2, 'examples': [], 'raw_indices': []}
        asyncio.create_task(self.load_spell_info())  # Async load

    async def load_spell_info(self):
        """Lade ALLE Spells dynamisch von API"""
        self.spell_info = {'known': self.char.spells_known or 0, 'slots': 2, 'examples': [], 'raw_indices': []}
        
        # DB → API Names + Effects PRELOAD
        prepared_raw = self.char.prepared_spells
        if prepared_raw:
            try:
                raw_indices = json.loads(prepared_raw)
            except:
                raw_indices = [s.strip() for s in prepared_raw.split(',') if s.strip()]
        else:
            # API: Lade Level 1 Spells für diesen Char-Class
            raw_indices = await self.fetch_class_spells(self.char.apiClass.lower())
        
        self.spell_info['raw_indices'] = raw_indices[:6]
        
        # PARALLEL alle API-Effekte laden
        async with aiohttp.ClientSession() as session:
            tasks = [get_spell_effect(idx, session) for idx in raw_indices]
            effects = await asyncio.gather(*tasks, return_exceptions=True)
            
            for effect in effects:
                if isinstance(effect, dict):
                    self.spell_info['examples'].append(effect['name'])
        
        print(f"✅ API-LOADED: {self.spell_info['examples']}")

    async def fetch_class_spells(self, class_name: str):
        """Dynamisch Level1 Spells für Class laden"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{DND_API}/classes/{class_name}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Parse spelllist → level1
                        spellbook = data.get('spellcasting', {}).get('spell_list', {}).get('level_1', [])
                        return [s.split('/')[-1] for s in spellbook[:6]]  # Extract indices
        except:
            pass
        return ['fire-bolt', 'magic-missile']  # Nur wenn API total failt


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Nur der Spieler!", ephemeral=True)
            return False
        return True

    @ui.button(label="🎲 Initiative", style=discord.ButtonStyle.primary, emoji="⚔️", row=0)
    async def roll_init(self, interaction: discord.Interaction, button: ui.Button):
        if self.state['step'] != 0:
            return await interaction.response.send_message("❌ Initiative schon!", ephemeral=True)
        
        dex_mod = get_mod(self.char.dex_score)
        player_init = random.randint(1, 20) + dex_mod
        goblin_init = random.randint(1, 20) + 2
        self.state.update({'player_init': player_init, 'goblin_init': goblin_init})
        
        who_first = "🗡️ **Du zuerst**!" if player_init >= goblin_init else "👹 **Goblin zuerst**!"
        self.state['step'] = 2 if player_init < goblin_init else 1
        
        embed = discord.Embed(
            title="🎲 Initiative!", 
            description=f"**{self.char.name}:** 1d20+{dex_mod} = **{player_init}**\n"
                       f"**Goblin:** 1d20+2 = **{goblin_init}**\n\n{who_first}\n**Runde {self.state['round']}**",
            color=0xe74c3c
        )
        button.disabled = True
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger, emoji="🗡️", row=1, disabled=True)
    async def attack(self, interaction: discord.Interaction, button: ui.Button):
        if self.state['step'] != 1:
            return await interaction.response.send_message("❌ Nicht dein Zug!", ephemeral=True)
        
        str_mod = get_mod(self.char.str_score)
        attack_roll = random.randint(1, 20) + str_mod
        hit = attack_roll >= 15
        dmg = random.randint(1, 8) + str_mod if hit else 0
        self.state['goblin_hp'] -= dmg
        self.state['last_action'] = 'attack'
        
        goblin_dead = self.state['goblin_hp'] <= 0
        embed = discord.Embed(
            title="⚔️ Attack!", 
            description=f"**1d20+{str_mod}:** {attack_roll} ({'✅' if hit else '❌'} AC15)\n"
                       f"**1d8+{str_mod}:** {dmg} DMG\n**Goblin:** {max(0,self.state['goblin_hp'])}/7",
            color=0xf39c12
        )
        
        if goblin_dead:
            embed.add_field(name="🏆 Sieg!", value="**+50 XP!** `/levelup`", inline=False)
            self.disable_all_except_end()
        else:
            self.state['step'] = 2
        
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    
    @ui.button(label="🔮 Spell", style=discord.ButtonStyle.blurple, emoji="✨", row=1, disabled=True)
    async def cast_spell(self, interaction: discord.Interaction, button: ui.Button):
        if self.state['step'] != 1 or self.state['spell_slots_left'] <= 0:
            await interaction.response.send_message(f"❌ Slots: {self.state['spell_slots_left']}", ephemeral=True)
            return
        
        # API Spells laden für Dropdown
        spell_options = []
        async with aiohttp.ClientSession() as session:
            tasks = [get_spell_effect(idx, session) for idx in self.spell_info['raw_indices']]
            effects = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, effect in enumerate(effects):
                idx = self.spell_info['raw_indices'][i]
                if isinstance(effect, dict):
                    spell_options.append(discord.SelectOption(
                        label=effect['name'][:100],
                        value=idx,  # API Index!
                        description=f"{effect['type']} | {effect['dice']}"
                    ))
        
        embed = discord.Embed(
            title="🔮 Prepared Spells",
            description=f"**Wähle deinen Spell**\nSlots: {self.state['spell_slots_left']}/2",
            color=0x9b59b6
        )
        
        # DROPDOWN VIEW (ephemeral)
        view = SpellSelectView(self, spell_options)
        self.message = interaction.message  # Für später
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)



                    



    @ui.button(label="🛡️ Dodge", style=discord.ButtonStyle.grey, emoji="🛡️", row=1, disabled=True)
    async def dodge(self, interaction: discord.Interaction, button: ui.Button):
        if self.state['step'] != 1:
            return await interaction.response.send_message("❌ Nicht dein Zug!", ephemeral=True)
        
        self.state['last_action'] = 'dodge'
        self.state['player_ac_bonus'] = 5
        embed = discord.Embed(title="🛡️ Dodge!", description="**+5 AC diese Runde!**\n**Goblin greift...**", color=0x3498db)
        self.state['step'] = 2
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="👹 Goblin Turn", style=discord.ButtonStyle.secondary, emoji="🩸", row=2, disabled=True)
    async def goblin_turn(self, interaction: discord.Interaction, button: ui.Button):
        if self.state['step'] != 2:
            return await interaction.response.send_message("❌ Nicht Goblin!", ephemeral=True)
        
        ac = self.char.ac_base + self.state.get('player_ac_bonus', 0)
        goblin_penalty = self.state.get('goblin_penalty', 0)
        goblin_roll = random.randint(1, 20) + 4 - goblin_penalty
        hit = goblin_roll >= ac
        goblin_dmg = random.randint(1, 6) + 2 if hit else 0
        self.state['player_hp'] -= goblin_dmg
        
        self.state['player_ac_bonus'] = 0  # Reset Dodge
        self.state['goblin_penalty'] = 0   # Reset Utility
        
        player_dead = self.state['player_hp'] <= 0
        embed = discord.Embed(
            title="👹 Goblin Attack!", 
            description=f"**vs AC{ac}:** 1d20+4-{goblin_penalty}={goblin_roll} ({'✅' if hit else '❌'})\n"
                       f"**DMG:** {goblin_dmg}\n**Dein HP:** {max(0,self.state['player_hp'])}/{self.char.hp_max}",
            color=0xe74c3c
        )
        
        if not player_dead:
            self.state['round'] += 1
            self.state['step'] = 1
        else:
            embed.add_field(name="💀 Game Over!", value="`/tutorial start` neu!", inline=False)
            self.disable_all_except_end()
        
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @ui.button(label="🏁 Ende", style=discord.ButtonStyle.success, emoji="✨", row=3)
    async def end_tutorial(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id in self.cog.active_tutorials:
            del self.cog.active_tutorials[interaction.user.id]
        embed = discord.Embed(title="✨ Tutorial Ende!", description="API-Spells rocken! 🎭", color=0x27ae60)
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)

    def update_buttons(self):
        self.children[1].disabled = self.state['step'] != 1  # Attack
        self.children[2].disabled = self.state['step'] != 1 or self.state['spell_slots_left'] <= 0  # Spell
        self.children[3].disabled = self.state['step'] != 1  # Dodge
        self.children[4].disabled = self.state['step'] != 2  # Goblin

    def disable_all_except_end(self):
        for i in range(5):
            self.children[i].disabled = True

class TutorialCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_tutorials = {}

    tutorial = app_commands.Group(name="tutorial", description="Interaktives DnD-Tutorial!")

    @tutorial.command(name="start", description="Starte Goblin-Kampf!")
    async def tutorial_start(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        discordId = str(interaction.user.id)
        
        async for db in get_async_db():
            char = await getactivechar(db, discordId)
            if not char:
                await interaction.followup.send("❌ Kein aktiver Char! `/charswitch`", ephemeral=True)
                return
        
        # Spell-Debug im Embed
        spells_text = f"{char.spells_known or 0} bekannt"
        if char.prepared_spells:
            try:
                spells = json.loads(char.prepared_spells)
                spells_text += f": {', '.join(spells[:3])}..."
            except:
                spells_text += ": geladen"
        else:
            spells_text += ": keine 😢"
        
        str_mod = get_mod(char.str_score)
        dex_mod = get_mod(char.dex_score)
        
        embed = discord.Embed(
            title=f"⚔️ {char.name} vs Goblin 👹",
            description=f"**{char.apiClass.title()} Lvl1**\n"
                       f"STR+{str_mod} DEX+{dex_mod} HP:{char.hp_current}/{char.hp_max} AC:{char.ac_base}\n"
                       f"**Spells:** {spells_text}\n\n"
                       f"👹 **Goblin:** 7HP AC15 (+Penalty möglich)\n🎲 **Klicke Initiative**!",
            color=0x9b59b6
        )
        
        self.active_tutorials[interaction.user.id] = {
            'goblin_hp': 7, 'player_hp': char.hp_current, 
            'step': 0, 'round': 1, 'spell_slots_left': 2
        }
        
        view = TutorialView(self, interaction.user.id, char)
        await interaction.followup.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(TutorialCog(bot))
    print("✅ API-SPELL TUTORIAL COMPLETE! 🪄")
