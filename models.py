from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Business(db.Model):
    __tablename__ = "businesses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(160), unique=True, nullable=False, index=True)
    category = db.Column(db.String(120), nullable=False)
    google_review_url = db.Column(db.String(600), nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    logo_path = db.Column(db.String(300), nullable=True)
    qr_code_path = db.Column(db.String(300), nullable=True)
    access_code = db.Column(db.String(12), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # --- تخصيص الهوية والصفحة ---
    brand_color = db.Column(db.String(20), nullable=True)          # لون النشاط الأساسي
    about = db.Column(db.Text, nullable=True)                       # نبذة قصيرة عن النشاط
    welcome_message = db.Column(db.String(200), nullable=True)      # نص صفحة التقييم
    happy_message = db.Column(db.String(300), nullable=True)        # نص صفحة العميل الراضي

    # --- روابط التواصل والموقع ---
    whatsapp = db.Column(db.String(40), nullable=True)             # 9665xxxxxxxx للطلب/التواصل
    location_url = db.Column(db.String(600), nullable=True)        # رابط الموقع على الخرائط
    instagram_url = db.Column(db.String(300), nullable=True)
    snapchat_url = db.Column(db.String(300), nullable=True)
    tiktok_url = db.Column(db.String(300), nullable=True)
    x_url = db.Column(db.String(300), nullable=True)

    # --- واي فاي ---
    wifi_ssid = db.Column(db.String(120), nullable=True)
    wifi_password = db.Column(db.String(120), nullable=True)
    wifi_encryption = db.Column(db.String(10), nullable=True)      # WPA / WEP / nopass
    wifi_qr_path = db.Column(db.String(300), nullable=True)

    # --- QR صفحة النشاط الموحّدة (الهَب) ---
    hub_qr_path = db.Column(db.String(300), nullable=True)

    # --- مفاتيح تشغيل الميزات في صفحة النشاط ---
    menu_enabled = db.Column(db.Boolean, default=True, nullable=False)
    offers_enabled = db.Column(db.Boolean, default=True, nullable=False)
    reservations_enabled = db.Column(db.Boolean, default=False, nullable=False)
    loyalty_enabled = db.Column(db.Boolean, default=False, nullable=False)
    wifi_enabled = db.Column(db.Boolean, default=False, nullable=False)

    # --- إعدادات بطاقة الولاء ---
    loyalty_goal = db.Column(db.Integer, default=8, nullable=True)        # عدد الأختام للمكافأة
    loyalty_reward = db.Column(db.String(200), nullable=True)            # وصف المكافأة
    staff_pin = db.Column(db.String(12), nullable=True)                  # رمز الموظف لإضافة ختم

    # --- عدّادات المشاهدات (تحليلات) ---
    hub_views = db.Column(db.Integer, default=0, nullable=False)
    menu_views = db.Column(db.Integer, default=0, nullable=False)
    rating_views = db.Column(db.Integer, default=0, nullable=False)

    feedbacks = db.relationship(
        "Feedback", backref="business", lazy=True, cascade="all, delete-orphan"
    )
    menu_categories = db.relationship(
        "MenuCategory", backref="business", lazy=True, cascade="all, delete-orphan"
    )
    menu_items = db.relationship(
        "MenuItem", backref="business", lazy=True, cascade="all, delete-orphan"
    )
    menu_images = db.relationship(
        "MenuImage", backref="business", lazy=True, cascade="all, delete-orphan"
    )
    offers = db.relationship(
        "Offer", backref="business", lazy=True, cascade="all, delete-orphan"
    )
    reservations = db.relationship(
        "Reservation", backref="business", lazy=True, cascade="all, delete-orphan"
    )
    loyalty_members = db.relationship(
        "LoyaltyMember", backref="business", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def color(self):
        return self.brand_color or "#16A34A"

    def __repr__(self):
        return f"<Business {self.slug}>"


class Feedback(db.Model):
    __tablename__ = "feedbacks"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(40), nullable=True)  # تصنيف الملاحظة (طعم/خدمة/نظافة/سعر/أخرى)
    customer_name = db.Column(db.String(160), nullable=True)
    customer_phone = db.Column(db.String(40), nullable=True)
    clicked_google = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Feedback {self.id} rating={self.rating}>"


class MenuCategory(db.Model):
    __tablename__ = "menu_categories"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    items = db.relationship(
        "MenuItem", backref="category", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<MenuCategory {self.name}>"


class MenuItem(db.Model):
    __tablename__ = "menu_items"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    category_id = db.Column(
        db.Integer, db.ForeignKey("menu_categories.id"), nullable=True, index=True
    )
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(400), nullable=True)
    price = db.Column(db.Float, nullable=True)
    image_path = db.Column(db.String(300), nullable=True)
    is_available = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<MenuItem {self.name}>"


class MenuImage(db.Model):
    """صورة منيو جاهزة (لأن أغلب المطاعم منيوهم على شكل صورة)."""

    __tablename__ = "menu_images"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    image_path = db.Column(db.String(300), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<MenuImage {self.id}>"


class Offer(db.Model):
    __tablename__ = "offers"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(400), nullable=True)
    emoji = db.Column(db.String(8), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Offer {self.title}>"


class Reservation(db.Model):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    customer_name = db.Column(db.String(160), nullable=False)
    customer_phone = db.Column(db.String(40), nullable=False)
    party_size = db.Column(db.Integer, nullable=True)
    requested_time = db.Column(db.String(120), nullable=True)  # نص حر: اليوم/الوقت
    note = db.Column(db.String(400), nullable=True)
    status = db.Column(db.String(20), default="new", nullable=False)  # new/confirmed/done/cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Reservation {self.id} {self.status}>"


class LoyaltyMember(db.Model):
    __tablename__ = "loyalty_members"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True
    )
    phone = db.Column(db.String(40), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=True)
    stamps = db.Column(db.Integer, default=0, nullable=False)
    rewards_redeemed = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_stamp_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<LoyaltyMember {self.phone} stamps={self.stamps}>"
