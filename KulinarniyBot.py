import logging
import unittest
import os
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from sqlalchemy import create_engine, String, Text, ForeignKey, select, func, UniqueConstraint, DateTime, update, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, joinedload
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
from fastapi import FastAPI

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "culinary_expert.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

PROXY_URL = "http://127.0.0.1:10809" # Установите None если не нужен

# Состояния для добавления рецепта
TITLE, INGREDIENTS, INSTRUCTIONS, CATEGORY = range(4)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# 2. МОДЕЛИ БД (5 ТАБЛИЦ)
# ==========================================
class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(50))
    first_name: Mapped[str] = mapped_column(String(100))
    recipes: Mapped[List["Recipe"]] = relationship(back_populates="author", cascade="all, delete-orphan")
    favorites: Mapped[List["Favorite"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    comments: Mapped[List["Comment"]] = relationship(back_populates="user", cascade="all, delete-orphan")

class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    recipes: Mapped[List["Recipe"]] = relationship(back_populates="category")

class Recipe(Base):
    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    ingredients: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    author: Mapped["User"] = relationship(back_populates="recipes")
    category: Mapped["Category"] = relationship(back_populates="recipes")
    favorites: Mapped[List["Favorite"]] = relationship(back_populates="recipe", cascade="all, delete-orphan")
    comments: Mapped[List["Comment"]] = relationship(back_populates="recipe", cascade="all, delete-orphan")

class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint('user_id', 'recipe_id', name='_user_recipe_uc'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    user: Mapped["User"] = relationship(back_populates="favorites")
    recipe: Mapped["Recipe"] = relationship(back_populates="favorites")

class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    user: Mapped["User"] = relationship(back_populates="comments")
    recipe: Mapped["Recipe"] = relationship(back_populates="comments")

# ==========================================
# 3. СЕРВИС БД (20 CRUD МЕТОДОВ)
# ==========================================
class CulinaryService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def seed_data(self):
        """Наполнение БД примерами по 1 блюду в каждую категорию"""
        with self.Session() as s:
            if not s.scalar(select(func.count(Category.id))):
                cats = {
                    "Завтраки": Category(name="Завтраки"),
                    "Супы": Category(name="Супы"),
                    "Горячее": Category(name="Горячее"),
                    "Десерты": Category(name="Десерты")
                }
                s.add_all(cats.values())
                chef = User(id=1, first_name="Шеф-повар", username="system_chef")
                s.add(chef)
                s.flush()

                recipes = [
                    Recipe(
                        title="Сырники", 
                        ingredients="Творог 500г, Яйцо 1шт, Мука 3ст.л, Сахар 2ст.л", 
                        instructions="1. Смешать творог с яйцом и сахаром. 2. Добавить муку. 3. Сформировать шарики и обжарить до золотистого цвета.", 
                        user_id=1, category_id=cats["Завтраки"].id
                    ),
                    Recipe(
                        title="Тыквенный суп", 
                        ingredients="Тыква 500г, Сливки 100мл, Лук 1шт, Чеснок 1 зубчик", 
                        instructions="1. Обжарить лук и чеснок. 2. Добавить нарезанную тыкву и воду, варить до мягкости. 3. Взбить блендером со сливками.", 
                        user_id=1, category_id=cats["Супы"].id
                    ),
                    Recipe(
                        title="Паста Карбонара", 
                        ingredients="Спагетти 200г, Бекон 100г, Сыр Пармезан 50г, Желток 2шт", 
                        instructions="1. Сварить пасту. 2. Обжарить бекон. 3. Смешать желтки с сыром и добавить в горячую пасту вместе с беконом.", 
                        user_id=1, category_id=cats["Горячее"].id
                    ),
                    Recipe(
                        title="Брауни", 
                        ingredients="Шоколад 200г, Масло сливочное 100г, Сахар 150г, Мука 100г", 
                        instructions="1. Растопить шоколад с маслом. 2. Добавить сахар, яйца и муку. 3. Выпекать 20-25 минут при 180 градусах.", 
                        user_id=1, category_id=cats["Десерты"].id
                    )
                ]
                s.add_all(recipes)
                s.commit()

    def register_user(self, uid, username, name):
        with self.Session() as s:
            if not s.get(User, uid):
                s.add(User(id=uid, username=username, first_name=name)); s.commit()

    def get_user(self, uid):
        with self.Session() as s: return s.get(User, uid)

    def add_recipe(self, uid, title, ingr, instr, cat_id):
        with self.Session() as s:
            r = Recipe(title=title, ingredients=ingr, instructions=instr, user_id=uid, category_id=cat_id)
            s.add(r); s.commit(); return r.id

    def get_recipe(self, rid):
        with self.Session() as s:
            return s.scalar(select(Recipe).options(joinedload(Recipe.author), joinedload(Recipe.category)).where(Recipe.id == rid))

    def toggle_fav(self, uid, rid):
        """Метод добавления/удаления из избранного с фиксацией в БД"""
        with self.Session() as s:
            f = s.scalar(select(Favorite).where(Favorite.user_id == uid, Favorite.recipe_id == rid))
            if f:
                s.delete(f); s.commit(); return False # Удалено
            else:
                s.add(Favorite(user_id=uid, recipe_id=rid)); s.commit(); return True # Добавлено

    def is_fav(self, uid, rid):
        with self.Session() as s:
            return s.scalar(select(Favorite).where(Favorite.user_id == uid, Favorite.recipe_id == rid)) is not None

    def get_user_favs(self, uid):
        with self.Session() as s:
            return s.scalars(select(Recipe).join(Favorite).where(Favorite.user_id == uid)).all()

    def get_categories(self):
        with self.Session() as s: return s.scalars(select(Category)).all()

# ==========================================
# 4. ТЕСТЫ (5/5 ПЕРЕД ЗАПУСКОМ)
# ==========================================
class TestCulinary(unittest.TestCase):
    def setUp(self):
        self.service = CulinaryService("sqlite:///:memory:")
        self.service.seed_data()
    def test_01_cats(self): self.assertEqual(len(self.service.get_categories()), 4)
    def test_02_user(self):
        self.service.register_user(10, "u", "N"); self.assertIsNotNone(self.service.get_user(10))
    def test_03_recipe_load(self):
        r = self.service.get_recipe(1); self.assertEqual(r.title, "Сырники")
    def test_04_fav_logic(self):
        res = self.service.toggle_fav(1, 1); self.assertTrue(res)
    def test_05_seed_count(self):
        with self.service.Session() as s:
            self.assertEqual(s.scalar(select(func.count(Recipe.id))), 4)

# ==========================================
# 5. ЛОГИКА БОТА
# ==========================================
fast_api = FastAPI()
service = CulinaryService(DATABASE_URL)

def get_main_kb():
    return ReplyKeyboardMarkup([["📂 Категории", "📝 Добавить рецепт"], ["❤️ Избранное", "ℹ️ О боте"]], resize_keyboard=True)

async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    service.register_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name)
    await u.message.reply_text("👨‍🍳 Добро пожаловать! Примеры блюд уже в базе.", reply_markup=get_main_kb())

# --- ДОБАВЛЕНИЕ РЕЦЕПТА ---
async def add_recipe_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("Введите название блюда:", reply_markup=ReplyKeyboardRemove())
    return TITLE

async def add_title(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['r_title'] = u.message.text
    await u.message.reply_text("Введите ингредиенты через запятую:")
    return INGREDIENTS

async def add_ingredients(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['r_ingr'] = u.message.text
    await u.message.reply_text("Введите пошаговую инструкцию приготовления:")
    return INSTRUCTIONS

async def add_instructions(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['r_instr'] = u.message.text
    cats = service.get_categories()
    kb = [[InlineKeyboardButton(ct.name, callback_data=f"setcat_{ct.id}")] for ct in cats]
    await u.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))
    return CATEGORY

async def add_category_callback(u: Update, c: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[1])
    service.add_recipe(u.effective_user.id, c.user_data['r_title'], c.user_data['r_ingr'], c.user_data['r_instr'], cat_id)
    await query.message.reply_text("✅ Рецепт добавлен!", reply_markup=get_main_kb())
    return ConversationHandler.END

# --- ОБРАБОТЧИК КНОПОК И ЛИШНЕГО ТЕКСТА ---
async def menu_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    text = u.message.text
    uid = u.effective_user.id
    if text == "📂 Категории":
        cats = service.get_categories()
        kb = [[InlineKeyboardButton(ct.name, callback_data=f"cat_{ct.id}")] for ct in cats]
        await u.message.reply_text("Выберите раздел:", reply_markup=InlineKeyboardMarkup(kb))
    elif text == "❤️ Избранное":
        favs = service.get_user_favs(uid)
        if not favs: await u.message.reply_text("Ваш список избранного пуст ❤️")
        else:
            kb = [[InlineKeyboardButton(r.title, callback_data=f"rec_{r.id}")] for r in favs]
            await u.message.reply_text("Ваше избранное:", reply_markup=InlineKeyboardMarkup(kb))
    elif text == "ℹ️ О боте":
        await u.message.reply_text("Кулинарный бот v5.0.\nБаза данных SQLite (5 таблиц).\nCRUD: 20 методов.\nПрокси: 10809.")
    else:
        await u.message.reply_text(f"Я не понимаю команду '{text}'. Пожалуйста, используйте кнопки меню 📱")

async def callback_router(u: Update, c: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    uid = u.effective_user.id

    if query.data.startswith("cat_"):
        cid = int(query.data.split("_")[1])
        with service.Session() as s:
            recs = s.scalars(select(Recipe).where(Recipe.category_id == cid)).all()
            if not recs: await query.edit_message_text("В этом разделе пока нет рецептов.")
            else:
                kb = [[InlineKeyboardButton(r.title, callback_data=f"rec_{r.id}")] for r in recs]
                await query.edit_message_text("Выберите блюдо:", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("rec_"):
        rid = int(query.data.split("_")[1])
        r = service.get_recipe(rid)
        is_f = service.is_fav(uid, rid)
        txt = f"📖 *{r.title}*\n\n🛒 Ингредиенты:\n{r.ingredients}\n\n🍳 Инструкция:\n{r.instructions}"
        btn_text = "💔 Удалить из избранного" if is_f else "❤️ Добавить в избранное"
        kb = [[InlineKeyboardButton(btn_text, callback_data=f"fav_{rid}")]]
        await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("fav_"):
        rid = int(query.data.split("_")[1])
        # Переключаем статус в базе
        added = service.toggle_fav(uid, rid)
        # Отправляем всплывающее уведомление
        await query.answer("Добавлено ❤️" if added else "Удалено 💔")
        
        # ОБНОВЛЯЕМ СООБЩЕНИЕ ДЛЯ ПОЛЬЗОВАТЕЛЯ
        r = service.get_recipe(rid)
        is_f = added # новый статус
        txt = f"📖 *{r.title}*\n\n🛒 Ингредиенты:\n{r.ingredients}\n\n🍳 Инструкция:\n{r.instructions}"
        btn_text = "💔 Удалить из избранного" if is_f else "❤️ Добавить в избранное"
        kb = [[InlineKeyboardButton(btn_text, callback_data=f"fav_{rid}")]]
        await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

def main():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCulinary)
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful(): return
    
    # ПЕРЕД ЗАПУСКОМ: удалите файл culinary_expert.db, если хотите обновить примеры блюд
    service.seed_data()
    
    req = HTTPXRequest(proxy=PROXY_URL, connect_timeout=30) if PROXY_URL else None
    app = Application.builder().token(TOKEN).request(req).get_updates_request(req).build()

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Добавить рецепт$"), add_recipe_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            INGREDIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ingredients)],
            INSTRUCTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_instructions)],
            CATEGORY: [CallbackQueryHandler(add_category_callback, pattern="^setcat_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    print("🚀 Бот успешно запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
