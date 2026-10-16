import os
import datetime
from fastapi import FastAPI, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_БОТА_ОТ_BOTFATHER")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://твой-сайт.onrender.com") # Ссылка на хостинг
DATABASE_URL = "sqlite:///./poizon_service.db"
# ==========================================

app = FastAPI()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- База данных ---
class DBUser(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True)
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    orders_count = Column(Integer, default=0)

class DBOrder(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, index=True)
    phone = Column(String, nullable=True)
    item_link = Column(String)
    size = Column(String)
    cny_price = Float
    total_price = Float
    status = Column(String, default="Ожидает предоплаты")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Инициализация Telegram Бота ---
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    db = SessionLocal()
    user = db.query(DBUser).filter(DBUser.telegram_id == message.from_user.id).first()
    db.close()

    if not user or not user.phone:
        # Запрашиваем номер телефона
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            "Привет! Добро пожаловать в **POIZON PICKUP!**\nДля оформления заказов и расчета скидок поделитесь, пожалуйста, номером телефона:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    else:
        await send_welcome_with_webapp(message)

@dp.message(F.contact)
async def handle_contact(message: Message):
    db = SessionLocal()
    tg_id = message.from_user.id
    phone = message.contact.phone_number
    name = message.from_user.full_name

    user = db.query(DBUser).filter(DBUser.telegram_id == tg_id).first()
    if not user:
        user = DBUser(telegram_id=tg_id, full_name=name, phone=phone, orders_count=0)
        db.add(user)
    else:
        user.phone = phone
        user.full_name = name
    db.commit()
    db.close()

    await message.answer("Спасибо! Номер успешно сохранен.")
    await send_welcome_with_webapp(message)

async def send_welcome_with_webapp(message: Message):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧮 Открыть калькулятор заказа", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer("Нажми кнопку ниже, чтобы рассчитать стоимость товара и оформить заказ:", reply_markup=kb)

# --- API для WebApp ---
class OrderData(BaseModel):
    telegram_id: int
    item_link: str
    size: str
    cny_price: float

@app.get("/api/check-user/{telegram_id}")
def check_user(telegram_id: int, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.telegram_id == telegram_id).first()
    # Логика: новый клиент — если заказов меньше 2 (то есть 0 или 1)
    # С 3-го заказа (orders_count >= 2) — постоянный клиент
    if not user or user.orders_count < 2:
        return {"client_type": "new", "discount_rate": 0.20}
    else:
        return {"client_type": "regular", "discount_rate": 0.15}

@app.post("/api/create-order")
def create_order(data: OrderData, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.telegram_id == data.telegram_id).first()
    phone = user.phone if user else "Не указан"
    
    orders_count = user.orders_count if user else 0
    comm_rate = 0.20 if orders_count < 2 else 0.15
    
    item_rub = data.cny_price * 13.8 # Курс
    min_comm = 1500 if comm_rate == 0.20 else 1000
    commission = max(item_rub * comm_rate, min_comm)
    total = item_rub + commission

    new_order = DBOrder(
        telegram_id=data.telegram_id,
        phone=phone,
        item_link=data.item_link,
        size=data.size,
        cny_price=data.cny_price,
        total_price=total,
        status="Ожидает предоплаты"
    )
    db.add(new_order)
    
    if user:
        user.orders_count += 1
    db.commit()
    db.refresh(new_order)

    return {"status": "success", "order_id": new_order.id, "total": total}

# --- Простая Админка в браузере ---
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(db: Session = Depends(get_db)):
    orders = db.query(DBOrder).order_by(DBOrder.id.desc()).all()
    rows = ""
    for o in orders:
        rows += f"""
        <tr>
            <td>#{o.id}</td>
            <td>{o.telegram_id}</td>
            <td>{o.phone}</td>
            <td><a href="{o.item_link}" target="_blank">Ссылка</a></td>
            <td>{o.size}</td>
            <td>{o.cny_price} ¥</td>
            <td><b>{o.total_price} ₽</b></td>
            <td>{o.status}</td>
            <td>{o.created_at.strftime('%d.%m %H:%M')}</td>
        </tr>
        """
    return f"""
    <html>
    <head>
        <title>Админка — Poizon Pickup</title>
        <style>
            body {{ font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; background: #1e293b; border-radius: 8px; overflow: hidden; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #334155; font-size: 14px; }}
            th {{ background: #0284c7; color: white; }}
            a {{ color: #38bdf8; }}
        </style>
    </head>
    <body>
        <h1>👑 Админ-панель заказов</h1>
        <table>
            <tr>
                <th>ID</th>
                <th>TG ID</th>
                <th>Телефон</th>
                <th>Товар</th>
                <th>Размер</th>
                <th>Цена (¥)</th>
                <th>Итого (₽)</th>
                <th>Статус</th>
                <th>Дата</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """