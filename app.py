import os
import re
import unicodedata
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

import qrcode
from dotenv import load_dotenv
import secrets

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from PIL import Image
from sqlalchemy import func
from werkzeug.middleware.proxy_fix import ProxyFix

from models import (
    Business,
    Feedback,
    LoyaltyMember,
    MenuCategory,
    MenuImage,
    MenuItem,
    Offer,
    Reservation,
    db,
)

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# مجلد البيانات: محليًا داخل المشروع، وعلى Render يشير للقرص الدائم عبر متغير DATA_DIR
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
QR_DIR = os.path.join(DATA_DIR, "qrcodes")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_LOGO_SIZE = (600, 600)
MAX_MENU_IMAGE_SIZE = (1400, 1800)  # أكبر عشان نص المنيو يضل واضح

# رمز الدخول للوحة الإدارة (إضافة الأنشطة). يُضبط من إعدادات Render، ولا يُكتب في الكود.
ADMIN_CODE = (os.getenv("ADMIN_CODE") or "").strip()


def create_app():
    app = Flask(__name__, instance_path=INSTANCE_DIR)
    # خلف بروكسي مثل Render: يجعل url_for(_external=True) يستخدم https والدومين الصحيح
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-key")

    db_url = os.getenv("DATABASE_URL", "sqlite:///database.db")
    if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite:////"):
        rel = db_url.replace("sqlite:///", "", 1)
        db_url = "sqlite:///" + os.path.join(DATA_DIR, rel)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_schema()

    register_routes(app)
    return app


def ensure_schema():
    """ترقية بسيطة لقاعدة البيانات: تضيف الأعمدة الناقصة دون حذف البيانات."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    existing = {col["name"] for col in inspector.get_columns("businesses")}

    # (اسم العمود, تعريف SQL) — تُضاف فقط إن لم تكن موجودة
    biz_columns = [
        ("logo_path", "VARCHAR(300)"),
        ("access_code", "VARCHAR(12)"),
        ("is_active", "BOOLEAN DEFAULT 1"),
        ("brand_color", "VARCHAR(20)"),
        ("about", "TEXT"),
        ("welcome_message", "VARCHAR(200)"),
        ("happy_message", "VARCHAR(300)"),
        ("whatsapp", "VARCHAR(40)"),
        ("location_url", "VARCHAR(600)"),
        ("instagram_url", "VARCHAR(300)"),
        ("snapchat_url", "VARCHAR(300)"),
        ("tiktok_url", "VARCHAR(300)"),
        ("x_url", "VARCHAR(300)"),
        ("wifi_ssid", "VARCHAR(120)"),
        ("wifi_password", "VARCHAR(120)"),
        ("wifi_encryption", "VARCHAR(10)"),
        ("wifi_qr_path", "VARCHAR(300)"),
        ("hub_qr_path", "VARCHAR(300)"),
        ("menu_enabled", "BOOLEAN DEFAULT 1"),
        ("offers_enabled", "BOOLEAN DEFAULT 1"),
        ("reservations_enabled", "BOOLEAN DEFAULT 0"),
        ("loyalty_enabled", "BOOLEAN DEFAULT 0"),
        ("wifi_enabled", "BOOLEAN DEFAULT 0"),
        ("loyalty_goal", "INTEGER DEFAULT 8"),
        ("loyalty_reward", "VARCHAR(200)"),
        ("staff_pin", "VARCHAR(12)"),
        ("hub_views", "INTEGER DEFAULT 0"),
        ("menu_views", "INTEGER DEFAULT 0"),
        ("rating_views", "INTEGER DEFAULT 0"),
    ]
    added = []
    for col_name, ddl in biz_columns:
        if col_name not in existing:
            with db.engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE businesses ADD COLUMN {col_name} {ddl}")
                )
            added.append(col_name)

    if "access_code" in added:
        for biz in Business.query.filter(
            (Business.access_code.is_(None)) | (Business.access_code == "")
        ).all():
            biz.access_code = generate_access_code()
        db.session.commit()
    if "is_active" in added:
        for biz in Business.query.filter(Business.is_active.is_(None)).all():
            biz.is_active = True
        db.session.commit()

    # عمود تصنيف الملاحظة في جدول التقييمات
    fb_existing = {col["name"] for col in inspector.get_columns("feedbacks")}
    if "category" not in fb_existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE feedbacks ADD COLUMN category VARCHAR(40)"))


def slugify(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^\w\-؀-ۿ]", "", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "business"


def generate_access_code() -> str:
    """يولّد رمز دخول سري من 6 أرقام للوحة التحكم."""
    return f"{secrets.randbelow(1_000_000):06d}"


def ensure_unique_slug(base_slug: str) -> str:
    slug = base_slug
    counter = 2
    while Business.query.filter_by(slug=slug).first() is not None:
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def generate_qr_for_business(business: Business, app: Flask) -> str:
    rating_url = url_for("customer_rating", slug=business.slug, _external=True)
    img = qrcode.make(rating_url)
    filename = f"{business.slug}.png"
    abs_path = os.path.join(QR_DIR, filename)
    img.save(abs_path)
    return f"qrcodes/{filename}"


def generate_hub_qr(business: Business) -> str:
    """ينشئ رمز QR يفتح صفحة النشاط الموحّدة (المنيو + كل شيء)."""
    hub_url = url_for("hub", slug=business.slug, _external=True)
    img = qrcode.make(hub_url)
    filename = f"{business.slug}-hub.png"
    img.save(os.path.join(QR_DIR, filename))
    return f"qrcodes/{filename}"


def generate_wifi_qr(business: Business) -> str:
    """ينشئ رمز QR للاتصال بشبكة الواي فاي تلقائيًا عند المسح."""
    enc = (business.wifi_encryption or "WPA").upper()
    if enc not in ("WPA", "WEP", "NOPASS"):
        enc = "WPA"

    def esc(v):
        v = v or ""
        for ch in ["\\", ";", ",", ":", '"']:
            v = v.replace(ch, "\\" + ch)
        return v

    ssid = esc(business.wifi_ssid)
    pwd = esc(business.wifi_password)
    if enc == "NOPASS":
        payload = f"WIFI:T:nopass;S:{ssid};;"
    else:
        payload = f"WIFI:T:{enc};S:{ssid};P:{pwd};;"
    img = qrcode.make(payload)
    filename = f"{business.slug}-wifi.png"
    img.save(os.path.join(QR_DIR, filename))
    return f"qrcodes/{filename}"


def normalize_phone(raw: str) -> str:
    """يطبّع رقم الجوال السعودي إلى صيغة 9665XXXXXXXX قدر الإمكان."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00966"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "966" + digits[1:]
    elif digits.startswith("5") and len(digits) == 9:
        digits = "966" + digits
    return digits


def save_image(file_storage, filename_base: str, max_size=MAX_LOGO_SIZE):
    """يحفظ صورة (شعار/صنف منيو/صورة منيو) بعد التحقق والتصغير. يعيد المسار النسبي أو None."""
    if file_storage is None or not file_storage.filename:
        return None

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError("صيغة الصورة غير مدعومة. استخدم PNG أو JPG أو WEBP.")

    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, img).convert("RGB")
        else:
            img = img.convert("RGB")
        img.thumbnail(max_size)
    except ValueError:
        raise
    except Exception:
        raise ValueError("تعذّر قراءة الصورة. تأكد أن الملف صورة صحيحة.")

    filename = f"{filename_base}.jpg"
    abs_path = os.path.join(UPLOAD_DIR, filename)
    img.save(abs_path, "JPEG", quality=85)
    return f"uploads/{filename}"


def save_logo(file_storage, slug: str):
    """يحفظ شعار/صورة النشاط بعد التحقق منه وتصغيره. يعيد المسار النسبي أو None."""
    if file_storage is None or not file_storage.filename:
        return None

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError("صيغة الصورة غير مدعومة. استخدم PNG أو JPG أو WEBP.")

    try:
        img = Image.open(file_storage.stream)
        img.verify()  # التحقق أن الملف صورة سليمة
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, img).convert("RGB")
        else:
            img = img.convert("RGB")
        img.thumbnail(MAX_LOGO_SIZE)
    except ValueError:
        raise
    except Exception:
        raise ValueError("تعذّر قراءة الصورة. تأكد أن الملف صورة صحيحة.")

    filename = f"{slug}.jpg"
    abs_path = os.path.join(UPLOAD_DIR, filename)
    img.save(abs_path, "JPEG", quality=85)
    return f"uploads/{filename}"


def owner_required(view):
    """يتحقق من دخول صاحب النشاط ويمرّر كائن النشاط للدالة."""

    @wraps(view)
    def wrapper(slug, *args, **kwargs):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not session.get(f"auth_{slug}"):
            return redirect(url_for("dashboard_login", slug=slug))
        return view(business, *args, **kwargs)

    return wrapper


def register_routes(app: Flask):
    @app.route("/")
    def home():
        # الصفحة الرئيسية تذهب للوحة الإدارة (وهي بدورها تطلب الدخول إن لم تكن مسجّلاً)
        return redirect(url_for("admin_dashboard"))

    @app.route("/pitch")
    def pitch():
        # رقم واتساب للتواصل (يُضبط من إعدادات الخادم SALES_WHATSAPP بصيغة 9665XXXXXXXX)
        whatsapp = (os.getenv("SALES_WHATSAPP") or "").strip()
        return render_template("pitch.html", whatsapp=whatsapp)

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_auth"):
            return redirect(url_for("admin_dashboard"))

        if request.method == "POST":
            code = (request.form.get("admin_code") or "").strip()
            if not ADMIN_CODE:
                flash(
                    "لم يتم ضبط رمز الإدارة بعد. أضف المتغيّر ADMIN_CODE في إعدادات الخادم.",
                    "error",
                )
            elif code == ADMIN_CODE:
                session["admin_auth"] = True
                return redirect(url_for("admin_dashboard"))
            else:
                flash("رمز الإدارة غير صحيح.", "error")

        return render_template("admin_login.html")

    @app.route("/admin/logout")
    def admin_logout():
        session.pop("admin_auth", None)
        flash("تم تسجيل الخروج من لوحة الإدارة.", "success")
        return redirect(url_for("admin_login"))

    @app.route("/admin")
    @app.route("/admin/dashboard")
    def admin_dashboard():
        if not session.get("admin_auth"):
            return redirect(url_for("admin_login"))

        businesses = Business.query.order_by(Business.created_at.desc()).all()
        rows = []
        for b in businesses:
            total = Feedback.query.filter_by(business_id=b.id).count()
            avg = (
                db.session.query(func.avg(Feedback.rating))
                .filter(Feedback.business_id == b.id)
                .scalar()
            )
            google_clicks = Feedback.query.filter_by(
                business_id=b.id, clicked_google=True
            ).count()
            complaints = Feedback.query.filter(
                Feedback.business_id == b.id,
                Feedback.comment.isnot(None),
                Feedback.comment != "",
            ).count()
            rows.append(
                {
                    "business": b,
                    "total": total,
                    "avg": round(float(avg or 0), 2),
                    "google_clicks": google_clicks,
                    "complaints": complaints,
                }
            )

        totals = {
            "businesses": len(businesses),
            "active": sum(1 for b in businesses if b.is_active),
            "paused": sum(1 for b in businesses if not b.is_active),
            "feedback": sum(r["total"] for r in rows),
        }

        return render_template("admin_dashboard.html", rows=rows, totals=totals)

    @app.route("/admin/business/<slug>/toggle", methods=["POST"])
    def admin_toggle_business(slug):
        if not session.get("admin_auth"):
            return redirect(url_for("admin_login"))
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        business.is_active = not business.is_active
        db.session.commit()
        state = "تشغيل" if business.is_active else "إيقاف"
        flash(f'تم {state} خدمة "{business.name}".', "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/business/<slug>/delete", methods=["POST"])
    def admin_delete_business(slug):
        if not session.get("admin_auth"):
            return redirect(url_for("admin_login"))
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        # تأكيد إضافي: لازم يكتب اسم النشاط بالضبط
        confirm = (request.form.get("confirm_name") or "").strip()
        if confirm != business.name:
            flash("لم يتم الحذف: تأكد من كتابة اسم النشاط بشكل صحيح للتأكيد.", "error")
            return redirect(url_for("admin_dashboard"))

        name = business.name
        # حذف ملفات الشعار والـ QR من القرص
        for rel in (business.logo_path, business.qr_code_path):
            if rel:
                try:
                    os.remove(os.path.join(DATA_DIR, rel))
                except OSError:
                    pass
        db.session.delete(business)  # يحذف التقييمات المرتبطة تلقائيًا (cascade)
        db.session.commit()
        flash(f'تم حذف "{name}" وكل بياناته نهائيًا.', "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/add", methods=["GET", "POST"])
    def admin_add_business():
        if not session.get("admin_auth"):
            return redirect(url_for("admin_login"))

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            category = (request.form.get("category") or "").strip()
            google_review_url = (request.form.get("google_review_url") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            custom_slug = (request.form.get("slug") or "").strip()

            logo_file = request.files.get("logo")

            errors = []
            if not name:
                errors.append("اسم النشاط مطلوب.")
            if not category:
                errors.append("نوع النشاط مطلوب.")
            if not is_valid_url(google_review_url):
                errors.append("رابط تقييم Google غير صالح.")

            if errors:
                for e in errors:
                    flash(e, "error")
                return render_template(
                    "admin_add_business.html",
                    form={
                        "name": name,
                        "category": category,
                        "google_review_url": google_review_url,
                        "phone": phone,
                        "email": email,
                        "slug": custom_slug,
                    },
                )

            base_slug = slugify(custom_slug or name)
            slug = ensure_unique_slug(base_slug)

            logo_path = None
            try:
                logo_path = save_logo(logo_file, slug)
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template(
                    "admin_add_business.html",
                    form={
                        "name": name,
                        "category": category,
                        "google_review_url": google_review_url,
                        "phone": phone,
                        "email": email,
                        "slug": custom_slug,
                    },
                )

            business = Business(
                name=name,
                slug=slug,
                category=category,
                google_review_url=google_review_url,
                phone=phone or None,
                email=email or None,
                logo_path=logo_path,
                access_code=generate_access_code(),
                brand_color="#16A34A",
                loyalty_goal=8,
                loyalty_reward="مشروب مجاني",
                staff_pin=f"{secrets.randbelow(10000):04d}",
            )
            db.session.add(business)
            db.session.commit()

            try:
                business.qr_code_path = generate_qr_for_business(business, app)
                business.hub_qr_path = generate_hub_qr(business)
                db.session.commit()
            except Exception as exc:
                flash(f"تعذّر إنشاء رمز QR: {exc}", "error")

            # تسجيل دخول صاحب النشاط تلقائيًا للوحة التحكم بعد الإضافة
            session[f"auth_{business.slug}"] = True
            flash(
                f"تم إضافة النشاط بنجاح. رمز الدخول السري للوحة التحكم هو: "
                f"{business.access_code} — احفظه في مكان آمن.",
                "success",
            )
            return redirect(url_for("dashboard", slug=business.slug))

        return render_template("admin_add_business.html", form={})

    @app.route("/r/<slug>", methods=["GET", "POST"])
    def customer_rating(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)

        # إذا كانت الخدمة موقوفة من الإدارة، لا تستقبل تقييمات
        if not business.is_active:
            return render_template("paused.html", business=business)

        if request.method == "POST":
            raw_rating = request.form.get("rating")
            try:
                rating = int(raw_rating)
            except (TypeError, ValueError):
                rating = 0

            if rating < 1 or rating > 5:
                flash("الرجاء اختيار تقييم من 1 إلى 5 نجوم.", "error")
                return render_template("customer_rating.html", business=business)

            feedback = Feedback(
                business_id=business.id,
                rating=rating,
                clicked_google=False,
            )
            db.session.add(feedback)
            db.session.commit()

            if rating >= 4:
                return redirect(url_for("happy", feedback_id=feedback.id))
            return redirect(url_for("complaint", feedback_id=feedback.id))

        business.rating_views = (business.rating_views or 0) + 1
        db.session.commit()
        return render_template("customer_rating.html", business=business)

    @app.route("/happy/<int:feedback_id>")
    def happy(feedback_id):
        feedback = Feedback.query.get(feedback_id)
        if feedback is None:
            abort(404)
        return render_template("happy.html", feedback=feedback, business=feedback.business)

    @app.route("/go-google/<int:feedback_id>")
    def go_google(feedback_id):
        feedback = Feedback.query.get(feedback_id)
        if feedback is None:
            abort(404)
        feedback.clicked_google = True
        db.session.commit()
        return redirect(feedback.business.google_review_url)

    @app.route("/complaint/<int:feedback_id>", methods=["GET", "POST"])
    def complaint(feedback_id):
        feedback = Feedback.query.get(feedback_id)
        if feedback is None:
            abort(404)

        if request.method == "POST":
            comment = (request.form.get("comment") or "").strip()
            customer_name = (request.form.get("customer_name") or "").strip()
            customer_phone = (request.form.get("customer_phone") or "").strip()
            category = (request.form.get("category") or "").strip()

            if not comment:
                flash("الرجاء كتابة ملاحظتك قبل الإرسال.", "error")
                return render_template(
                    "complaint.html",
                    feedback=feedback,
                    business=feedback.business,
                )

            feedback.comment = comment
            feedback.customer_name = customer_name or None
            feedback.customer_phone = customer_phone or None
            feedback.category = category or None
            db.session.commit()
            return redirect(url_for("thank_you"))

        return render_template(
            "complaint.html", feedback=feedback, business=feedback.business
        )

    @app.route("/thank-you")
    def thank_you():
        return render_template("thank_you.html")

    @app.route("/dashboard/<slug>/login", methods=["GET", "POST"])
    def dashboard_login(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)

        # إن لم يكن للنشاط رمز (بيانات قديمة) ولّد واحدًا
        if not business.access_code:
            business.access_code = generate_access_code()
            db.session.commit()

        if session.get(f"auth_{slug}"):
            return redirect(url_for("dashboard", slug=slug))

        if request.method == "POST":
            code = (request.form.get("access_code") or "").strip()
            if code and code == business.access_code:
                session[f"auth_{slug}"] = True
                return redirect(url_for("dashboard", slug=slug))
            flash("الرمز السري غير صحيح.", "error")

        return render_template("dashboard_login.html", business=business)

    @app.route("/dashboard/<slug>/logout")
    def dashboard_logout(slug):
        session.pop(f"auth_{slug}", None)
        flash("تم تسجيل الخروج من لوحة التحكم.", "success")
        return redirect(url_for("dashboard_login", slug=slug))

    @app.route("/dashboard/<slug>")
    def dashboard(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)

        if not session.get(f"auth_{slug}"):
            return redirect(url_for("dashboard_login", slug=slug))

        feedbacks = (
            Feedback.query.filter_by(business_id=business.id)
            .order_by(Feedback.created_at.desc())
            .all()
        )

        total_feedback = len(feedbacks)
        positive_count = sum(1 for f in feedbacks if f.rating >= 4)
        negative_count = sum(1 for f in feedbacks if f.rating <= 3)
        google_clicks = sum(1 for f in feedbacks if f.clicked_google)
        complaints = [f for f in feedbacks if (f.comment or "").strip()]
        complaints_count = len(complaints)

        if total_feedback > 0:
            avg = (
                db.session.query(func.avg(Feedback.rating))
                .filter(Feedback.business_id == business.id)
                .scalar()
            )
            average_rating = round(float(avg or 0), 2)
            satisfaction_rate = round(positive_count / total_feedback * 100, 1)
        else:
            average_rating = 0
            satisfaction_rate = 0

        stats = {
            "total_feedback": total_feedback,
            "average_rating": average_rating,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "google_clicks": google_clicks,
            "complaints_count": complaints_count,
            "satisfaction_rate": satisfaction_rate,
        }

        # --- مخطط آخر 7 أيام ---
        today = datetime.utcnow().date()
        days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        day_labels = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
        chart = []
        for d in days:
            count = sum(1 for f in feedbacks if f.created_at.date() == d)
            # weekday(): الإثنين=0 ... الأحد=6  → نحوّل لترتيب عربي يبدأ بالأحد
            label = day_labels[(d.weekday() + 1) % 7]
            chart.append({"label": label, "date": d.strftime("%m-%d"), "count": count})
        chart_max = max([c["count"] for c in chart] + [1])

        # --- تصنيف الملاحظات ---
        cat_names = {
            "taste": "الطعم/الجودة",
            "service": "الخدمة",
            "cleanliness": "النظافة",
            "price": "الأسعار",
            "speed": "السرعة",
            "other": "أخرى",
        }
        category_breakdown = []
        for key, label in cat_names.items():
            c = sum(1 for f in feedbacks if (f.category or "") == key)
            if c:
                category_breakdown.append({"label": label, "count": c})
        category_breakdown.sort(key=lambda x: x["count"], reverse=True)

        # --- عدّادات إضافية ---
        new_reservations = Reservation.query.filter_by(
            business_id=business.id, status="new"
        ).count()
        loyalty_members_count = LoyaltyMember.query.filter_by(
            business_id=business.id
        ).count()
        menu_items_count = MenuItem.query.filter_by(business_id=business.id).count()
        active_offers = Offer.query.filter_by(
            business_id=business.id, is_active=True
        ).count()

        stats.update(
            {
                "hub_views": business.hub_views or 0,
                "menu_views": business.menu_views or 0,
                "rating_views": business.rating_views or 0,
                "new_reservations": new_reservations,
                "loyalty_members": loyalty_members_count,
                "menu_items": menu_items_count,
                "active_offers": active_offers,
            }
        )

        rating_url = url_for("customer_rating", slug=business.slug, _external=True)
        hub_url = url_for("hub", slug=business.slug, _external=True)

        return render_template(
            "dashboard.html",
            business=business,
            stats=stats,
            complaints=complaints[:20],
            rating_url=rating_url,
            hub_url=hub_url,
            chart=chart,
            chart_max=chart_max,
            category_breakdown=category_breakdown,
        )

    @app.route("/qr-card/<slug>")
    def qr_card(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not session.get(f"auth_{slug}"):
            return redirect(url_for("dashboard_login", slug=slug))

        kind = request.args.get("type", "rating")
        if kind == "hub":
            if not business.hub_qr_path:
                business.hub_qr_path = generate_hub_qr(business)
                db.session.commit()
            qr_path = business.hub_qr_path
            headline = "كل شي عننا في مكان واحد"
            subline = "امسح الرمز: المنيو، العروض، التقييم، والموقع"
        else:
            qr_path = business.qr_code_path
            headline = "كيف كانت تجربتك معنا؟"
            subline = "امسح الرمز وشاركنا رأيك — يأخذ ثوانٍ فقط"

        return render_template(
            "qr_card.html",
            business=business,
            qr_path=qr_path,
            headline=headline,
            subline=subline,
            kind=kind,
        )

    @app.route("/media/<path:filename>")
    def media(filename):
        # يخدم الصور والـ QR من مجلد البيانات (القرص الدائم على Render)
        return send_from_directory(DATA_DIR, filename)

    @app.route("/qr-download/<slug>")
    def qr_download(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None or not business.qr_code_path:
            abort(404)
        filename = os.path.basename(business.qr_code_path)
        return send_from_directory(QR_DIR, filename, as_attachment=True)

    # ==================================================================
    #  الصفحات العامة (يراها العميل) — صفحة النشاط الموحّدة وملحقاتها
    # ==================================================================

    @app.route("/b/<slug>")
    def hub(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not business.is_active:
            return render_template("paused.html", business=business)

        business.hub_views = (business.hub_views or 0) + 1
        db.session.commit()

        offers = (
            Offer.query.filter_by(business_id=business.id, is_active=True)
            .order_by(Offer.created_at.desc())
            .all()
        )
        item_count = MenuItem.query.filter_by(
            business_id=business.id, is_available=True
        ).count()
        image_count = MenuImage.query.filter_by(business_id=business.id).count()
        return render_template(
            "hub.html",
            business=business,
            offers=offers,
            item_count=item_count,
            has_menu=(item_count > 0 or image_count > 0),
        )

    @app.route("/b/<slug>/menu")
    def public_menu(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not business.is_active:
            return render_template("paused.html", business=business)

        business.menu_views = (business.menu_views or 0) + 1
        db.session.commit()

        categories = (
            MenuCategory.query.filter_by(business_id=business.id)
            .order_by(MenuCategory.sort_order, MenuCategory.id)
            .all()
        )
        uncategorized = (
            MenuItem.query.filter_by(business_id=business.id, category_id=None)
            .order_by(MenuItem.sort_order, MenuItem.id)
            .all()
        )
        menu_images = (
            MenuImage.query.filter_by(business_id=business.id)
            .order_by(MenuImage.sort_order, MenuImage.id)
            .all()
        )
        return render_template(
            "menu_public.html",
            business=business,
            categories=categories,
            uncategorized=uncategorized,
            menu_images=menu_images,
        )

    @app.route("/b/<slug>/reserve", methods=["GET", "POST"])
    def public_reserve(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not business.is_active or not business.reservations_enabled:
            return redirect(url_for("hub", slug=slug))

        if request.method == "POST":
            name = (request.form.get("customer_name") or "").strip()
            phone = (request.form.get("customer_phone") or "").strip()
            party = request.form.get("party_size")
            req_time = (request.form.get("requested_time") or "").strip()
            note = (request.form.get("note") or "").strip()

            if not name or not phone:
                flash("الاسم ورقم الجوال مطلوبان.", "error")
                return render_template("reserve.html", business=business)

            try:
                party_size = int(party) if party else None
            except (TypeError, ValueError):
                party_size = None

            res = Reservation(
                business_id=business.id,
                customer_name=name,
                customer_phone=phone,
                party_size=party_size,
                requested_time=req_time or None,
                note=note or None,
                status="new",
            )
            db.session.add(res)
            db.session.commit()
            return render_template("reserve_done.html", business=business)

        return render_template("reserve.html", business=business)

    @app.route("/b/<slug>/loyalty", methods=["GET", "POST"])
    def public_loyalty(slug):
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not business.is_active or not business.loyalty_enabled:
            return redirect(url_for("hub", slug=slug))

        member = None
        searched = False
        if request.method == "POST":
            searched = True
            phone = normalize_phone(request.form.get("phone") or "")
            if phone:
                member = LoyaltyMember.query.filter_by(
                    business_id=business.id, phone=phone
                ).first()

        return render_template(
            "loyalty_public.html",
            business=business,
            member=member,
            searched=searched,
        )

    @app.route("/b/<slug>/staff", methods=["GET", "POST"])
    def staff_station(slug):
        """محطة الموظف لإضافة ختم — محميّة برمز الموظف (PIN)."""
        business = Business.query.filter_by(slug=slug).first()
        if business is None:
            abort(404)
        if not business.loyalty_enabled:
            return redirect(url_for("hub", slug=slug))

        result = None
        member = None
        if request.method == "POST":
            pin = (request.form.get("staff_pin") or "").strip()
            if not business.staff_pin or pin != business.staff_pin:
                flash("رمز الموظف غير صحيح.", "error")
                return render_template("staff_station.html", business=business)

            action = request.form.get("action") or "stamp"
            phone = normalize_phone(request.form.get("phone") or "")
            name = (request.form.get("name") or "").strip()
            if not phone:
                flash("أدخل رقم جوال العميل.", "error")
                return render_template("staff_station.html", business=business)

            member = LoyaltyMember.query.filter_by(
                business_id=business.id, phone=phone
            ).first()
            if member is None:
                member = LoyaltyMember(
                    business_id=business.id, phone=phone, name=name or None, stamps=0
                )
                db.session.add(member)
                db.session.flush()

            goal = business.loyalty_goal or 8
            if action == "redeem":
                if member.stamps >= goal:
                    member.stamps -= goal
                    member.rewards_redeemed = (member.rewards_redeemed or 0) + 1
                    result = "redeemed"
                else:
                    result = "not_enough"
            else:  # stamp
                member.stamps = (member.stamps or 0) + 1
                member.last_stamp_at = datetime.utcnow()
                if name and not member.name:
                    member.name = name
                result = "ready" if member.stamps >= goal else "stamped"
            db.session.commit()

        return render_template(
            "staff_station.html", business=business, result=result, member=member
        )

    # ==================================================================
    #  لوحة التحكم — إدارة المنيو
    # ==================================================================

    @app.route("/dashboard/<slug>/menu")
    @owner_required
    def manage_menu(business):
        categories = (
            MenuCategory.query.filter_by(business_id=business.id)
            .order_by(MenuCategory.sort_order, MenuCategory.id)
            .all()
        )
        uncategorized = (
            MenuItem.query.filter_by(business_id=business.id, category_id=None)
            .order_by(MenuItem.sort_order, MenuItem.id)
            .all()
        )
        menu_images = (
            MenuImage.query.filter_by(business_id=business.id)
            .order_by(MenuImage.sort_order, MenuImage.id)
            .all()
        )
        return render_template(
            "manage_menu.html",
            business=business,
            categories=categories,
            uncategorized=uncategorized,
            menu_images=menu_images,
        )

    @app.route("/dashboard/<slug>/menu/image/add", methods=["POST"])
    @owner_required
    def add_menu_image(business):
        files = request.files.getlist("images") or []
        added = 0
        for f in files:
            if not f or not f.filename:
                continue
            try:
                path = save_image(
                    f, f"menu-{secrets.token_hex(6)}", max_size=MAX_MENU_IMAGE_SIZE
                )
            except ValueError as exc:
                flash(str(exc), "error")
                continue
            if path:
                db.session.add(MenuImage(business_id=business.id, image_path=path))
                added += 1
        if added:
            db.session.commit()
            flash(f"تم رفع {added} صورة منيو.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/image/<int:img_id>/delete", methods=["POST"])
    @owner_required
    def delete_menu_image(business, img_id):
        mi = MenuImage.query.filter_by(id=img_id, business_id=business.id).first()
        if mi:
            if mi.image_path:
                try:
                    os.remove(os.path.join(DATA_DIR, mi.image_path))
                except OSError:
                    pass
            db.session.delete(mi)
            db.session.commit()
            flash("تم حذف صورة المنيو.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/category/add", methods=["POST"])
    @owner_required
    def add_category(business):
        name = (request.form.get("name") or "").strip()
        if name:
            cat = MenuCategory(business_id=business.id, name=name)
            db.session.add(cat)
            db.session.commit()
            flash("تمت إضافة القسم.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/category/<int:cid>/delete", methods=["POST"])
    @owner_required
    def delete_category(business, cid):
        cat = MenuCategory.query.filter_by(id=cid, business_id=business.id).first()
        if cat:
            db.session.delete(cat)
            db.session.commit()
            flash("تم حذف القسم وأصنافه.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/item/add", methods=["POST"])
    @owner_required
    def add_item(business):
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("اسم الصنف مطلوب.", "error")
            return redirect(url_for("manage_menu", slug=business.slug))

        description = (request.form.get("description") or "").strip()
        price_raw = (request.form.get("price") or "").strip()
        cat_raw = request.form.get("category_id") or ""
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            price = None
        try:
            category_id = int(cat_raw) if cat_raw else None
        except ValueError:
            category_id = None

        item = MenuItem(
            business_id=business.id,
            category_id=category_id,
            name=name,
            description=description or None,
            price=price,
        )
        try:
            img = request.files.get("image")
            item.image_path = save_image(img, f"item-{secrets.token_hex(6)}")
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("manage_menu", slug=business.slug))

        db.session.add(item)
        db.session.commit()
        flash("تمت إضافة الصنف.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/item/<int:iid>/edit", methods=["POST"])
    @owner_required
    def edit_item(business, iid):
        item = MenuItem.query.filter_by(id=iid, business_id=business.id).first()
        if not item:
            abort(404)
        item.name = (request.form.get("name") or item.name).strip()
        item.description = (request.form.get("description") or "").strip() or None
        price_raw = (request.form.get("price") or "").strip()
        try:
            item.price = float(price_raw) if price_raw else None
        except ValueError:
            pass
        cat_raw = request.form.get("category_id") or ""
        try:
            item.category_id = int(cat_raw) if cat_raw else None
        except ValueError:
            item.category_id = None
        try:
            img = request.files.get("image")
            new_path = save_image(img, f"item-{secrets.token_hex(6)}")
            if new_path:
                item.image_path = new_path
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("manage_menu", slug=business.slug))
        db.session.commit()
        flash("تم تحديث الصنف.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/item/<int:iid>/toggle", methods=["POST"])
    @owner_required
    def toggle_item(business, iid):
        item = MenuItem.query.filter_by(id=iid, business_id=business.id).first()
        if item:
            item.is_available = not item.is_available
            db.session.commit()
        return redirect(url_for("manage_menu", slug=business.slug))

    @app.route("/dashboard/<slug>/menu/item/<int:iid>/delete", methods=["POST"])
    @owner_required
    def delete_item(business, iid):
        item = MenuItem.query.filter_by(id=iid, business_id=business.id).first()
        if item:
            if item.image_path:
                try:
                    os.remove(os.path.join(DATA_DIR, item.image_path))
                except OSError:
                    pass
            db.session.delete(item)
            db.session.commit()
            flash("تم حذف الصنف.", "success")
        return redirect(url_for("manage_menu", slug=business.slug))

    # ==================================================================
    #  لوحة التحكم — العروض
    # ==================================================================

    @app.route("/dashboard/<slug>/offers")
    @owner_required
    def manage_offers(business):
        offers = (
            Offer.query.filter_by(business_id=business.id)
            .order_by(Offer.created_at.desc())
            .all()
        )
        return render_template("manage_offers.html", business=business, offers=offers)

    @app.route("/dashboard/<slug>/offers/add", methods=["POST"])
    @owner_required
    def add_offer(business):
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("عنوان العرض مطلوب.", "error")
            return redirect(url_for("manage_offers", slug=business.slug))
        offer = Offer(
            business_id=business.id,
            title=title,
            description=(request.form.get("description") or "").strip() or None,
            emoji=(request.form.get("emoji") or "🎁").strip()[:8] or "🎁",
            is_active=True,
        )
        db.session.add(offer)
        db.session.commit()
        flash("تمت إضافة العرض.", "success")
        return redirect(url_for("manage_offers", slug=business.slug))

    @app.route("/dashboard/<slug>/offers/<int:oid>/toggle", methods=["POST"])
    @owner_required
    def toggle_offer(business, oid):
        offer = Offer.query.filter_by(id=oid, business_id=business.id).first()
        if offer:
            offer.is_active = not offer.is_active
            db.session.commit()
        return redirect(url_for("manage_offers", slug=business.slug))

    @app.route("/dashboard/<slug>/offers/<int:oid>/delete", methods=["POST"])
    @owner_required
    def delete_offer(business, oid):
        offer = Offer.query.filter_by(id=oid, business_id=business.id).first()
        if offer:
            db.session.delete(offer)
            db.session.commit()
            flash("تم حذف العرض.", "success")
        return redirect(url_for("manage_offers", slug=business.slug))

    # ==================================================================
    #  لوحة التحكم — الحجوزات
    # ==================================================================

    @app.route("/dashboard/<slug>/reservations")
    @owner_required
    def manage_reservations(business):
        reservations = (
            Reservation.query.filter_by(business_id=business.id)
            .order_by(Reservation.created_at.desc())
            .all()
        )
        return render_template(
            "manage_reservations.html", business=business, reservations=reservations
        )

    @app.route("/dashboard/<slug>/reservations/<int:rid>/status", methods=["POST"])
    @owner_required
    def update_reservation(business, rid):
        res = Reservation.query.filter_by(id=rid, business_id=business.id).first()
        if res:
            new_status = request.form.get("status") or res.status
            if new_status in ("new", "confirmed", "done", "cancelled"):
                res.status = new_status
                db.session.commit()
        return redirect(url_for("manage_reservations", slug=business.slug))

    # ==================================================================
    #  لوحة التحكم — بطاقة الولاء
    # ==================================================================

    @app.route("/dashboard/<slug>/loyalty", methods=["GET", "POST"])
    @owner_required
    def manage_loyalty(business):
        if request.method == "POST":
            goal_raw = (request.form.get("loyalty_goal") or "").strip()
            try:
                goal = int(goal_raw)
                if 1 <= goal <= 50:
                    business.loyalty_goal = goal
            except ValueError:
                pass
            business.loyalty_reward = (
                request.form.get("loyalty_reward") or ""
            ).strip() or business.loyalty_reward
            pin = re.sub(r"\D", "", request.form.get("staff_pin") or "")
            if pin:
                business.staff_pin = pin[:8]
            db.session.commit()
            flash("تم حفظ إعدادات بطاقة الولاء.", "success")
            return redirect(url_for("manage_loyalty", slug=business.slug))

        members = (
            LoyaltyMember.query.filter_by(business_id=business.id)
            .order_by(LoyaltyMember.stamps.desc(), LoyaltyMember.id.desc())
            .limit(100)
            .all()
        )
        staff_url = url_for("staff_station", slug=business.slug, _external=True)
        return render_template(
            "manage_loyalty.html",
            business=business,
            members=members,
            staff_url=staff_url,
        )

    # ==================================================================
    #  لوحة التحكم — الإعدادات والتخصيص
    # ==================================================================

    @app.route("/dashboard/<slug>/settings", methods=["GET", "POST"])
    @owner_required
    def settings(business):
        if request.method == "POST":
            # --- المعلومات الأساسية ---
            name = (request.form.get("name") or "").strip()
            if name:
                business.name = name
            category = (request.form.get("category") or "").strip()
            if category:
                business.category = category
            business.phone = (request.form.get("phone") or "").strip() or None
            business.email = (request.form.get("email") or "").strip() or None
            gurl = (request.form.get("google_review_url") or "").strip()
            if gurl:
                business.google_review_url = gurl
            try:
                logo = request.files.get("logo")
                new_logo = save_image(logo, f"{business.slug}-logo-{secrets.token_hex(4)}")
                if new_logo:
                    business.logo_path = new_logo
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("settings", slug=business.slug))

            color = (request.form.get("brand_color") or "").strip()
            if re.match(r"^#[0-9A-Fa-f]{6}$", color):
                business.brand_color = color
            business.about = (request.form.get("about") or "").strip() or None
            business.welcome_message = (
                request.form.get("welcome_message") or ""
            ).strip() or None
            business.happy_message = (
                request.form.get("happy_message") or ""
            ).strip() or None
            business.whatsapp = normalize_phone(request.form.get("whatsapp") or "") or None
            business.location_url = (
                request.form.get("location_url") or ""
            ).strip() or None
            business.instagram_url = (
                request.form.get("instagram_url") or ""
            ).strip() or None
            business.snapchat_url = (
                request.form.get("snapchat_url") or ""
            ).strip() or None
            business.tiktok_url = (request.form.get("tiktok_url") or "").strip() or None
            business.x_url = (request.form.get("x_url") or "").strip() or None

            # واي فاي
            business.wifi_ssid = (request.form.get("wifi_ssid") or "").strip() or None
            business.wifi_password = (
                request.form.get("wifi_password") or ""
            ).strip() or None
            enc = (request.form.get("wifi_encryption") or "WPA").strip().upper()
            business.wifi_encryption = enc if enc in ("WPA", "WEP", "NOPASS") else "WPA"

            # مفاتيح التشغيل
            business.menu_enabled = bool(request.form.get("menu_enabled"))
            business.offers_enabled = bool(request.form.get("offers_enabled"))
            business.reservations_enabled = bool(
                request.form.get("reservations_enabled")
            )
            business.loyalty_enabled = bool(request.form.get("loyalty_enabled"))
            business.wifi_enabled = bool(request.form.get("wifi_enabled"))

            # توليد رمز واي فاي إن كان مفعّلًا وفيه اسم شبكة
            if business.wifi_enabled and business.wifi_ssid:
                try:
                    business.wifi_qr_path = generate_wifi_qr(business)
                except Exception:
                    pass

            # تأكد من وجود رمز الهَب
            if not business.hub_qr_path:
                try:
                    business.hub_qr_path = generate_hub_qr(business)
                except Exception:
                    pass

            db.session.commit()
            flash("تم حفظ الإعدادات.", "success")
            return redirect(url_for("settings", slug=business.slug))

        return render_template("settings.html", business=business)

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("not_found.html"), 404


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
