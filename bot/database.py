import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func  # Für timestamp
from dotenv import load_dotenv
import datetime  # Fallback


load_dotenv()


Base = declarative_base()


# DB Engine (MySQL Async)
DATABASE_URL = os.getenv("DATABASE_URL")
MYSQL_URL = DATABASE_URL.replace("mysql://", "mysql+aiomysql://") if DATABASE_URL else "mysql+aiomysql://root:password@localhost/dndbot"
engine = create_async_engine(MYSQL_URL, echo=True)


# Async Session
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session


# Sync Engine für create_all
from sqlalchemy import create_engine
sync_engine = create_engine(MYSQL_URL.replace("+aiomysql", ""), echo=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    discordId = Column(String(20), unique=True, index=True)
    username = Column(String(32))
    maxSlots = Column(Integer, default=1)
    activeCharId = Column(Integer, ForeignKey("characters.id", ondelete="SET NULL"))


class Character(Base):
    __tablename__ = "characters"
    id = Column(Integer, primary_key=True)
    user_Id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    name = Column(String(32))
    gender = Column(String(16), nullable=True)
    age = Column(Integer, default=25)
    background = Column(String(100), default="Adventurer")
    description = Column(Text, default="Neuer Held")
    apiRace = Column(String(32))
    apiClass = Column(String(32))
    level = Column(Integer, default=1)
    
    # Ability Scores
    str_score = Column(Integer, default=10)
    dex_score = Column(Integer, default=10)
    con_score = Column(Integer, default=10)
    int_score = Column(Integer, default=10)
    wis_score = Column(Integer, default=10)
    cha_score = Column(Integer, default=10)
    
    # Vitals
    hp_max = Column(Integer, default=10)
    hp_current = Column(Integer, default=10)
    ac_base = Column(Integer, default=10)
    hit_die = Column(String(8), default="d8")
    
    # Progression
    xp = Column(Integer, default=0)
    xp_next = Column(Integer, default=300)
    prof_bonus = Column(Integer, default=2)
    inspiration = Column(Integer, default=0)
    
    # Combat
    initiative = Column(Integer, default=0)
    passive_perc = Column(Integer, default=10)
    speed = Column(Integer, default=30)
    alignment = Column(String(16), default="neutral")
    
    # Magic
    spell_class = Column(String(32), nullable=True)
    spells_known = Column(Text, default="[]")  # JSON
    prepared_spells = Column(Text, default="[]")
    spell_slots = Column(Text, default="{}")   # JSON '{"1":2}'
    
    # Features/Equipment
    features = Column(Text, default="[]")
    armor_prof = Column(Text, default="[]")    # JSON Array
    weapon_prof = Column(Text, default="[]")
    image_path = Column(String(256), nullable=True)
    is_active = Column(Integer, default=0)
    
    # Save Proficiencies (Boolean 0/1 → Integer)
    str_save_prof = Column(Integer, default=0)
    dex_save_prof = Column(Integer, default=0)
    con_save_prof = Column(Integer, default=0)
    int_save_prof = Column(Integer, default=0)
    wis_save_prof = Column(Integer, default=0)
    cha_save_prof = Column(Integer, default=0)
    
    # Skill Proficiencies
    acrobatics_prof = Column(Integer, default=0)
    animal_handling_prof = Column(Integer, default=0)
    arcana_prof = Column(Integer, default=0)
    athletics_prof = Column(Integer, default=0)
    deception_prof = Column(Integer, default=0)
    history_prof = Column(Integer, default=0)
    insight_prof = Column(Integer, default=0)
    intimidation_prof = Column(Integer, default=0)
    investigation_prof = Column(Integer, default=0)
    medicine_prof = Column(Integer, default=0)
    nature_prof = Column(Integer, default=0)
    perception_prof = Column(Integer, default=0)
    performance_prof = Column(Integer, default=0)
    persuasion_prof = Column(Integer, default=0)
    religion_prof = Column(Integer, default=0)
    sleight_of_hand_prof = Column(Integer, default=0)
    stealth_prof = Column(Integer, default=0)
    survival_prof = Column(Integer, default=0)

    starting_equipment = Column(Text, default="[]")  # JSON


class InventoryItem(Base):
    __tablename__ = "inventory_items"  # Plural + _
    id = Column(Integer, primary_key=True)
    characterId = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"))
    name = Column(String(64))
    quantity = Column(Integer, default=1)
    weight = Column(Float, default=0.0)  # kg/lbs
    equipped = Column(Boolean, default=False)
    properties = Column(Text, default="{}")  # JSON


class CombatLog(Base):
    __tablename__ = "combat_logs"
    id = Column(Integer, primary_key=True)
    characterId = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"))
    type = Column(String(32))  # attack, spell, save
    ability = Column(String(32))  # str, dex, fire_bolt
    roll = Column(Integer)
    modifier = Column(Integer)
    total = Column(Integer)
    success = Column(Boolean)
    dc = Column(Integer, default=0)
    timestamp = Column(DateTime, server_default=func.now())
    notes = Column(String(128))


class Tutorial(Base):
    __tablename__ = "tutorials"
    id = Column(Integer, primary_key=True)
    characterId = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"))
    success = Column(Boolean, default=False)
    timestamp = Column(DateTime, server_default=func.now())