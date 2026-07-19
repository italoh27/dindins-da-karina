from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from urllib.parse import quote
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from openpyxl import Workbook
from psycopg.rows import dict_row
from functools import lru_cache
import json
import os
import tempfile
import requests
import psycopg
from psycopg_pool import ConnectionPool

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_SABORES = os.path.join(BASE_DIR, "sabores.json")
ARQUIVO_PEDIDOS = os.path.join(BASE_DIR, "pedidos.json")
ARQUIVO_CONFIG = os.path.join(BASE_DIR, "config_loja.json")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SENHA_ADMIN = os.environ.get("ADMIN_PASSWORD", "Zaraba@27")

NUMERO_ITALO = os.environ.get("NUMERO_ITALO", "5581999616265")
NUMERO_KARINA = os.environ.get("NUMERO_KARINA", "5585981998730")

CHAVE_PIX = os.environ.get("CHAVE_PIX", "italo-henrique-27@jim.com")
NOME_PIX = os.environ.get("NOME_PIX", "Italo Henrique de Oliveira Farias")
BANCO_PIX = os.environ.get("BANCO_PIX", "InfinitePay")
INFINITEPAY_HANDLE = os.environ.get("INFINITEPAY_HANDLE", "italo-henrique-27").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
MIGRATION_MARKER = os.path.join(BASE_DIR, ".json_to_db_migrated")

APP_TIMEZONE = ZoneInfo("America/Recife")
DIAS_SEMANA_PT = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]

# --- CONEXÃO COM POOL ---
db_pool = None

def get_conn():
    global db_pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada.")
    if db_pool is None:
        db_pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row})
    return db_pool.connection()

# --- HELPERS JSON/CONFIG ---
def salvar_json(caminho, dados):
    pasta = os.path.dirname(os.path.abspath(caminho)) or "."
    os.makedirs(pasta, exist_ok=True)
    fd, arquivo_temp = tempfile.mkstemp(prefix="tmp_", suffix=".json", dir=pasta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(arquivo_temp, caminho)
    finally:
        if os.path.exists(arquivo_temp):
            try: os.remove(arquivo_temp)
            except OSError: pass

def ler_json(caminho, valor_padrao):
    if not os.path.exists(caminho): salvar_json(caminho, valor_padrao)
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return dados if isinstance(dados, type(valor_padrao)) else valor_padrao
    except Exception: return valor_padrao

def configuracao_padrao():
    return {"loja_aberta": True, "mensagem_loja_fechada": "Pausado.", "infinitepay_ativo": True, "bloquear_italo": False, "bloquear_karina": False}

def ler_config_arquivo():
    config = ler_json(ARQUIVO_CONFIG, configuracao_padrao())
    base = configuracao_padrao()
    base.update(config)
    return base

def now_local(): return datetime.now(APP_TIMEZONE)

def db_enabled(): return bool(DATABASE_URL)

def get_config_value(key, default):
    if not db_enabled(): return default
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

# --- CACHE E LÓGICA DE SABORES ---

@lru_cache(maxsize=1)
def _ler_sabores_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sabores ORDER BY id")
            return [row_to_sabor(row) for row in cur.fetchall()]

def ler_sabores():
    if not db_enabled():
        sabores = ler_json(ARQUIVO_SABORES, [])
        return sabores
    return _ler_sabores_db()

def limpar_cache_sabores():
    _ler_sabores_db.cache_clear()

def row_to_sabor(row):
    estoque_base = int(row.get("estoque", 0))
    e_italo = int(row.get("estoque_italo", 0))
    e_karina = int(row.get("estoque_karina", 0))
    return {
        "id": int(row["id"]), "nome": str(row["nome"]), "preco": float(row["preco"]),
        "img": row["img"], "disponivel": bool(row["disponivel"]),
        "estoque_italo": e_italo, "estoque_karina": e_karina,
        "ativo_italo": bool(row.get("ativo_italo", True)), "ativo_karina": bool(row.get("ativo_karina", True))
    }

# --- RESTO DO CÓDIGO MANTIDO IGUAL ---
def normalizar_destinatario(destinatario): return "karina" if str(destinatario).strip().lower() == "karina" else "italo"
def estoque_campo_destinatario(destinatario): return "estoque_karina" if normalizar_destinatario(destinatario) == "karina" else "estoque_italo"
def ativo_campo_destinatario(destinatario): return "ativo_karina" if normalizar_destinatario(destinatario) == "karina" else "ativo_italo"
def sabor_ativo_para_destinatario(sabor, destinatario): return bool(sabor.get("disponivel", True)) and bool(sabor.get(ativo_campo_destinatario(destinatario), True))
def estoque_para_destinatario(sabor, destinatario): return max(0, int(sabor.get(estoque_campo_destinatario(destinatario), 0)))

def enrich_sabor_destinatario(sabor, destinatario, carrinho=None):
    item = dict(sabor)
    estoque_base = estoque_para_destinatario(item, destinatario)
    reservado = 0
    if carrinho:
        reservado = sum(int(c.get("quantidade", 0)) for c in carrinho if c.get("nome") == item.get("nome"))
    item["estoque_exibicao"] = max(0, estoque_base - reservado)
    return item

@app.route("/")
def home():
    carrinho = session.get("carrinho", [])
    config = ler_config()
    destinatario = normalizar_destinatario(session.get("destinatario_atual", "italo"))
    sabores = [enrich_sabor_destinatario(s, destinatario, carrinho) for s in ler_sabores() if sabor_ativo_para_destinatario(s, destinatario)]
    return render_template("index.html", sabores=sabores, destinatario_atual=destinatario)

# ... (Incluir aqui todas as outras rotas do seu app.py original sem mudanças, apenas usando ler_sabores() e get_conn())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
