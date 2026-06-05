import logging
import unittest
import os
from datetime import datetime
from typing import List, Optional
from sqlalchemy import create_engine, String, Text, ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = "http://127.0.0.1:10809"  
DB_URL = "sqlite:///culinary_expert.db"

# Состояния диалога
ADD_TITLE, ADD_CATEGORY, ADD_INGREDIENTS, ADD_INSTRUCTIONS = range(4)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# МОДУЛЬ 1: БАЗА ДАННЫХ (ORM)
# ==========================================
class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(50))
    first_name: Mapped[str] = mapped_column(String(50))
    recipes: Mapped[List["Recipe"]] = relationship(back_populates="author")

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

class CulinaryService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def seed_data(self):
        with self.Session() as session:
            if not session.scalar(select(Category)):
                system_user = User(id=1, first_name="Шеф-повар")
                session.add(system_user)
                cats = {
                    "Завтраки": Category(name="Завтраки"),
                    "Супы": Category(name="Супы"),
                    "Горячее": Category(name="Горячее"),
                    "Десерты": Category(name="Десерты")
                }
                session.add_all(cats.values())
                session.flush()
                recipes = [
                    Recipe(title="Омлет", ingredients="Яйца", instructions="Жарить", user_id=1, category_id=cats["Завтраки"].id),
                    Recipe(title="Борщ", ingredients="Мясо", instructions="Варить", user_id=1, category_id=cats["Супы"].id)
                ]
                session.add_all(recipes)
                session.commit()

    def register_user(self, user_id, username, first_name):
        with self.Session() as session:
            if not session.get(User, user_id):
                session.add(User(id=user_id, username=username, first_name=first_name))
                session.commit()

    def add_recipe(self, user_id, title, cat_name, ingredients, instructions):
        with self.Session() as session:
            cat = session.scalar(select(Category).where(Category.name == cat_name))
            if cat:
                session.add(Recipe(title=title, ingredients=ingredients, instructions=instructions, 
                                   user_id=user_id, category_id=cat.id))
                session.commit()

# ==========================================
# МОДУЛЬ 2: ТЕСТОВОЕ ОКРУЖЕНИЕ (UNIT TESTS)
# ==========================================
class TestCulinaryExpert(unittest.TestCase):
    def setUp(self):
        # Тесты всегда запускаются в оперативной памяти (чистое окружение)
        self.service = CulinaryService("sqlite:///:memory:")
        self.service.seed_data()

    def test_seed_categories(self):
        """Проверка инициализации категорий"""
        with self.service.Session() as session:
            cats = session.scalars(select(Category)).all()
            self.assertEqual(len(cats), 4)

    def test_recipe_addition(self):
        """Проверка добавления рецепта"""
        self.service.register_user(10, "tester", "Test")
        self.service.add_recipe(10, "Блюдо", "Завтраки", "Ингр", "Инстр")
        with self.service.Session() as session:
            r = session.scalar(select(Recipe).where(Recipe.title == "Блюдо"))
            self.assertIsNotNone(r)
            self.assertEqual(r.category.name, "Завтраки")

    # НОВЫЙ ТЕСТ 1: Регистрация пользователя
    def test_user_registration(self):
        """Проверка корректной регистрации пользователя"""
        self.service.register_user(12345, "ivan_bot", "Ivan")
        with self.service.Session() as session:
            user = session.get(User, 12345)
            self.assertIsNotNone(user)
            self.assertEqual(user.first_name, "Ivan")
            self.assertEqual(user.username, "ivan_bot")

    # НОВЫЙ ТЕСТ 2: Связь Пользователь -> Рецепты
    def test_user_recipes_link(self):
        """Проверка того, что рецепты привязаны к автору"""
        uid = 500
        self.service.register_user(uid, "chef", "Gordon")
        self.service.add_recipe(uid, "Стейк", "Горячее", "Мясо", "Жарить")
        self.service.add_recipe(uid, "Пюре", "Горячее", "Картошка", "Варить")
        
        with self.service.Session() as session:
            user = session.get(User, uid)
            # Проверяем количество рецептов через связь в ORM
            self.assertEqual(len(user.recipes), 2)
            titles = [r.title for r in user.recipes]
            self.assertIn("Стейк", titles)

    # НОВЫЙ ТЕСТ 3: Ошибка категории
    def test_invalid_category_handling(self):
        """Проверка того, что рецепт не добавляется в несуществующую категорию"""
        self.service.register_user(1, "admin", "Admin")
        # Пытаемся добавить в категорию 'Космос' (ее нет в seed_data)
        self.service.add_recipe(1, "Звездная пыль", "Космос", "Пыль", "Собрать")
        
        with self.service.Session() as session:
            r = session.scalar(select(Recipe).where(Recipe.title == "Звездная пыль"))
            self.assertIsNone(r) # Рецепт не должен быть создан

