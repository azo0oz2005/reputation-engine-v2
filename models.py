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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    feedbacks = db.relationship(
        "Feedback",
        backref="business",
        lazy=True,
        cascade="all, delete-orphan",
    )

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
    customer_name = db.Column(db.String(160), nullable=True)
    customer_phone = db.Column(db.String(40), nullable=True)
    clicked_google = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Feedback {self.id} rating={self.rating}>"
