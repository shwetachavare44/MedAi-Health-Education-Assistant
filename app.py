import os
import sqlite3
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    flash
)

from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from google import genai


# =========================================================
# CONFIGURATION
# =========================================================

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "development-secret-key-change-this"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY is not configured.")


# Gemini client
client = genai.Client(api_key=GEMINI_API_KEY)


DATABASE = "medical.db"


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            response TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# LOGIN REQUIRED DECORATOR
# =========================================================

def login_required(function):

    @wraps(function)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return decorated_function


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template(
        "index.html",
        user_name=session.get("user_name")
    )


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        if not name or not email or not password:

            flash("Please fill in all fields.", "error")

            return render_template("register.html")

        if password != confirm_password:

            flash("Passwords do not match.", "error")

            return render_template("register.html")

        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return render_template("register.html")

        conn = get_db()

        existing_user = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing_user:

            conn.close()

            flash(
                "An account with this email already exists.",
                "error"
            )

            return render_template("register.html")

        hashed_password = generate_password_hash(password)

        conn.execute(
            """
            INSERT INTO users (name, email, password)
            VALUES (?, ?, ?)
            """,
            (
                name,
                email,
                hashed_password
            )
        )

        conn.commit()
        conn.close()

        flash(
            "Account created successfully. Please log in.",
            "success"
        )

        return redirect(url_for("login"))

    return render_template("register.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session.clear()

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]

            return redirect(url_for("home"))

        flash(
            "Invalid email or password.",
            "error"
        )

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# =========================================================
# GENERATE MEDICAL RESPONSE
# =========================================================

@app.route("/generate", methods=["POST"])
@login_required
def generate():

    data = request.get_json()

    question = data.get(
        "question",
        ""
    ).strip()

    if not question:

        return jsonify({
            "success": False,
            "error": "Please enter a health question."
        }), 400

    if len(question) > 1000:

        return jsonify({
            "success": False,
            "error": "Question is too long."
        }), 400

    prompt = f"""
You are MedAI, an educational health information assistant.

User question:
{question}

Provide a clear, calm and easy-to-understand educational response.

Structure your answer with:

1. Short answer
2. Possible causes or explanations
3. Common symptoms or signs
4. Helpful general steps
5. When to contact a healthcare professional
6. Emergency warning signs, if relevant

Important:
- Do not claim to diagnose the user.
- Do not prescribe medications or give personalized treatment.
- Encourage professional medical care when appropriate.
- Use simple language.
- Do not create unnecessary fear.
- This is educational information only.
"""

    try:

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        answer = response.text

        # Save history
        conn = get_db()

        conn.execute(
            """
            INSERT INTO history
            (user_id, question, response)
            VALUES (?, ?, ?)
            """,
            (
                session["user_id"],
                question,
                answer
            )
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "response": answer
        })

    except Exception as error:

        print("Gemini error:", error)

        return jsonify({
            "success": False,
            "error": "Unable to generate a response right now."
        }), 500


# =========================================================
# HISTORY
# =========================================================

@app.route("/history")
@login_required
def history():

    conn = get_db()

    records = conn.execute(
        """
        SELECT *
        FROM history
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (session["user_id"],)
    ).fetchall()

    conn.close()

    return jsonify([
        {
            "question": record["question"],
            "response": record["response"],
            "created_at": record["created_at"]
        }
        for record in records
    ])


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )