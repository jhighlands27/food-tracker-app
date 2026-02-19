from fastapi import FastAPI, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import BackgroundTasks

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Float
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

from starlette.middleware.sessions import SessionMiddleware

from datetime import datetime, timedelta
import os
import hashlib
import base64
import shutil

# ----------------- APP SETUP -----------------

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key-change-this-in-production")

# Create uploads folder for images
os.makedirs("uploads", exist_ok=True)

# Only mount static if directory exists
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

templates = Jinja2Templates(directory="templates")

# ----------------- DATABASE -----------------

DATABASE_URL = "sqlite:///./food_tracker.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ----------------- MODELS -----------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    is_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    food_items = relationship("FoodItem", back_populates="user", cascade="all, delete-orphan")

class FoodItem(Base):
    __tablename__ = "food_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    barcode = Column(String, nullable=True)
    category = Column(String, nullable=True)
    purchase_date = Column(DateTime, default=datetime.utcnow)
    expiry_date = Column(DateTime, nullable=False)
    quantity = Column(Integer, default=1)
    price = Column(Float, nullable=True)  # NEW: Track item price
    image_path = Column(String, nullable=True)  # NEW: Store image path
    is_expired = Column(Boolean, default=False)
    is_used = Column(Boolean, default=False)  # NEW: Track if item was used
    
    user = relationship("User", back_populates="food_items")

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    food_item_id = Column(Integer)
    notification_type = Column(String)
    sent_at = Column(DateTime, default=datetime.utcnow)
    message = Column(String)

Base.metadata.create_all(bind=engine)

# ----------------- SECURITY UTILS -----------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str):
    return hashlib.sha256(password.encode()).hexdigest() == hashed

def require_login(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id

def require_admin(request: Request, db: Session):
    user_id = require_login(request)
    user = db.get(User, user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def get_current_user(request: Request, db: Session):
    user_id = require_login(request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# ----------------- FOOD TRACKING UTILS -----------------

def calculate_days_until_expiry(expiry_date: datetime) -> int:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    expiry = expiry_date.replace(hour=0, minute=0, second=0, microsecond=0)
    return (expiry - today).days

def check_and_send_notifications(db: Session):
    items = db.query(FoodItem).filter(FoodItem.is_expired == False).all()
    
    for item in items:
        days_left = calculate_days_until_expiry(item.expiry_date)
        
        if days_left < 0:
            item.is_expired = True
            send_notification(db, item, "expired", f"{item.name} has expired!")
            db.commit()
            continue
        
        existing_notifications = db.query(Notification).filter(
            Notification.food_item_id == item.id
        ).all()
        
        notification_types_sent = [n.notification_type for n in existing_notifications]
        
        if days_left <= 30 and "month" not in notification_types_sent:
            recipes = get_recipe_suggestions(db, item.user_id)
            send_notification(db, item, "month", 
                f"{item.name} expires in a month! Recipe ideas: {recipes}")
        
        if days_left <= 7 and "week" not in notification_types_sent:
            send_notification(db, item, "week", 
                f"{item.name} expires in a week! Use it soon.")
        
        if days_left <= 1 and "day" not in notification_types_sent:
            send_notification(db, item, "day", 
                f"{item.name} expires tomorrow! Use it today.")

def send_notification(db: Session, item: FoodItem, notif_type: str, message: str):
    notification = Notification(
        user_id=item.user_id,
        food_item_id=item.id,
        notification_type=notif_type,
        message=message
    )
    db.add(notification)
    db.commit()
    
    print(f"NOTIFICATION [{notif_type}]: {message}")
    
    # NEW: Send SMS if phone number exists
    user = db.get(User, item.user_id)
    if user and user.phone:
        send_sms_notification(user.phone, message)

def send_sms_notification(phone: str, message: str):
    """
    Placeholder for SMS notifications
    To enable: pip install twilio
    Then add your Twilio credentials
    """
    print(f"SMS to {phone}: {message}")
    # Uncomment below to enable real SMS:
    # from twilio.rest import Client
    # client = Client(account_sid, auth_token)
    # client.messages.create(to=phone, from_=twilio_number, body=message)

def get_recipe_suggestions(db: Session, user_id: int) -> str:
    thirty_days = datetime.now() + timedelta(days=30)
    expiring_items = db.query(FoodItem).filter(
        FoodItem.user_id == user_id,
        FoodItem.expiry_date <= thirty_days,
        FoodItem.is_expired == False
    ).all()
    
    ingredients = [item.name for item in expiring_items]
    
    if not ingredients:
        return "No recipes available"
    
    return f"You have: {', '.join(ingredients[:3])}. Try making a stir-fry, soup, or salad!"

def calculate_user_stats(db: Session, user_id: int):
    """Calculate statistics for user dashboard"""
    all_items = db.query(FoodItem).filter(FoodItem.user_id == user_id).all()
    
    total_items = len(all_items)
    expired_items = len([i for i in all_items if i.is_expired])
    used_items = len([i for i in all_items if i.is_used])
    active_items = len([i for i in all_items if not i.is_expired and not i.is_used])
    
    # Calculate money saved (items used before expiry)
    money_saved = sum([i.price or 5.0 for i in all_items if i.is_used])
    
    # Calculate money wasted (expired items)
    money_wasted = sum([i.price or 5.0 for i in all_items if i.is_expired])
    
    # Calculate waste percentage
    waste_percentage = (expired_items / total_items * 100) if total_items > 0 else 0
    
    return {
        "total_items": total_items,
        "expired_items": expired_items,
        "used_items": used_items,
        "active_items": active_items,
        "money_saved": round(money_saved, 2),
        "money_wasted": round(money_wasted, 2),
        "waste_percentage": round(waste_percentage, 1)
    }

# ----------------- AUTH ROUTES -----------------

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/login")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username).first()

    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid username or password"
        })

    if not user.is_approved:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Your account is pending admin approval"
        })

    request.session["user_id"] = user.id

    if user.is_admin:
        return RedirectResponse("/admin/dashboard", status_code=302)

    return RedirectResponse("/dashboard", status_code=302)

