from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from main import run_scraper


def get_db_connection() -> sqlite3.Connection:
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


class User(UserMixin):
    def __init__(self, id: int, email: str, password_hash: str):
        self.id = id
        self.email = email
        self.password_hash = password_hash

    @staticmethod
    def get_by_id(user_id: int) -> "User | None":
        conn = get_db_connection()
        row = conn.execute("SELECT id, email, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2])
        return None

    @staticmethod
    def get_by_email(email: str) -> "User | None":
        conn = get_db_connection()
        row = conn.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2])
        return None

    @staticmethod
    def create(email: str, password: str) -> "User":
        password_hash = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return User(new_id, email, password_hash)


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
STORAGE_ROOT = os.environ.get("STORAGE_DIR", "storage")

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.get_by_id(int(user_id))
    except Exception:
        return None


# Inicializa o banco na carga da aplicação (Flask 3 removeu before_first_request)
init_db()


@app.route("/", methods=["GET"]) 
@login_required
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"]) 
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Preencha email e senha.")
            return redirect(url_for("register"))
        if User.get_by_email(email):
            flash("Email já registrado.")
            return redirect(url_for("register"))
        user = User.create(email, password)
        login_user(user)
        return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"]) 
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.get_by_email(email)
        if not user or not check_password_hash(user.password_hash, password):
            flash("Credenciais inválidas.")
            return redirect(url_for("login"))
        login_user(user)
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout") 
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/healthz", methods=["GET"]) 
def healthz():
    return "ok", 200


@app.route("/scrape", methods=["POST"]) 
@login_required
def scrape():
    palavra_chave = request.form.get("palavra_chave", "").strip()
    localizacao = request.form.get("localizacao", "").strip()
    total_raw = request.form.get("total", "").strip() or "100"
    try:
        total = int(total_raw)
    except Exception:
        total = 100
    # Guardrails: clamp total and inputs to keep synchronous job reasonable
    total = max(1, min(total, 500))
    if len(palavra_chave) > 100:
        palavra_chave = palavra_chave[:100]
    if len(localizacao) > 100:
        localizacao = localizacao[:100]

    if not palavra_chave or not localizacao:
        flash("Por favor, preencha 'Palavra-chave' e 'Localização'.")
        return redirect(url_for("index"))

    query = f"{palavra_chave} in {localizacao}"
    user_base_dir = os.path.join(STORAGE_ROOT, str(current_user.id), "GMaps Data")
    results = run_scraper([query], total=total, headless=True, save_base_dir=user_base_dir)

    return render_template("result.html", results=results, query=query)


def _is_path_owned_by_current_user(path: str) -> bool:
    if not current_user.is_authenticated:
        return False
    user_root = os.path.abspath(os.path.join(STORAGE_ROOT, str(current_user.id)))
    abs_path = os.path.abspath(path)
    try:
        return os.path.commonpath([abs_path, user_root]) == user_root
    except Exception:
        return False


@app.route("/download") 
@login_required
def download():
    path = request.args.get("path")
    if not path or not os.path.exists(path):
        flash("Arquivo não encontrado para download.")
        return redirect(url_for("index"))
    if not _is_path_owned_by_current_user(path):
        flash("Acesso negado ao arquivo solicitado.")
        return redirect(url_for("index"))
    filename = os.path.basename(path)
    return send_file(path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    # Em produção, use um servidor WSGI (Gunicorn/Waitress). Traefik no Dokploy fará o proxy.
    app.run(host="0.0.0.0", port=8000, debug=True)