def run_tests():
    print("🧪 Запуск модульных тестов (5 тестов)...")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCulinaryExpert)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result.wasSuccessful()

# ==========================================
# МОДУЛЬ 3: ЛОГИКА БОТА
# ==========================================
service = CulinaryService(DB_URL)

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup([["📂 Категории", "📝 Добавить рецепт"], ["ℹ️ О боте"]], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    service.register_user(user.id, user.username, user.first_name)
    await update.message.reply_text(f"👨‍🍳 Привет, {user.first_name}! Я твоя Книга Рецептов.", reply_markup=get_main_menu_keyboard())
    
    with service.Session() as session:
        cats = session.scalars(select(Category)).all()
        kb = [[InlineKeyboardButton(f"📂 {c.name}", callback_data=f"cat_{c.id}")] for c in cats]
    await update.message.reply_text("Выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))

# ... остальной код бота без изменений ...
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📂 Категории":
        with service.Session() as session:
            cats = session.scalars(select(Category)).all()
            kb = [[InlineKeyboardButton(f"📂 {c.name}", callback_data=f"cat_{c.id}")] for c in cats]
        await update.message.reply_text("Категории блюд:", reply_markup=InlineKeyboardMarkup(kb))
    elif text == "ℹ️ О боте":
        await update.message.reply_text("Бот 'Книга Рецептов' v2.7\nВсе тесты (5/5) пройдены успешно!")
    else:
        await update.message.reply_text("Это что-то на не съедобном 🥴")

async def browse_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    with service.Session() as session:
        if query.data == "back_start":
            cats = session.scalars(select(Category)).all()
            kb = [[InlineKeyboardButton(f"📂 {c.name}", callback_data=f"cat_{c.id}")] for c in cats]
            await query.edit_message_text("Категории:", reply_markup=InlineKeyboardMarkup(kb))
        elif query.data.startswith("cat_"):
            cat = session.get(Category, int(query.data.split("_")[1]))
            kb = [[InlineKeyboardButton(r.title, callback_data=f"rec_{r.id}")] for r in cat.recipes]
            kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_start")])
            await query.edit_message_text(f"Рецепты {cat.name}:", reply_markup=InlineKeyboardMarkup(kb))
        elif query.data.startswith("rec_"):
            r = session.get(Recipe, int(query.data.split("_")[1]))
            msg = f"📖 *{r.title}*\n\n🛒 *Ингредиенты:*\n{r.ingredients}\n\n🍳 *Инструкция:*\n{r.instructions}"
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_{r.category_id}")]]))

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query: await update.callback_query.answer()
    await msg.reply_text("📝 Введите название блюда:", reply_markup=ReplyKeyboardRemove())
    return ADD_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['title'] = update.message.text
    with service.Session() as session:
        cats = session.scalars(select(Category)).all()
        kb = [[c.name] for c in cats]
    await update.message.reply_text("Выберите категорию:", reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True))
    return ADD_CATEGORY

async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cat'] = update.message.text
    await update.message.reply_text("🛒 *Инструкция:* Введите ингредиенты (с новой строки):", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return ADD_INGREDIENTS

async def add_ingredients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ingredients'] = update.message.text
    await update.message.reply_text("👨‍🍳 *Инструкция:* Опишите пошаговый процесс приготовления:", parse_mode="Markdown")
    return ADD_INSTRUCTIONS

async def add_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service.add_recipe(update.effective_user.id, context.user_data['title'], context.user_data['cat'], context.user_data['ingredients'], update.message.text)
    await update.message.reply_text("✅ Рецепт сохранен!", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

def main():
    if not run_tests():
        print("❌ Тесты провалены. Запуск бота отменен.")
        return

    print("✅ Все 5 тестов пройдены. Запуск бота...")
    service.seed_data()
    
    request = HTTPXRequest(proxy=PROXY_URL, connect_timeout=20.0, read_timeout=20.0)
    app = Application.builder().token(TOKEN).request(request).get_updates_request(request).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Добавить рецепт$"), add_start), CallbackQueryHandler(add_start, pattern="add_new")],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category)],
            ADD_INGREDIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ingredients)],
            ADD_INSTRUCTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_done)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(browse_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
