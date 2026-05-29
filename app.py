import os
import re
import unicodedata
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

from models import Business, Feedback, db

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
    if "logo_path" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE businesses ADD COLUMN logo_path VARCHAR(300)"))
    if "access_code" not in existing:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE businesses ADD COLUMN access_code VARCHAR(12)"))
        # توليد رمز للأنشطة القديمة التي ليس لها رمز
        for biz in Business.query.filter(
            (Business.access_code.is_(None)) | (Business.access_code == "")
        ).all():
            biz.access_code = generate_access_code()
        db.session.commit()


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


def register_routes(app: Flask):
    @app.route("/")
    def home():
        return render_template("home.html")

    @app.route("/admin/add", methods=["GET", "POST"])
    def admin_add_business():
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
            )
            db.session.add(business)
            db.session.commit()

            try:
                qr_path = generate_qr_for_business(business, app)
                business.qr_code_path = qr_path
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

        rating_url = url_for("customer_rating", slug=business.slug, _external=True)

        return render_template(
            "dashboard.html",
            business=business,
            stats=stats,
            complaints=complaints[:20],
            rating_url=rating_url,
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

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("not_found.html"), 404


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
