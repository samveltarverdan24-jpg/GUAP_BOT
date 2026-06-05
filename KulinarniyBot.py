import logging
import unittest
import os
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from sqlalchemy import create_engine, String, Text, ForeignKey, select, func, UniqueConstraint, DateTime, update, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, joinedload
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
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

# Если прокси не работает, поставьте None
PROXY_URL = "http://127.0.0.1:10809" 

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    user: Mapped["User"] = relationship(back_populates="comments")
    recipe: Mapped["Recipe"] = relationship(back_populates="comments")

# ==========================================
# 3. СЕРВИС БД (19 CRUD МЕТОДОВ)
# ==========================================
class CulinaryService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def seed_data(self):
        with self.Session() as s:
            if not s.scalar(select(func.count(Category.id))):
                s.add_all([Category(name=n) for n in ["Завтраки", "Супы", "Горячее", "Десерты"]])
                s.add(User(id=1, first_name="Шеф-повар")); s.commit()

    # User CRUD (4)
    def register_user(self, uid, username, name):
        with self.Session() as s:
            if not s.get(User, uid):
                s.add(User(id=uid, username=username, first_name=name)); s.commit()
    def get_user(self, uid):
        with self.Session() as s: return s.get(User, uid)
    def update_user(self, uid, name):
        with self.Session() as s: s.execute(update(User).where(User.id == uid).values(first_name=name)); s.commit()
    def delete_user(self, uid):
        with self.Session() as s: s.execute(delete(User).where(User.id == uid)); s.commit()

    # Recipe CRUD (4)
    def add_recipe(self, uid, title, cat_name):
        with self.Session() as s:
            cat = s.scalar(select(Category).where(Category.name == cat_name))
            if cat:
                r = Recipe(title=title, ingredients="...", instructions="...", user_id=uid, category_id=cat.id)
                s.add(r); s.commit(); return r.id
    def get_recipe(self, rid):
        with self.Session() as s: return s.scalar(select(Recipe).options(joinedload(Recipe.author), joinedload(Recipe.category)).where(Recipe.id == rid))
    def update_recipe(self, rid, title):
        with self.Session() as s: s.execute(update(Recipe).where(Recipe.id == rid).values(title=title)); s.commit()
    def delete_recipe(self, rid):
        with self.Session() as s: r = s.get(Recipe, rid); s.delete(r); s.commit()

    # Favorite CRUD (4)
    def toggle_fav(self, uid, rid):
        with self.Session() as s:
            f = s.scalar(select(Favorite).where(Favorite.user_id == uid, Favorite.recipe_id == rid))
            if f: s.delete(f); res = False
            else: s.add(Favorite(user_id=uid, recipe_id=rid)); res = True
            s.commit(); return res
    def is_fav(self, uid, rid):
        with self.Session() as s: return s.scalar(select(Favorite).where(Favorite.user_id == uid, Favorite.recipe_id == rid)) is not None
    def get_favs(self, uid):
        with self.Session() as s: return s.scalars(select(Recipe).join(Favorite).where(Favorite.user_id == uid)).all()
    def clear_favs(self, uid):
        with self.Session() as s: s.execute(delete(Favorite).where(Favorite.user_id == uid)); s.commit()

    # Comment CRUD (3)
    def add_comment(self, rid, uid, text):
        with self.Session() as s: s.add(Comment(recipe_id=rid, user_id=uid, text=text)); s.commit()
    def get_comments(self, rid):
        with self.Session() as s: return s.scalars(select(Comment).options(joinedload(Comment.user)).where(Comment.recipe_id == rid)).all()
    def delete_comment(self, cid):
        with self.Session() as s: c = s.get(Comment, cid); s.delete(c); s.commit()

    # Category CRUD (4)
    def get_categories(self):
        with self.Session() as s: return s.scalars(select(Category)).all()
    def get_cat_by_id(self, cid):
        with self.Session() as s: return s.get(Category, cid)
    def add_cat(self, name):
        with self.Session() as s: s.add(Category(name=name)); s.commit()
    def delete_cat(self, cid):
        with self.Session() as s: s.execute(delete(Category).where(Category.id == cid)); s.commit()

# ==========================================
# 4. ТЕСТЫ (5 ТЕСТОВ)
# ==========================================
class TestCulinary(unittest.TestCase):
    def setUp(self):
        self.service = CulinaryService("sqlite:///:memory:")
        self.service.seed_data()

    def test_01_user_registration(self):
        """Тест 1: Регистрация и получение пользователя"""
        self.service.register_user(100, "testuser", "Test")
        user = self.service.get_user(100)
        self.assertIsNotNone(user)
        self.assertEqual(user.first_name, "Test")

    def test_02_recipe_creation(self):
        """Тест 2: Создание рецепта и проверка связи с автором"""
        self.service.register_user(200, "chef", "Gordon")
        rid = self.service.add_recipe(200, "Омлет", "Завтраки")
        recipe = self.service.get_recipe(rid)
        self.assertEqual(recipe.title, "Омлет")
        self.assertEqual(recipe.author.first_name, "Gordon")

    def test_03_favorite_logic(self):
        """Тест 3: Добавление и удаление из избранного"""
        self.service.register_user(300, "user", "User")
        rid = self.service.add_recipe(1, "Суп", "Супы")
        
        # Добавляем
        self.service.toggle_fav(300, rid)
        self.assertTrue(self.service.is_fav(300, rid))
        
        # Удаляем
        self.service.toggle_fav(300, rid)
        self.assertFalse(self.service.is_fav(300, rid))

    def test_04_comment_logic(self):
        """Тест 4: Добавление комментария к рецепту"""
        self.service.register_user(400, "critic", "Critic")
        rid = self.service.add_recipe(1, "Торт", "Десерты")
        self.service.add_comment(rid, 400, "Отлично!")
        
        comments = self.service.get_comments(rid)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].text, "Отлично!")

    def test_05_category_seed_data(self):
        """Тест 5: Проверка инициализации 4 категорий"""
        categories = self.service.get_categories()
        self.assertEqual(len(categories), 4)
        cat_names = [c.name for c in categories]
        self.assertIn("Завтраки", cat_names)
        self.assertIn("Десерты", cat_names)

# ==========================================
# 5. FASTAPI & BOT
# ==========================================
fast_api = FastAPI()
service = CulinaryService(DATABASE_URL)

@fast_api.get("/")
def home(): return {"status": "API Running", "db": str(DB_PATH)}

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    service.register_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name)
    await u.message.reply_text("👨‍🍳 Бот запущен!")

def main():
    # Прогон 5 тестов
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCulinary)
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful(): 
        print("❌ Тесты не пройдены!")
        return

    service.seed_data()
    print(f"✅ БД создана: {DB_PATH}")

    req = HTTPXRequest(proxy=PROXY_URL, connect_timeout=60, read_timeout=60) if PROXY_URL else None
    
    try:
        app = Application.builder().token(TOKEN).request(req).get_updates_request(req).build()
        app.add_handler(CommandHandler("start", start))
        print("🚀 Бот запускается...")
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