@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})

@app.post("/signup")
def signup(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(""),
    db: Session = Depends(get_db)
):
    existing_user = db.query(User).filter(
        (User.username == username) | (User.email == email)
    ).first()
    
    if existing_user:
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "error": "Username or email already exists"
        })

    is_first_user = db.query(User).count() == 0

    user = User(
        username=username,
        email=email,
        password=hash_password(password),
        phone=phone if phone else None,
        is_admin=is_first_user,
        is_approved=is_first_user
    )

    db.add(user)
    db.commit()

    return RedirectResponse("/login", status_code=302)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

# ----------------- USER DASHBOARD -----------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    
    # Auto-check notifications when viewing dashboard
    check_and_send_notifications(db)
    
    items = db.query(FoodItem).filter(
        FoodItem.user_id == user.id,
        FoodItem.is_used == False  # Don't show used items
    ).order_by(FoodItem.expiry_date).all()
    
    expired = []
    expiring_soon = []
    fresh = []
    
    for item in items:
        days = calculate_days_until_expiry(item.expiry_date)
        item.days_remaining = max(0, days)
        
        # Auto-mark items as expired if past expiry date
        if days < 0 and not item.is_expired:
            item.is_expired = True
            db.commit()
        
        if item.is_expired or days < 0:
            expired.append(item)
        elif days <= 7:
            expiring_soon.append(item)
        else:
            fresh.append(item)
    
    # Get statistics
    stats = calculate_user_stats(db, user.id)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "expired": expired,
        "expiring_soon": expiring_soon,
        "fresh": fresh,
        "stats": stats
    })

@app.get("/add-item", response_class=HTMLResponse)
def add_item_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse("add_item.html", {
        "request": request,
        "user": user
    })

