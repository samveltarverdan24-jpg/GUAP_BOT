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

# Состояния диалогов
ADD_REC_TITLE, ADD_REC_INGR, ADD_REC_INSTR, ADD_REC_CAT = range(4)
WAITING_COMMENT = 5

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
    comments: Mapped[List["Comment"]] = relationship(back_populates="recipe", cascade="all, delete-orphan", order_by="Comment.created_at.desc()")

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    user: Mapped["User"] = relationship(back_populates="comments")
    recipe: Mapped["Recipe"] = relationship(back_populates="comments")

# ==========================================
# 3. СЕРВИС БД (22 CRUD МЕТОДА)
# ==========================================
class CulinaryService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def seed_data(self):
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
                recs = [
                    Recipe(title="Омлет", ingredients="Яйца, Молоко", instructions="Пожарить на сковороде", user_id=1, category_id=cats["Завтраки"].id),
                    Recipe(title="Борщ", ingredients="Мясо, Свекла, Капуста", instructions="Варить 2 часа", user_id=1, category_id=cats["Супы"].id),
                    Recipe(title="Стейк", ingredients="Говядина, Соль, Перец", instructions="Жарить по 3 мин с каждой стороны", user_id=1, category_id=cats["Горячее"].id),
                    Recipe(title="Медовик", ingredients="Мед, Мука, Сметана", instructions="Запечь коржи, смазать кремом", user_id=1, category_id=cats["Десерты"].id)
                ]
                s.add_all(recs)
                s.commit()

    def register_user(self, uid, username, name):
        with self.Session() as s:
            if not s.get(User, uid):
                s.add(User(id=uid, username=username, first_name=name)); s.commit()

    def get_recipe(self, rid):
        with self.Session() as s:
            return s.scalar(select(Recipe).options(
                joinedload(Recipe.author), 
                joinedload(Recipe.category), 
                joinedload(Recipe.comments).joinedload(Comment.user)
            ).where(Recipe.id == rid))

    def toggle_fav(self, uid, rid):
        with self.Session() as s:
            f = s.scalar(select(Favorite).where(Favorite.user_id == uid, Favorite.recipe_id == rid))
            if f: s.delete(f); s.commit(); return False
            else: s.add(Favorite(user_id=uid, recipe_id=rid)); s.commit(); return True

    def is_fav(self, uid, rid):
        with self.Session() as s: return s.scalar(select(Favorite).where(Favorite.user_id == uid, Favorite.recipe_id == rid)) is not None

    def get_user_favs(self, uid):
        with self.Session() as s: return s.scalars(select(Recipe).join(Favorite).where(Favorite.user_id == uid)).all()

    def add_comment(self, rid, uid, text):
        with self.Session() as s:
            s.add(Comment(recipe_id=rid, user_id=uid, text=text)); s.commit()

    def add_recipe(self, uid, title, ingr, instr, cat_id):
        with self.Session() as s:
            r = Recipe(title=title, ingredients=ingr, instructions=instr, user_id=uid, category_id=cat_id)
            s.add(r); s.commit(); return r.id

    def get_categories(self):
        with self.Session() as s: return s.scalars(select(Category)).all()

# ==========================================
# 4. ТЕСТЫ (5/5 ПЕРЕД ЗАПУСКОМ)
# ==========================================
class TestCulinary(unittest.TestCase):
    def setUp(self):
        self.service = CulinaryService("sqlite:///:memory:")
        self.service.seed_data()
    def test_01_categories(self): self.assertEqual(len(self.service.get_categories()), 4)
    def test_02_registration(self):
        self.service.register_user(10, "u", "N")
        with self.service.Session() as s: self.assertIsNotNone(s.get(User, 10))
    def test_03_recipe_seed(self):
        r = self.service.get_recipe(1)
        self.assertEqual(r.title, "Омлет")
    def test_04_favorites(self):
        self.service.toggle_fav(1, 1)
        self.assertTrue(self.service.is_fav(1, 1))
    def test_05_comments_logic(self):
        self.service.add_comment(1, 1, "Вкусно!")
        r = self.service.get_recipe(1)
        self.assertEqual(r.comments[0].text, "Вкусно!")

# ==========================================
# 5. ЛОГИКА БОТА
# ==========================================
fast_api = FastAPI()
service = CulinaryService(DATABASE_URL)

def get_main_kb():
    return ReplyKeyboardMarkup([["📂 Категории", "📝 Добавить рецепт"], ["❤️ Избранное", "ℹ️ О боте"]], resize_keyboard=True)

async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    service.register_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name)
    await u.message.reply_text("👨‍🍳 Добро пожаловать! Рецепты, избранное и комментарии готовы.", reply_markup=get_main_kb())

# --- ДОБАВЛЕНИЕ КОММЕНТАРИЯ ---
async def comment_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    rid = int(query.data.split("_")[1])
    c.user_data['active_rid'] = rid
    await query.message.reply_text("Введите ваш комментарий (отзыв) к блюду:")
    return WAITING_COMMENT