@app.post("/add-item")
async def add_item(
    request: Request,
    name: str = Form(...),
    barcode: str = Form(""),
    category: str = Form(""),
    expiry_date: str = Form(...),
    quantity: int = Form(1),
    price: float = Form(None),
    image: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
    
    # Handle image upload
    image_path = None
    if image and image.filename:
        # Create unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{user.id}_{timestamp}_{image.filename}"
        filepath = os.path.join("uploads", filename)
        
        # Save image
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        
        image_path = f"/uploads/{filename}"
    
    item = FoodItem(
        user_id=user.id,
        name=name,
        barcode=barcode if barcode else None,
        category=category if category else None,
        expiry_date=expiry,
        quantity=quantity,
        price=price,
        image_path=image_path
    )
    
    db.add(item)
    db.commit()
    
    return RedirectResponse("/dashboard", status_code=302)

@app.post("/item/{item_id}/delete")
def delete_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    item = db.query(FoodItem).filter(
        FoodItem.id == item_id,
        FoodItem.user_id == user.id
    ).first()
    
    if item:
        # Delete image if exists
        if item.image_path:
            try:
                os.remove(item.image_path.replace("/uploads/", "uploads/"))
            except:
                pass
        
        db.delete(item)
        db.commit()
    
    return RedirectResponse("/dashboard", status_code=302)

@app.post("/item/{item_id}/mark-used")
def mark_item_used(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    item = db.query(FoodItem).filter(
        FoodItem.id == item_id,
        FoodItem.user_id == user.id
    ).first()
    
    if item:
        item.is_used = True
        db.commit()
    
    return RedirectResponse("/dashboard", status_code=302)

@app.get("/statistics", response_class=HTMLResponse)
def statistics_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    stats = calculate_user_stats(db, user.id)
    
    # Get monthly data for charts
    all_items = db.query(FoodItem).filter(FoodItem.user_id == user.id).all()
    
    return templates.TemplateResponse("statistics.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "all_items": all_items
    })

@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    
    notifications = db.query(Notification).filter(
        Notification.user_id == user.id
    ).order_by(Notification.sent_at.desc()).limit(50).all()
    
    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "user": user,
        "notifications": notifications
    })

@app.get("/recipes", response_class=HTMLResponse)
def recipes_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    
    thirty_days = datetime.now() + timedelta(days=30)
    expiring_items = db.query(FoodItem).filter(
        FoodItem.user_id == user.id,
        FoodItem.expiry_date <= thirty_days,
        FoodItem.is_expired == False,
        FoodItem.is_used == False
    ).all()
    
    return templates.TemplateResponse("recipes.html", {
        "request": request,
        "user": user,
        "ingredients": expiring_items
    })

@app.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse("scanner.html", {
        "request": request,
        "user": user
    })

# ----------------- ADMIN ROUTES -----------------

@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    
    pending_users = db.query(User).filter(User.is_approved == False).all()
    all_users = db.query(User).all()
    
    total_items = db.query(FoodItem).count()
    expired_items = db.query(FoodItem).filter(FoodItem.is_expired == True).count()
    
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "admin": admin,
        "pending_users": pending_users,
        "all_users": all_users,
        "total_items": total_items,
        "expired_items": expired_items
    })

@app.post("/admin/approve/{user_id}")
def approve_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    require_admin(request, db)
    
    user = db.get(User, user_id)
    if user:
        user.is_approved = True
        db.commit()
    
    return RedirectResponse("/admin/dashboard", status_code=302)

@app.post("/admin/delete-user/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    admin = require_admin(request, db)
    
    if user_id == admin.id:
        return RedirectResponse("/admin/dashboard", status_code=302)
    
    user = db.get(User, user_id)
    if user:
        db.delete(user)
        db.commit()
    
    return RedirectResponse("/admin/dashboard", status_code=302)

@app.get("/check-notifications")
def check_notifications_endpoint(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    check_and_send_notifications(db)
    return {"status": "Notifications checked"}