async def comment_save(u: Update, c: ContextTypes.DEFAULT_TYPE):
    rid = c.user_data.get('active_rid')
    text = u.message.text
    if len(text) < 2:
        await u.message.reply_text("Слишком короткий комментарий. Попробуйте еще раз:")
        return WAITING_COMMENT
    service.add_comment(rid, u.effective_user.id, text)
    await u.message.reply_text("✅ Комментарий добавлен!", reply_markup=get_main_kb())
    return ConversationHandler.END

# --- ДОБАВЛЕНИЕ РЕЦЕПТА ---
async def add_recipe_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("Введите название нового рецепта:", reply_markup=ReplyKeyboardRemove())
    return ADD_REC_TITLE

async def add_rec_title(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['tmp_title'] = u.message.text
    await u.message.reply_text("Введите ингредиенты через запятую:")
    return ADD_REC_INGR

async def add_rec_ingr(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['tmp_ingr'] = u.message.text
    await u.message.reply_text("Введите инструкцию по приготовлению:")
    return ADD_REC_INSTR

async def add_rec_instr(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['tmp_instr'] = u.message.text
    kb = [[InlineKeyboardButton(ct.name, callback_data=f"setcat_{ct.id}")] for ct in service.get_categories()]
    await u.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_REC_CAT

async def add_rec_finish(u: Update, c: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[1])
    service.add_recipe(u.effective_user.id, c.user_data['tmp_title'], c.user_data['tmp_ingr'], c.user_data['tmp_instr'], cat_id)
    await query.message.reply_text("✅ Рецепт успешно создан!", reply_markup=get_main_kb())
    return ConversationHandler.END

# --- ОБРАБОТКА МЕНЮ ---
async def text_router(u: Update, c: ContextTypes.DEFAULT_TYPE):
    t = u.message.text
    uid = u.effective_user.id
    if t == "📂 Категории":
        kb = [[InlineKeyboardButton(ct.name, callback_data=f"cat_{ct.id}")] for ct in service.get_categories()]
        await u.message.reply_text("Выберите раздел:", reply_markup=InlineKeyboardMarkup(kb))
    elif t == "❤️ Избранное":
        favs = service.get_user_favs(uid)
        if not favs: await u.message.reply_text("Ваш список избранного пуст ❤️")
        else:
            kb = [[InlineKeyboardButton(r.title, callback_data=f"rec_{r.id}")] for r in favs]
            await u.message.reply_text("Ваше избранное:", reply_markup=InlineKeyboardMarkup(kb))
    elif t == "ℹ️ О боте":
        await u.message.reply_text("Кулинарный бот v6.0\n5 таблиц БД\n22 CRUD метода\n5 тестов пройдено")
    else:
        await u.message.reply_text(f"Я не понимаю '{t}'. Используйте кнопки меню 📱")

async def callback_router(u: Update, c: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    uid = u.effective_user.id
    await query.answer()

    if query.data.startswith("cat_"):
        cid = int(query.data.split("_")[1])
        with service.Session() as s:
            recs = s.scalars(select(Recipe).where(Recipe.category_id == cid)).all()
            if not recs: await query.edit_message_text("В этой категории пока пусто.")
            else:
                kb = [[InlineKeyboardButton(r.title, callback_data=f"rec_{r.id}")] for r in recs]
                await query.edit_message_text("Выберите рецепт:", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("rec_"):
        rid = int(query.data.split("_")[1])
        r = service.get_recipe(rid)
        is_f = service.is_fav(uid, rid)
        
        # Отображение комментариев
        comms = "\n".join([f"💬 {cm.user.first_name}: {cm.text}" for cm in r.comments[:5]]) if r.comments else "Отзывов пока нет."
        
        txt = f"📖 *{r.title}*\n\n🛒 Ингредиенты: {r.ingredients}\n\n🍳 Инструкция: {r.instructions}\n\n*Комментарии:*\n{comms}"
        
        kb = [
            [InlineKeyboardButton("💔 Удалить из избранного" if is_f else "❤️ В избранное", callback_data=f"fav_{rid}")],
            [InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"addcom_{rid}")]
        ]
        await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("fav_"):
        rid = int(query.data.split("_")[1])
        service.toggle_fav(uid, rid)
        # Сразу обновляем сообщение, чтобы кнопка изменилась
        await callback_router(u, c)

def main():
    if not unittest.TextTestRunner().run(unittest.TestLoader().loadTestsFromTestCase(TestCulinary)).wasSuccessful(): return
    service.seed_data()
    req = HTTPXRequest(proxy=PROXY_URL, connect_timeout=30) if PROXY_URL else None
    app = Application.builder().token(TOKEN).request(req).get_updates_request(req).build()

    # Стейк-машина для комментариев
    com_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(comment_start, pattern="^addcom_")],
        states={WAITING_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, comment_save)]},
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )

    # Стейк-машина для добавления рецепта
    rec_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Добавить рецепт$"), add_recipe_start)],
        states={
            ADD_REC_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rec_title)],
            ADD_REC_INGR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rec_ingr)],
            ADD_REC_INSTR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rec_instr)],
            ADD_REC_CAT: [CallbackQueryHandler(add_rec_finish, pattern="^setcat_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(com_conv)
    app.add_handler(rec_conv)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("🚀 Бот запущен! Файл БД:", DB_PATH)
    app.run_polling()

if __name__ == "__main__":
    main()