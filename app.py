from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from urllib.parse import quote
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from openpyxl import Workbook
from psycopg.rows import dict_row
import json
import os
import secrets
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


# =========================
# HELPERS DE JSON (migração/config)
# =========================
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
            try:
                os.remove(arquivo_temp)
            except OSError:
                pass


def ler_json(caminho, valor_padrao):
    if not os.path.exists(caminho):
        salvar_json(caminho, valor_padrao)
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
            return dados if isinstance(dados, type(valor_padrao)) else valor_padrao
    except Exception:
        return valor_padrao


def configuracao_padrao():
    return {
        "loja_aberta": True,
        "mensagem_loja_fechada": "No momento os pedidos estão pausados. Tente novamente mais tarde.",
        "infinitepay_ativo": True,
        "bloquear_italo": False,
        "bloquear_karina": False,
    }


def ler_config_arquivo():
    config = ler_json(ARQUIVO_CONFIG, configuracao_padrao())
    base = configuracao_padrao()
    base.update(config)
    return base


# =========================
# HELPERS GERAIS
# =========================
def now_local():
    return datetime.now(APP_TIMEZONE)


def formatar_data_pedido(dt):
    return f"{DIAS_SEMANA_PT[dt.weekday()]}, {dt.strftime('%d/%m/%Y às %H:%M')}"


def parse_data_filtro_admin(valor):
    valor = str(valor or '').strip()
    if not valor:
        return ''
    try:
        return datetime.strptime(valor, '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return valor


def resumo_status_pedidos(pedidos):
    return {
        'todos': len(pedidos),
        'nao_pagos': len([p for p in pedidos if p.get('pagamento_status') == 'aguardando_pagamento']),
        'pagos': len([p for p in pedidos if p.get('pagamento_status') == 'pago']),
        'cancelados': len([p for p in pedidos if p.get('status') == 'cancelado' or p.get('pagamento_status') == 'cancelado']),
        'ocultos': len([p for p in pedidos if p.get('oculto', False)]),
    }


def money(valor):
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def contar_itens_carrinho(carrinho):
    return sum(int(item.get("quantidade", 0) or 0) for item in carrinho)


def admin_logado():
    return session.get("admin_logado", False)


def get_admin_password():
    if db_enabled():
        senha_db = get_config_value("admin_password", None)
        if isinstance(senha_db, str) and senha_db.strip():
            return senha_db.strip()
    config_local = ler_config_arquivo()
    senha_local = str(config_local.get("admin_password", "") or "").strip()
    return senha_local or SENHA_ADMIN


def salvar_admin_password(nova_senha):
    nova_senha = str(nova_senha or "").strip()
    if not nova_senha:
        return False
    if db_enabled():
        set_config_value("admin_password", nova_senha)
    cfg = ler_config_arquivo()
    cfg["admin_password"] = nova_senha
    try:
        salvar_json(ARQUIVO_CONFIG, cfg)
    except Exception:
        pass
    return True


def redirect_admin_back(default='/admin'):
    destino = request.form.get('return_to') or request.args.get('return_to') or request.referrer or default
    destino = str(destino or default)
    if '/admin' not in destino:
        destino = default
    return redirect(destino)


def set_mensagem(chave, texto):
    session[chave] = texto
    session.modified = True


def pop_mensagem(chave):
    return session.pop(chave, None)


def get_nome_vendedor(destinatario):
    return "Karina" if destinatario == "karina" else "Italo"


def get_numero_vendedor(destinatario):
    return NUMERO_KARINA if destinatario == "karina" else NUMERO_ITALO

def pedidos_bloqueados_para(config, destinatario):
    destino = normalizar_destinatario(destinatario)
    return bool(config.get(f"bloquear_{destino}", False))


def mensagem_bloqueio_destinatario(destinatario):
    return f"No momento os pedidos para {get_nome_vendedor(destinatario)} estão pausados no painel administrativo."


def ajustar_destinatario_disponivel(destinatario, config):
    destino = normalizar_destinatario(destinatario)
    if not pedidos_bloqueados_para(config, destino):
        return destino
    alternativo = "karina" if destino == "italo" else "italo"
    if not pedidos_bloqueados_para(config, alternativo):
        return alternativo
    return destino



def obter_base_url():
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return request.url_root.rstrip("/")


def normalizar_destinatario(destinatario):
    return "karina" if str(destinatario).strip().lower() == "karina" else "italo"

def normalizar_telefone_br(telefone):
    numeros = "".join(ch for ch in str(telefone or "") if ch.isdigit())
    if not numeros:
        return ""
    if numeros.startswith("55"):
        return f"+{numeros}"
    return f"+55{numeros}"


def normalizar_imagem_sabor(img):
    caminho = str(img or "").strip()
    if not caminho:
        return "/static/gelinhos.png"
    mapa = {
        "paçoca.jpg": "/static/pacoca.jpg",
        "pa#U00e7oca.jpg": "/static/pacoca.jpg",
        "pacoca.jpeg": "/static/pacoca.jpg",
    }
    lower = caminho.lower()
    for origem, destino in mapa.items():
        if origem.lower() in lower:
            return destino
    return caminho


def status_pagamento_legivel(status):
    mapa = {
        "aguardando_pagamento": "Aguardando pagamento",
        "pago": "Pago",
        "cancelado": "Cancelado",
        "falhou": "Falhou",
        "reembolsado": "Reembolsado",
    }
    return mapa.get(status, str(status).replace("_", " ").capitalize())


def is_ajax_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def enriquecer_pedido(pedido):
    pedido = dict(pedido)
    pedido["oculto"] = bool(pedido.get("oculto", False))
    pedido["ocultado_em"] = pedido.get("ocultado_em", "") or ""
    pedido["pagamento_status_legivel"] = status_pagamento_legivel(str(pedido.get("pagamento_status", "aguardando_pagamento")))
    return pedido


# =========================
# BANCO DE DADOS
# =========================
def db_enabled():
    return bool(DATABASE_URL)


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_database():
    if not db_enabled():
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sabores (
                    id INTEGER PRIMARY KEY,
                    nome VARCHAR(150) NOT NULL UNIQUE,
                    preco NUMERIC(10,2) NOT NULL DEFAULT 0,
                    img TEXT NOT NULL DEFAULT '',
                    disponivel BOOLEAN NOT NULL DEFAULT TRUE,
                    estoque INTEGER NOT NULL DEFAULT 0,
                    estoque_italo INTEGER NOT NULL DEFAULT 0,
                    estoque_karina INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS pedidos (
                    id BIGINT PRIMARY KEY,
                    data TEXT NOT NULL,
                    data_filtro DATE NOT NULL,
                    cliente_nome VARCHAR(150) NOT NULL,
                    cliente_telefone VARCHAR(40) NOT NULL DEFAULT '',
                    cliente_endereco TEXT NOT NULL DEFAULT '',
                    total NUMERIC(10,2) NOT NULL DEFAULT 0,
                    status VARCHAR(40) NOT NULL DEFAULT 'pendente',
                    pagamento_status VARCHAR(40) NOT NULL DEFAULT 'aguardando_pagamento',
                    destinatario VARCHAR(20) NOT NULL DEFAULT 'italo',
                    nome_vendedor VARCHAR(50) NOT NULL DEFAULT 'Italo',
                    pagamento_link TEXT NOT NULL DEFAULT '',
                    receipt_url TEXT NOT NULL DEFAULT '',
                    transaction_nsu TEXT NOT NULL DEFAULT '',
                    invoice_slug TEXT NOT NULL DEFAULT '',
                    capture_method TEXT NOT NULL DEFAULT '',
                    preference_id TEXT NOT NULL DEFAULT '',
                    payment_id TEXT NOT NULL DEFAULT '',
                    payment_method TEXT NOT NULL DEFAULT '',
                    payment_detail TEXT NOT NULL DEFAULT '',
                    estoque_devolvido BOOLEAN NOT NULL DEFAULT FALSE,
                    oculto BOOLEAN NOT NULL DEFAULT FALSE,
                    ocultado_em TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS pedido_itens (
                    id BIGSERIAL PRIMARY KEY,
                    pedido_id BIGINT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
                    nome VARCHAR(150) NOT NULL,
                    quantidade INTEGER NOT NULL DEFAULT 0,
                    preco_unitario NUMERIC(10,2) NOT NULL DEFAULT 0,
                    subtotal NUMERIC(10,2) NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS pagamentos_log (
                    id BIGSERIAL PRIMARY KEY,
                    pedido_id BIGINT REFERENCES pedidos(id) ON DELETE SET NULL,
                    payment_id TEXT NOT NULL DEFAULT '',
                    status VARCHAR(50) NOT NULL DEFAULT '',
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS oculto BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS ocultado_em TIMESTAMP NULL;
                ALTER TABLE sabores ADD COLUMN IF NOT EXISTS estoque_italo INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE sabores ADD COLUMN IF NOT EXISTS estoque_karina INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE sabores ADD COLUMN IF NOT EXISTS ativo_italo BOOLEAN NOT NULL DEFAULT TRUE;
                ALTER TABLE sabores ADD COLUMN IF NOT EXISTS ativo_karina BOOLEAN NOT NULL DEFAULT TRUE;
                UPDATE sabores
                SET estoque_italo = CASE WHEN estoque_italo = 0 AND estoque_karina = 0 AND estoque > 0 THEN estoque ELSE estoque_italo END,
                    ativo_italo = CASE WHEN ativo_italo IS NULL THEN COALESCE(disponivel, TRUE) ELSE ativo_italo END,
                    ativo_karina = CASE WHEN ativo_karina IS NULL THEN COALESCE(disponivel, TRUE) ELSE ativo_karina END;
                """
            )
        conn.commit()


def set_config_value(key, value):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_config (key, value)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )
        conn.commit()


def get_config_value(key, default):
    if not db_enabled():
        return default
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
            row = cur.fetchone()
            return row["value"] if row else default


def ler_config():
    arquivo_base = ler_config_arquivo()
    if not db_enabled():
        return arquivo_base

    base = configuracao_padrao()
    config_db = get_config_value("config_loja", None)

    # Se o banco ainda não tiver a configuração crítica da loja,
    # faz bootstrap automático usando o arquivo local existente.
    if not isinstance(config_db, dict) or not config_db:
        base.update(arquivo_base)
        set_config_value("config_loja", base)
        return base

    base.update(config_db)

    # Mantém o arquivo local espelhado como backup simples,
    # sem depender dele como fonte principal quando há banco.
    try:
        salvar_json(ARQUIVO_CONFIG, base)
    except Exception:
        pass
    return base


def salvar_config(config):
    base = configuracao_padrao()
    base.update(config)
    if db_enabled():
        set_config_value("config_loja", base)
        try:
            salvar_json(ARQUIVO_CONFIG, base)
        except Exception:
            pass
    else:
        salvar_json(ARQUIVO_CONFIG, base)


def estoque_campo_destinatario(destinatario):
    return "estoque_karina" if normalizar_destinatario(destinatario) == "karina" else "estoque_italo"


def inteiro_positivo(valor, padrao=0):
    try:
        if valor is None or valor == "":
            return max(0, int(padrao or 0))
        return max(0, int(valor))
    except (TypeError, ValueError):
        return max(0, int(padrao or 0))


def estoque_para_destinatario(sabor, destinatario):
    campo = estoque_campo_destinatario(destinatario)
    if campo in sabor:
        return max(0, int(sabor.get(campo, 0) or 0))
    return max(0, int(sabor.get("estoque", 0) or 0))


def ativo_campo_destinatario(destinatario):
    return "ativo_karina" if normalizar_destinatario(destinatario) == "karina" else "ativo_italo"


def sabor_ativo_para_destinatario(sabor, destinatario):
    campo = ativo_campo_destinatario(destinatario)
    ativo_destinatario = bool(sabor.get(campo, sabor.get("disponivel", True)))
    return bool(sabor.get("disponivel", True)) and ativo_destinatario


def enrich_sabor_destinatario(sabor, destinatario, carrinho=None):
    item = dict(sabor)
    estoque_base = estoque_para_destinatario(item, destinatario)
    reservado_no_carrinho = 0
    if carrinho:
        reservado_no_carrinho = sum(
            inteiro_positivo(c.get("quantidade", 0))
            for c in carrinho
            if str(c.get("nome", "")).strip().lower() == str(item.get("nome", "")).strip().lower()
        )
    item["estoque_exibicao"] = max(0, estoque_base - reservado_no_carrinho)
    item["ativo_exibicao"] = sabor_ativo_para_destinatario(item, destinatario)
    item["estoque_total"] = max(0, int(item.get("estoque_italo", item.get("estoque", 0)) or 0)) + max(0, int(item.get("estoque_karina", 0) or 0))
    item["quantidade_reservada_carrinho"] = reservado_no_carrinho
    return item


def row_to_sabor(row):
    estoque_base = inteiro_positivo(row.get("estoque", 0))
    estoque_italo_raw = row.get("estoque_italo")
    estoque_karina_raw = row.get("estoque_karina")
    estoque_italo = inteiro_positivo(estoque_italo_raw, estoque_base if estoque_italo_raw is None else 0)
    estoque_karina = inteiro_positivo(estoque_karina_raw, 0)
    estoque_total = estoque_italo + estoque_karina
    return {
        "id": int(row["id"]),
        "nome": str(row["nome"]),
        "preco": float(row["preco"]),
        "img": normalizar_imagem_sabor(row["img"]),
        "disponivel": bool(row["disponivel"]),
        "ativo_italo": bool(row.get("ativo_italo", row.get("disponivel", True))),
        "ativo_karina": bool(row.get("ativo_karina", row.get("disponivel", True))),
        "estoque": estoque_total if estoque_total >= 0 else estoque_base,
        "estoque_italo": estoque_italo,
        "estoque_karina": estoque_karina,
    }


def ler_sabores():
    if not db_enabled():
        sabores = ler_json(ARQUIVO_SABORES, [])
        normalizados = []
        for s in sabores:
            if isinstance(s, dict):
                normalizados.append(
                    {
                        "id": int(s.get("id", 0) or 0),
                        "nome": str(s.get("nome", "")).strip(),
                        "preco": float(s.get("preco", 0) or 0),
                        "img": normalizar_imagem_sabor(s.get("img", "")),
                        "disponivel": bool(s.get("disponivel", True)),
                        "ativo_italo": bool(s.get("ativo_italo", s.get("disponivel", True))),
                        "ativo_karina": bool(s.get("ativo_karina", s.get("disponivel", True))),
                        "estoque": inteiro_positivo(s.get("estoque", 0)),
                        "estoque_italo": inteiro_positivo(s.get("estoque_italo"), inteiro_positivo(s.get("estoque", 0)) if s.get("estoque_italo") is None else 0),
                        "estoque_karina": inteiro_positivo(s.get("estoque_karina", 0)),
                    }
                )
        return normalizados

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sabores ORDER BY id")
            return [row_to_sabor(row) for row in cur.fetchall()]


def buscar_sabor_por_nome(nome_sabor):
    if not db_enabled():
        for sabor in ler_sabores():
            if sabor.get("nome") == nome_sabor:
                return sabor
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sabores WHERE nome = %s", (nome_sabor,))
            row = cur.fetchone()
            return row_to_sabor(row) if row else None


def inserir_sabor(sabor):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sabores (id, nome, preco, img, disponivel, estoque, estoque_italo, estoque_karina, ativo_italo, ativo_karina)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(sabor["id"]),
                    str(sabor["nome"]).strip(),
                    money(sabor.get("preco", 0)),
                    str(sabor.get("img", "")).strip(),
                    bool(sabor.get("disponivel", True)),
                    inteiro_positivo(sabor.get("estoque", 0)),
                    inteiro_positivo(sabor.get("estoque_italo"), inteiro_positivo(sabor.get("estoque", 0)) if sabor.get("estoque_italo") is None else 0),
                    inteiro_positivo(sabor.get("estoque_karina", 0)),
                    bool(sabor.get("ativo_italo", sabor.get("disponivel", True))),
                    bool(sabor.get("ativo_karina", sabor.get("disponivel", True))),
                ),
            )
        conn.commit()


def atualizar_sabor(sabor_id, **campos):
    if not campos:
        return
    set_sql = []
    valores = []
    for chave, valor in campos.items():
        set_sql.append(f"{chave} = %s")
        valores.append(valor)
    set_sql.append("updated_at = NOW()")
    valores.append(sabor_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE sabores SET {', '.join(set_sql)} WHERE id = %s", tuple(valores))
        conn.commit()


def excluir_sabor_db(sabor_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sabores WHERE id = %s", (sabor_id,))
        conn.commit()


def next_sabor_id():
    sabores = ler_sabores()
    return max([s.get("id", 0) for s in sabores], default=0) + 1


def row_to_pedido(row, itens):
    data_bruta = row["data"]
    if isinstance(data_bruta, str):
        try:
            data_obj = datetime.strptime(data_bruta, "%d/%m/%Y %H:%M").replace(tzinfo=APP_TIMEZONE)
            data_formatada = formatar_data_pedido(data_obj)
        except ValueError:
            data_formatada = data_bruta
    else:
        data_formatada = str(data_bruta)

    pedido = {
        "id": int(row["id"]),
        "data": data_formatada,
        "data_filtro": row["data_filtro"].strftime("%Y-%m-%d") if hasattr(row["data_filtro"], "strftime") else str(row["data_filtro"]),
        "cliente": {
            "nome": row["cliente_nome"],
            "telefone": row["cliente_telefone"],
            "endereco": row["cliente_endereco"],
        },
        "itens": itens,
        "total": float(row["total"]),
        "status": row["status"],
        "pagamento_status": row["pagamento_status"],
        "destinatario": row["destinatario"],
        "nome_vendedor": row["nome_vendedor"],
        "pagamento_link": row["pagamento_link"],
        "receipt_url": row["receipt_url"],
        "transaction_nsu": row["transaction_nsu"],
        "invoice_slug": row["invoice_slug"],
        "capture_method": row["capture_method"],
        "preference_id": row["preference_id"],
        "payment_id": row["payment_id"],
        "payment_method": row["payment_method"],
        "payment_detail": row["payment_detail"],
        "estoque_devolvido": bool(row["estoque_devolvido"]),
        "oculto": bool(row.get("oculto", False)),
        "ocultado_em": row["ocultado_em"].strftime("%d/%m/%Y %H:%M") if row.get("ocultado_em") and hasattr(row.get("ocultado_em"), "strftime") else (str(row.get("ocultado_em", "")) if row.get("ocultado_em") else ""),
    }
    return enriquecer_pedido(pedido)


def ler_pedidos():
    if not db_enabled():
        pedidos = ler_json(ARQUIVO_PEDIDOS, [])
        if not isinstance(pedidos, list):
            return []
        return [enriquecer_pedido(p) for p in pedidos if isinstance(p, dict)]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*, 
                       COALESCE(
                         json_agg(
                           json_build_object(
                             'nome', i.nome,
                             'quantidade', i.quantidade,
                             'preco_unitario', i.preco_unitario,
                             'subtotal', i.subtotal
                           ) ORDER BY i.id
                         ) FILTER (WHERE i.id IS NOT NULL),
                         '[]'::json
                       ) AS itens
                FROM pedidos p
                LEFT JOIN pedido_itens i ON i.pedido_id = p.id
                GROUP BY p.id
                ORDER BY p.id DESC
                """
            )
            rows = cur.fetchall()
            pedidos = []
            for row in rows:
                itens = []
                for item in row["itens"]:
                    itens.append(
                        {
                            "nome": item["nome"],
                            "quantidade": int(item["quantidade"]),
                            "preco_unitario": float(item["preco_unitario"]),
                            "subtotal": float(item["subtotal"]),
                        }
                    )
                pedidos.append(row_to_pedido(row, itens))
            return pedidos


def buscar_pedido(pedido_id):
    for pedido in ler_pedidos():
        if int(pedido.get("id")) == int(pedido_id):
            return pedido
    return None


def obter_pedido_db(pedido_id):
    if not db_enabled():
        pedido = buscar_pedido(pedido_id)
        if not pedido:
            return None
        itens = []
        for idx, item in enumerate(pedido.get("itens", []), start=1):
            novo = dict(item)
            novo["id"] = idx
            itens.append(novo)
        pedido = dict(pedido)
        pedido["itens"] = itens
        return pedido

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*,
                       COALESCE(
                         json_agg(
                           json_build_object(
                             'id', i.id,
                             'nome', i.nome,
                             'quantidade', i.quantidade,
                             'preco_unitario', i.preco_unitario,
                             'subtotal', i.subtotal
                           ) ORDER BY i.id
                         ) FILTER (WHERE i.id IS NOT NULL),
                         '[]'::json
                       ) AS itens
                FROM pedidos p
                LEFT JOIN pedido_itens i ON i.pedido_id = p.id
                WHERE p.id = %s
                GROUP BY p.id
                """,
                (int(pedido_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            itens = []
            for item in row["itens"]:
                itens.append({
                    "id": int(item["id"]),
                    "nome": item["nome"],
                    "quantidade": int(item["quantidade"]),
                    "preco_unitario": float(item["preco_unitario"]),
                    "subtotal": float(item["subtotal"]),
                })
            return row_to_pedido(row, itens)


def atualizar_pedido_edicao_db(pedido_id, cliente_nome, cliente_telefone, cliente_endereco, quantidades_por_item, novo_item_nome="", novo_item_quantidade=0):
    pedido_id = int(pedido_id)
    novo_item_nome = (novo_item_nome or "").strip()
    try:
        novo_item_quantidade = max(0, int(novo_item_quantidade or 0))
    except (TypeError, ValueError):
        novo_item_quantidade = 0

    if not db_enabled():
        pedidos = ler_json(ARQUIVO_PEDIDOS, [])
        sabores = ler_sabores()
        pedido_encontrado = None
        for pedido in pedidos:
            if int(pedido.get("id", 0) or 0) == pedido_id:
                pedido_encontrado = pedido
                break
        if not pedido_encontrado:
            raise ValueError("Pedido não encontrado.")

        destinatario = normalizar_destinatario(pedido_encontrado.get("destinatario", "italo"))
        campo = estoque_campo_destinatario(destinatario)
        itens_originais = list(pedido_encontrado.get("itens", []))
        novos_itens = []

        for idx, item in enumerate(itens_originais, start=1):
            chave = str(idx)
            nova_qtd = max(0, int(quantidades_por_item.get(chave, item.get("quantidade", 0)) or 0))
            qtd_antiga = int(item.get("quantidade", 0) or 0)
            delta = nova_qtd - qtd_antiga
            sabor = next((s for s in sabores if s.get("nome") == item.get("nome")), None)

            if delta > 0:
                disponivel = int((sabor or {}).get(campo, 0) or 0)
                if disponivel < delta:
                    raise ValueError(f"Estoque insuficiente para aumentar {item.get('nome')}.")
                sabor[campo] = disponivel - delta
                sabor["estoque"] = int(sabor.get("estoque_italo", 0) or 0) + int(sabor.get("estoque_karina", 0) or 0)
            elif delta < 0 and sabor:
                sabor[campo] = int(sabor.get(campo, 0) or 0) + abs(delta)
                sabor["estoque"] = int(sabor.get("estoque_italo", 0) or 0) + int(sabor.get("estoque_karina", 0) or 0)

            if nova_qtd > 0:
                preco = float(item.get("preco_unitario", 0) or 0)
                novo_item = dict(item)
                novo_item["quantidade"] = nova_qtd
                novo_item["subtotal"] = round(preco * nova_qtd, 2)
                novos_itens.append(novo_item)

        if novo_item_nome and novo_item_quantidade > 0:
            sabor_add = next((s for s in sabores if s.get("nome") == novo_item_nome), None)
            if not sabor_add:
                raise ValueError("Sabor selecionado não encontrado.")
            disponivel_add = int(sabor_add.get(campo, 0) or 0)
            if disponivel_add < novo_item_quantidade:
                raise ValueError(f"Estoque insuficiente para adicionar {novo_item_nome}.")
            sabor_add[campo] = disponivel_add - novo_item_quantidade
            sabor_add["estoque"] = int(sabor_add.get("estoque_italo", 0) or 0) + int(sabor_add.get("estoque_karina", 0) or 0)
            preco_add = float(sabor_add.get("preco", 0) or 0)
            novos_itens.append({
                "id": (max([int(i.get("id", 0) or 0) for i in novos_itens], default=0) + 1),
                "nome": novo_item_nome,
                "quantidade": novo_item_quantidade,
                "preco_unitario": preco_add,
                "subtotal": round(preco_add * novo_item_quantidade, 2),
            })

        pedido_encontrado.setdefault("cliente", {})
        pedido_encontrado["cliente"]["nome"] = (cliente_nome or "").strip() or pedido_encontrado["cliente"].get("nome", "Cliente")
        pedido_encontrado["cliente"]["telefone"] = (cliente_telefone or "").strip()
        pedido_encontrado["cliente"]["endereco"] = (cliente_endereco or "").strip()
        pedido_encontrado["itens"] = novos_itens
        pedido_encontrado["total"] = round(sum(float(i.get("subtotal", 0) or 0) for i in novos_itens), 2)
        if not novos_itens:
            pedido_encontrado["status"] = "cancelado"
            if pedido_encontrado.get("pagamento_status") != "pago":
                pedido_encontrado["pagamento_status"] = "cancelado"
        elif pedido_encontrado.get("status") == "cancelado":
            pedido_encontrado["status"] = "pendente"
            if pedido_encontrado.get("pagamento_status") == "cancelado":
                pedido_encontrado["pagamento_status"] = "aguardando_pagamento"

        salvar_sabores(sabores)
        salvar_json(ARQUIVO_PEDIDOS, pedidos)
        return True

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, destinatario, pagamento_status, status
                FROM pedidos
                WHERE id = %s
                FOR UPDATE
                """,
                (pedido_id,),
            )
            pedido_row = cur.fetchone()
            if not pedido_row:
                raise ValueError("Pedido não encontrado.")

            destinatario = normalizar_destinatario(pedido_row["destinatario"])
            campo = estoque_campo_destinatario(destinatario)

            cur.execute(
                """
                SELECT id, nome, quantidade, preco_unitario
                FROM pedido_itens
                WHERE pedido_id = %s
                ORDER BY id
                FOR UPDATE
                """,
                (pedido_id,),
            )
            itens = cur.fetchall()

            for item in itens:
                item_id = int(item["id"])
                qtd_antiga = int(item["quantidade"] or 0)
                nova_qtd = max(0, int(quantidades_por_item.get(str(item_id), qtd_antiga) or 0))
                delta = nova_qtd - qtd_antiga

                if delta > 0:
                    cur.execute(f"SELECT {campo} FROM sabores WHERE nome = %s FOR UPDATE", (item["nome"],))
                    sabor_row = cur.fetchone()
                    disponivel = int((sabor_row or {}).get(campo, 0) or 0)
                    if disponivel < delta:
                        raise ValueError(f"Estoque insuficiente para aumentar {item['nome']}.")
                    cur.execute(
                        f"""
                        UPDATE sabores
                        SET {campo} = {campo} - %s,
                            estoque = COALESCE(estoque_italo, 0) + COALESCE(estoque_karina, 0) - %s,
                            updated_at = NOW()
                        WHERE nome = %s
                        """,
                        (delta, delta, item["nome"]),
                    )
                elif delta < 0:
                    devolucao = abs(delta)
                    cur.execute(
                        f"""
                        UPDATE sabores
                        SET {campo} = {campo} + %s,
                            estoque = COALESCE(estoque_italo, 0) + COALESCE(estoque_karina, 0) + %s,
                            updated_at = NOW()
                        WHERE nome = %s
                        """,
                        (devolucao, devolucao, item["nome"]),
                    )

                if nova_qtd <= 0:
                    cur.execute("DELETE FROM pedido_itens WHERE id = %s AND pedido_id = %s", (item_id, pedido_id))
                else:
                    subtotal = money(Decimal(str(item["preco_unitario"])) * Decimal(nova_qtd))
                    cur.execute(
                        "UPDATE pedido_itens SET quantidade = %s, subtotal = %s WHERE id = %s AND pedido_id = %s",
                        (nova_qtd, subtotal, item_id, pedido_id),
                    )

            if novo_item_nome and novo_item_quantidade > 0:
                cur.execute(f"SELECT id, nome, preco, {campo} FROM sabores WHERE nome = %s FOR UPDATE", (novo_item_nome,))
                sabor_row = cur.fetchone()
                if not sabor_row:
                    raise ValueError("Sabor selecionado não encontrado.")
                disponivel_add = int((sabor_row or {}).get(campo, 0) or 0)
                if disponivel_add < novo_item_quantidade:
                    raise ValueError(f"Estoque insuficiente para adicionar {novo_item_nome}.")
                cur.execute(
                    f"""
                    UPDATE sabores
                    SET {campo} = {campo} - %s,
                        estoque = COALESCE(estoque_italo, 0) + COALESCE(estoque_karina, 0) - %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (novo_item_quantidade, novo_item_quantidade, int(sabor_row["id"])),
                )
                preco_add = money(sabor_row["preco"])
                subtotal_add = money(preco_add * Decimal(novo_item_quantidade))
                cur.execute(
                    "INSERT INTO pedido_itens (pedido_id, nome, quantidade, preco_unitario, subtotal) VALUES (%s, %s, %s, %s, %s)",
                    (pedido_id, novo_item_nome, novo_item_quantidade, preco_add, subtotal_add),
                )

            cur.execute(
                "SELECT COALESCE(SUM(subtotal), 0) AS total_restante, COUNT(*) AS itens_restantes FROM pedido_itens WHERE pedido_id = %s",
                (pedido_id,),
            )
            resumo = cur.fetchone()
            total_restante = money(resumo["total_restante"])
            itens_restantes = int(resumo["itens_restantes"] or 0)

            status_novo = 'cancelado' if itens_restantes <= 0 else ('pendente' if pedido_row['status'] == 'cancelado' else pedido_row['status'])
            pagamento_status_novo = pedido_row["pagamento_status"]
            if itens_restantes <= 0 and pagamento_status_novo != 'pago':
                pagamento_status_novo = 'cancelado'
            elif itens_restantes > 0 and pagamento_status_novo == 'cancelado':
                pagamento_status_novo = 'aguardando_pagamento'

            cur.execute(
                """
                UPDATE pedidos
                SET cliente_nome = %s,
                    cliente_telefone = %s,
                    cliente_endereco = %s,
                    total = %s,
                    status = %s,
                    pagamento_status = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (cliente_nome.strip(), cliente_telefone.strip(), cliente_endereco.strip(), total_restante, status_novo, pagamento_status_novo, pedido_id),
            )
        conn.commit()
    return True


def criar_pedido_db(pedido):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pedidos (
                    id, data, data_filtro, cliente_nome, cliente_telefone, cliente_endereco,
                    total, status, pagamento_status, destinatario, nome_vendedor,
                    pagamento_link, receipt_url, transaction_nsu, invoice_slug, capture_method,
                    preference_id, payment_id, payment_method, payment_detail, estoque_devolvido
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    int(pedido["id"]),
                    pedido["data"],
                    pedido["data_filtro"],
                    pedido["cliente"]["nome"],
                    pedido["cliente"].get("telefone", ""),
                    pedido["cliente"].get("endereco", ""),
                    money(pedido["total"]),
                    pedido["status"],
                    pedido["pagamento_status"],
                    pedido["destinatario"],
                    pedido["nome_vendedor"],
                    pedido.get("pagamento_link", ""),
                    pedido.get("receipt_url", ""),
                    pedido.get("transaction_nsu", ""),
                    pedido.get("invoice_slug", ""),
                    pedido.get("capture_method", ""),
                    pedido.get("preference_id", ""),
                    pedido.get("payment_id", ""),
                    pedido.get("payment_method", ""),
                    pedido.get("payment_detail", ""),
                    bool(pedido.get("estoque_devolvido", False)),
                ),
            )
            for item in pedido.get("itens", []):
                cur.execute(
                    """
                    INSERT INTO pedido_itens (pedido_id, nome, quantidade, preco_unitario, subtotal)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        int(pedido["id"]),
                        item["nome"],
                        int(item["quantidade"]),
                        money(item["preco_unitario"]),
                        money(item["subtotal"]),
                    ),
                )
        conn.commit()


def atualizar_pedido_db(pedido_id, **campos):
    if not campos:
        return False
    set_sql = []
    valores = []
    for chave, valor in campos.items():
        set_sql.append(f"{chave} = %s")
        valores.append(valor)
    set_sql.append("updated_at = NOW()")
    valores.append(pedido_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE pedidos SET {', '.join(set_sql)} WHERE id = %s", tuple(valores))
            updated = cur.rowcount > 0
        conn.commit()
        return updated


def registrar_pagamento_log(pedido_id, payment_id, status, raw_payload):
    if not db_enabled():
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pagamentos_log (pedido_id, payment_id, status, raw_payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (pedido_id, str(payment_id or ""), str(status or ""), json.dumps(raw_payload or {}, ensure_ascii=False)),
            )
        conn.commit()


def restaurar_estoque_do_pedido(pedido):
    if not pedido or pedido.get("estoque_devolvido"):
        return False
    campo = estoque_campo_destinatario(pedido.get("destinatario", "italo"))
    with get_conn() as conn:
        with conn.cursor() as cur:
            for item in pedido.get("itens", []):
                cur.execute(
                    f"UPDATE sabores SET {campo} = {campo} + %s, estoque = (COALESCE(estoque_italo,0) + COALESCE(estoque_karina,0)) + %s, updated_at = NOW() WHERE nome = %s",
                    (int(item.get("quantidade", 0) or 0), int(item.get("quantidade", 0) or 0), item.get("nome", "")),
                )
            cur.execute(
                "UPDATE pedidos SET estoque_devolvido = TRUE, updated_at = NOW() WHERE id = %s",
                (int(pedido["id"]),),
            )
        conn.commit()
    return True


def reservar_estoque(carrinho, destinatario):
    campo = estoque_campo_destinatario(destinatario)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for item in carrinho:
                cur.execute(f"SELECT {campo} AS estoque_destino, disponivel FROM sabores WHERE nome = %s FOR UPDATE", (item["nome"],))
                row = cur.fetchone()
                if not row or not row["disponivel"]:
                    raise ValueError(f"O sabor {item['nome']} não está disponível no momento.")
                if int(row["estoque_destino"]) < int(item["quantidade"]):
                    raise ValueError(f"Estoque insuficiente para {item['nome']} com {get_nome_vendedor(destinatario)}. Restam {int(row['estoque_destino'])} unidade(s).")
            for item in carrinho:
                q=int(item["quantidade"])
                cur.execute(
                    f"UPDATE sabores SET {campo} = {campo} - %s, estoque = GREATEST((COALESCE(estoque_italo,0) + COALESCE(estoque_karina,0)) - %s, 0), updated_at = NOW() WHERE nome = %s",
                    (q, q, item["nome"]),
                )
        conn.commit()


def migrate_json_to_db_once():
    if not db_enabled() or os.path.exists(MIGRATION_MARKER):
        return

    ensure_database()

    sabores_db = ler_sabores()
    if not sabores_db and os.path.exists(ARQUIVO_SABORES):
        for sabor in ler_json(ARQUIVO_SABORES, []):
            if isinstance(sabor, dict) and sabor.get("nome"):
                inserir_sabor(
                    {
                        "id": int(sabor.get("id", next_sabor_id()) or next_sabor_id()),
                        "nome": str(sabor.get("nome", "")).strip(),
                        "preco": float(sabor.get("preco", 0) or 0),
                        "img": str(sabor.get("img", "")).strip(),
                        "disponivel": bool(sabor.get("disponivel", True)),
                        "ativo_italo": bool(sabor.get("ativo_italo", sabor.get("disponivel", True))),
                        "ativo_karina": bool(sabor.get("ativo_karina", sabor.get("disponivel", True))),
                        "estoque": inteiro_positivo(sabor.get("estoque", 0)),
                        "estoque_italo": inteiro_positivo(sabor.get("estoque_italo"), inteiro_positivo(sabor.get("estoque", 0)) if sabor.get("estoque_italo") is None else 0),
                        "estoque_karina": inteiro_positivo(sabor.get("estoque_karina", 0)),
                    }
                )

    pedidos_db = ler_pedidos()
    if not pedidos_db and os.path.exists(ARQUIVO_PEDIDOS):
        pedidos_json = ler_json(ARQUIVO_PEDIDOS, [])
        for pedido in pedidos_json:
            if not isinstance(pedido, dict) or not pedido.get("id"):
                continue
            itens = []
            for item in pedido.get("itens", []):
                itens.append(
                    {
                        "nome": str(item.get("nome", "")).strip(),
                        "quantidade": int(item.get("quantidade", 0) or 0),
                        "preco_unitario": float(item.get("preco_unitario", item.get("preco", 0)) or 0),
                        "subtotal": float(item.get("subtotal", 0) or 0),
                    }
                )
            pedido_db = {
                "id": int(pedido["id"]),
                "data": str(pedido.get("data", "")),
                "data_filtro": str(pedido.get("data_filtro", datetime.now().strftime("%Y-%m-%d"))),
                "cliente": {
                    "nome": str((pedido.get("cliente") or {}).get("nome", "")).strip(),
                    "telefone": str((pedido.get("cliente") or {}).get("telefone", "")).strip(),
                    "endereco": str((pedido.get("cliente") or {}).get("endereco", "")).strip(),
                },
                "itens": itens,
                "total": float(pedido.get("total", 0) or 0),
                "status": str(pedido.get("status", "pendente")),
                "pagamento_status": str(pedido.get("pagamento_status", "aguardando_pagamento")),
                "destinatario": normalizar_destinatario(pedido.get("destinatario", "italo")),
                "nome_vendedor": str(pedido.get("nome_vendedor", get_nome_vendedor(pedido.get("destinatario", "italo")))),
                "pagamento_link": str(pedido.get("pagamento_link", "")),
                "receipt_url": str(pedido.get("receipt_url", "")),
                "transaction_nsu": str(pedido.get("transaction_nsu", "")),
                "invoice_slug": str(pedido.get("invoice_slug", "")),
                "capture_method": str(pedido.get("capture_method", "")),
                "preference_id": str(pedido.get("preference_id", "")),
                "payment_id": str(pedido.get("payment_id", "")),
                "payment_method": str(pedido.get("payment_method", "")),
                "payment_detail": str(pedido.get("payment_detail", "")),
                "estoque_devolvido": bool(pedido.get("estoque_devolvido", False)),
            }
            criar_pedido_db(pedido_db)

    # Só faz bootstrap da configuração da loja quando ela ainda não existir no banco.
    # Não sobrescreve config já salva após reinícios do serviço.
    if get_config_value("config_loja", None) is None:
        salvar_config(ler_config_arquivo())
    with open(MIGRATION_MARKER, "w", encoding="utf-8") as f:
        f.write(now_local().isoformat())


# =========================
# INFINITEPAY
# =========================
def infinitepay_ativo():
    config = ler_config()
    return bool(config.get("infinitepay_ativo", True) and INFINITEPAY_HANDLE)


def criar_checkout_infinitepay(pedido):
    if not infinitepay_ativo():
        return {"checkout_url": "", "order_nsu": str(pedido["id"])}

    url = "https://api.infinitepay.io/invoices/public/checkout/links"
    payload = {
        "handle": INFINITEPAY_HANDLE,
        "items": [
            {
                "quantity": int(item["quantidade"]),
                "price": int(round(float(item["preco_unitario"]) * 100)),
                "description": item["nome"],
            }
            for item in pedido.get("itens", [])
        ],
        "order_nsu": str(pedido["id"]),
        "redirect_url": f"{obter_base_url()}{url_for('retorno_pagamento')}",
        "webhook_url": f"{obter_base_url()}{url_for('webhook_infinitepay')}",
    }

    telefone = normalizar_telefone_br(pedido["cliente"].get("telefone", ""))
    nome = str(pedido["cliente"].get("nome", "")).strip()
    if telefone:
        payload["customer"] = {
            "name": nome or "Cliente",
            "phone_number": telefone,
        }

    resp = requests.post(url, json=payload, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"Erro ao criar checkout InfinitePay: {resp.status_code} - {resp.text[:300]}")

    data = resp.json()
    checkout_url = data.get("checkout_url") or data.get("url") or data.get("link") or ""
    return {
        "checkout_url": checkout_url,
        "order_nsu": str(pedido["id"]),
        "raw": data,
    }


def consultar_pagamento_infinitepay(order_nsu, transaction_nsu="", slug=""):
    if not infinitepay_ativo():
        return None
    payload = {
        "handle": INFINITEPAY_HANDLE,
        "order_nsu": str(order_nsu),
    }
    if transaction_nsu:
        payload["transaction_nsu"] = transaction_nsu
    if slug:
        payload["slug"] = slug

    resp = requests.post("https://api.infinitepay.io/invoices/public/checkout/payment_check", json=payload, timeout=20)
    if not resp.ok:
        return None
    return resp.json()


def atualizar_status_pagamento_infinitepay(pedido_id, payment_data):
    if not payment_data:
        return False

    pedido = buscar_pedido(pedido_id)
    if not pedido:
        return False

    paid = bool(payment_data.get("paid"))
    capture_method = str(payment_data.get("capture_method", "")).strip()
    transaction_nsu = str(payment_data.get("transaction_nsu", "")).strip()
    receipt_url = str(payment_data.get("receipt_url", "")).strip()
    slug = str(payment_data.get("slug", payment_data.get("invoice_slug", ""))).strip()

    atualizar_pedido_db(
        pedido_id,
        pagamento_status="pago" if paid else "aguardando_pagamento",
        payment_method=capture_method,
        payment_detail="Pagamento confirmado pela InfinitePay" if paid else "Aguardando pagamento InfinitePay",
        capture_method=capture_method,
        transaction_nsu=transaction_nsu,
        invoice_slug=slug,
        receipt_url=receipt_url,
    )
    registrar_pagamento_log(pedido_id, transaction_nsu, "paid" if paid else "pending", payment_data)
    return True

# =========================
# CONTROLE DE CACHE
# =========================
@app.after_request
def add_cache_headers(response):
    caminho = request.path or ""
    if caminho.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        response.headers.pop("Pragma", None)
        response.headers.pop("Expires", None)
    else:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# =========================
# ROTAS PÚBLICAS
# =========================
@app.route("/")
def home():
    carrinho = session.get("carrinho", [])
    qtd_itens = contar_itens_carrinho(carrinho)
    config = ler_config()
    destinatario_sessao = normalizar_destinatario(session.get("destinatario_atual", "italo"))
    destinatario_atual = ajustar_destinatario_disponivel(destinatario_sessao, config)
    if destinatario_atual != destinatario_sessao:
        session["destinatario_atual"] = destinatario_atual
        session.modified = True
    sabores = [
        enrich_sabor_destinatario(s, destinatario_atual, carrinho)
        for s in ler_sabores()
        if sabor_ativo_para_destinatario(s, destinatario_atual)
    ]
    destinatario_bloqueado = pedidos_bloqueados_para(config, destinatario_atual)
    return render_template(
        "index.html",
        sabores=sabores,
        qtd_itens=qtd_itens,
        chave_pix=CHAVE_PIX,
        nome_pix=NOME_PIX,
        banco_pix=BANCO_PIX,
        infinitepay_ativo=infinitepay_ativo(),
        pix_checkout_automatico=infinitepay_ativo(),
        loja_aberta=config.get("loja_aberta", True),
        mensagem_loja=config.get("mensagem_loja_fechada", ""),
        mensagem=pop_mensagem("mensagem_home"),
        destinatario_atual=destinatario_atual,
        destinatario_bloqueado=destinatario_bloqueado,
        bloquear_italo=pedidos_bloqueados_para(config, "italo"),
        bloquear_karina=pedidos_bloqueados_para(config, "karina"),
        mensagem_bloqueio_destinatario=mensagem_bloqueio_destinatario(destinatario_atual) if destinatario_bloqueado else "",
    )




@app.route("/definir_destinatario", methods=["POST"])
def definir_destinatario():
    config = ler_config()
    solicitado = normalizar_destinatario(request.form.get("destinatario", "italo"))
    ajustado = ajustar_destinatario_disponivel(solicitado, config)
    session["destinatario_atual"] = ajustado
    session.modified = True
    if pedidos_bloqueados_para(config, solicitado):
        set_mensagem("mensagem_home", mensagem_bloqueio_destinatario(solicitado))
    destino = request.form.get("next") or request.headers.get("Referer") or "/"
    return redirect(destino)

@app.route("/pedido", methods=["POST"])
def pedido():
    config = ler_config()
    if not config.get("loja_aberta", True):
        set_mensagem("mensagem_home", config.get("mensagem_loja_fechada", "A loja está fechada no momento."))
        return redirect("/")

    sabor_nome = request.form.get("sabor", "").strip()
    quantidade = request.form.get("quantidade", type=int)
    destinatario = normalizar_destinatario(request.form.get("destinatario", session.get("destinatario_atual", "italo")))
    session["destinatario_atual"] = destinatario
    if pedidos_bloqueados_para(config, destinatario):
        set_mensagem("mensagem_home", mensagem_bloqueio_destinatario(destinatario))
        return redirect("/")
    if not sabor_nome or not quantidade or quantidade < 1:
        set_mensagem("mensagem_home", "Escolha um sabor e informe uma quantidade válida.")
        return redirect("/")

    sabor = buscar_sabor_por_nome(sabor_nome)
    if not sabor or not sabor_ativo_para_destinatario(sabor, destinatario):
        set_mensagem("mensagem_home", f"Esse sabor não está ativo no momento para {get_nome_vendedor(destinatario)}.")
        return redirect("/")
    estoque_disponivel = estoque_para_destinatario(sabor, destinatario)
    if estoque_disponivel <= 0:
        set_mensagem("mensagem_home", f"No momento estamos sem estoque do sabor {sabor_nome} com {get_nome_vendedor(destinatario)}.")
        return redirect("/")

    carrinho = session.get("carrinho", [])
    item_existente = next((item for item in carrinho if item.get("nome") == sabor_nome), None)
    quantidade_no_carrinho = int(item_existente.get("quantidade", 0)) if item_existente else 0
    nova_quantidade = quantidade_no_carrinho + quantidade

    if nova_quantidade > estoque_disponivel:
        set_mensagem("mensagem_home", f"Estoque insuficiente para {sabor_nome} com {get_nome_vendedor(destinatario)}. Disponível: {estoque_disponivel} unidade(s).")
        return redirect("/")

    if item_existente:
        item_existente["quantidade"] = nova_quantidade
        item_existente["subtotal"] = round(nova_quantidade * float(item_existente["preco"]), 2)
    else:
        carrinho.append(
            {
                "nome": sabor["nome"],
                "preco": float(sabor["preco"]),
                "quantidade": quantidade,
                "subtotal": round(float(sabor["preco"]) * quantidade, 2),
            }
        )

    session["carrinho"] = carrinho
    session.modified = True
    mensagem_sucesso = f"{sabor_nome} adicionado ao carrinho com sucesso."
    if is_ajax_request():
        sabor_atualizado = enrich_sabor_destinatario(sabor, destinatario, carrinho)
        return jsonify({
            "ok": True,
            "message": "Adicionado com sucesso.",
            "cart_count": contar_itens_carrinho(carrinho),
            "flavor_name": sabor_nome,
            "flavor_key": str(sabor_nome).strip().lower().replace(" ", "-"),
            "estoque_exibicao": int(sabor_atualizado.get("estoque_exibicao", 0) or 0),
            "item_quantity": nova_quantidade,
            "button_label": "Indisponível" if int(sabor_atualizado.get("estoque_exibicao", 0) or 0) <= 0 else "Adicionar ao carrinho",
        })
    set_mensagem("mensagem_home", mensagem_sucesso)
    return redirect("/")


@app.route("/carrinho")
def carrinho():
    carrinho = session.get("carrinho", [])
    total = sum(float(item.get("subtotal", 0) or 0) for item in carrinho)
    qtd_itens = contar_itens_carrinho(carrinho)
    config = ler_config()
    destinatario_sessao = normalizar_destinatario(session.get("destinatario_atual", "italo"))
    destinatario_atual = ajustar_destinatario_disponivel(destinatario_sessao, config)
    if destinatario_atual != destinatario_sessao:
        session["destinatario_atual"] = destinatario_atual
        session.modified = True
    destinatario_bloqueado = pedidos_bloqueados_para(config, destinatario_atual)
    carrinho_view = []
    for item in carrinho:
        item_view = dict(item)
        sabor = buscar_sabor_por_nome(item.get("nome", ""))
        if sabor and sabor_ativo_para_destinatario(sabor, destinatario_atual):
            item_view["estoque_maximo"] = max(0, estoque_para_destinatario(sabor, destinatario_atual))
        else:
            item_view["estoque_maximo"] = max(0, int(item.get("quantidade", 0) or 0))
        carrinho_view.append(item_view)
    return render_template(
        "carrinho.html",
        carrinho=carrinho_view,
        total=total,
        qtd_itens=qtd_itens,
        chave_pix=CHAVE_PIX,
        nome_pix=NOME_PIX,
        banco_pix=BANCO_PIX,
        infinitepay_ativo=infinitepay_ativo(),
        pix_checkout_automatico=infinitepay_ativo(),
        loja_aberta=config.get("loja_aberta", True),
        mensagem_loja=config.get("mensagem_loja_fechada", ""),
        mensagem=pop_mensagem("mensagem_carrinho"),
        destinatario_atual=destinatario_atual,
        destinatario_bloqueado=destinatario_bloqueado,
        bloquear_italo=pedidos_bloqueados_para(config, "italo"),
        bloquear_karina=pedidos_bloqueados_para(config, "karina"),
        mensagem_bloqueio_destinatario=mensagem_bloqueio_destinatario(destinatario_atual) if destinatario_bloqueado else "",
    )


@app.route("/remover_item/<int:item_index>", methods=["POST"])
def remover_item_carrinho(item_index):
    carrinho = session.get("carrinho", [])
    removido = None
    removed_index = item_index
    item_nome = request.form.get("item_nome", "").strip().lower()
    if 0 <= item_index < len(carrinho):
        removido = carrinho.pop(item_index)
    elif item_nome:
        for idx, item in enumerate(carrinho):
            if str(item.get("nome", "")).strip().lower() == item_nome:
                removido = carrinho.pop(idx)
                removed_index = idx
                break
    if removido is not None:
        session["carrinho"] = carrinho
        session.modified = True
    total = sum(float(item.get("subtotal", 0) or 0) for item in carrinho)
    if is_ajax_request():
        return jsonify({
            "ok": removido is not None,
            "removed": removido is not None,
            "removed_index": removed_index,
            "removed_name": removido.get("nome") if removido else "",
            "cart_count": contar_itens_carrinho(carrinho),
            "total": round(total, 2),
            "total_text": f"R$ {total:.2f}",
            "message": f"{removido.get('nome', 'Item')} removido do carrinho." if removido else "Item não encontrado.",
        })
    return redirect("/carrinho")


@app.route("/carrinho/atualizar/<int:item_index>", methods=["POST"])
def atualizar_item_carrinho(item_index):
    carrinho = session.get("carrinho", [])
    item_nome = request.form.get("item_nome", "").strip().lower()
    if not (0 <= item_index < len(carrinho)) and item_nome:
        for idx, current in enumerate(carrinho):
            if str(current.get("nome", "")).strip().lower() == item_nome:
                item_index = idx
                break
    if not (0 <= item_index < len(carrinho)):
        if is_ajax_request():
            return jsonify({"ok": False, "message": "Item do carrinho não encontrado."}), 404
        set_mensagem("mensagem_carrinho", "Item do carrinho não encontrado.")
        return redirect("/carrinho")

    item = carrinho[item_index]
    try:
        nova_quantidade = max(0, int(request.form.get("quantidade", item.get("quantidade", 1)) or 0))
    except (TypeError, ValueError):
        nova_quantidade = int(item.get("quantidade", 1) or 1)

    if nova_quantidade <= 0:
        carrinho.pop(item_index)
        session["carrinho"] = carrinho
        session.modified = True
        total = sum(float(prod.get("subtotal", 0) or 0) for prod in carrinho)
        if is_ajax_request():
            return jsonify({
                "ok": True,
                "removed": True,
                "removed_index": item_index,
                "cart_count": contar_itens_carrinho(carrinho),
                "total": round(total, 2),
                "total_text": f"R$ {total:.2f}",
                "message": f"{item.get('nome', 'Item')} removido do carrinho.",
            })
        set_mensagem("mensagem_carrinho", f"{item.get('nome', 'Item')} removido do carrinho.")
        return redirect("/carrinho")

    destinatario = normalizar_destinatario(session.get("destinatario_atual", "italo"))
    sabor = buscar_sabor_por_nome(item.get("nome", ""))
    if not sabor or not sabor_ativo_para_destinatario(sabor, destinatario):
        mensagem = f"O sabor {item.get('nome', 'selecionado')} não está disponível agora para {get_nome_vendedor(destinatario)}."
        if is_ajax_request():
            return jsonify({"ok": False, "message": mensagem}), 400
        set_mensagem("mensagem_carrinho", mensagem)
        return redirect("/carrinho")

    estoque_disponivel = max(0, estoque_para_destinatario(sabor, destinatario))
    if nova_quantidade > estoque_disponivel:
        mensagem = f"Estoque insuficiente para {item.get('nome')}. Disponível: {estoque_disponivel} unidade(s)."
        if is_ajax_request():
            return jsonify({"ok": False, "message": mensagem, "estoque_maximo": estoque_disponivel}), 400
        set_mensagem("mensagem_carrinho", mensagem)
        return redirect("/carrinho")

    item["quantidade"] = nova_quantidade
    item["subtotal"] = round(float(item.get("preco", 0) or 0) * nova_quantidade, 2)
    session["carrinho"] = carrinho
    session.modified = True
    if is_ajax_request():
        total = sum(float(prod.get("subtotal", 0) or 0) for prod in carrinho)
        sabor_atualizado = enrich_sabor_destinatario(sabor, destinatario, carrinho)
        return jsonify({
            "ok": True,
            "item_index": item_index,
            "item_quantity": nova_quantidade,
            "item_subtotal": round(float(item.get("subtotal", 0) or 0), 2),
            "item_subtotal_text": f"R$ {float(item.get('subtotal', 0) or 0):.2f}",
            "cart_count": contar_itens_carrinho(carrinho),
            "total": round(total, 2),
            "total_text": f"R$ {total:.2f}",
            "message": f"Quantidade de {item.get('nome')} atualizada para {nova_quantidade}.",
            "flavor_key": str(item.get("nome", "")).strip().lower().replace(" ", "-"),
            "estoque_exibicao": int(sabor_atualizado.get("estoque_exibicao", 0) or 0),
            "estoque_maximo": max(0, estoque_para_destinatario(sabor, destinatario)),
        })
    set_mensagem("mensagem_carrinho", f"Quantidade de {item.get('nome')} atualizada para {nova_quantidade}.")
    return redirect("/carrinho")


@app.route("/limpar_carrinho", methods=["POST"])
def limpar_carrinho():
    session["carrinho"] = []
    session.modified = True
    if is_ajax_request():
        return jsonify({"ok": True, "cart_count": 0, "total": 0, "total_text": "R$ 0.00", "message": "Carrinho limpo com sucesso."})
    return redirect("/carrinho")


@app.route("/finalizar_pedido", methods=["POST"])
def finalizar_pedido():
    if not db_enabled():
        set_mensagem("mensagem_carrinho", "Configure o DATABASE_URL para usar a nova versão com Neon.")
        return redirect("/carrinho")

    config = ler_config()
    if not config.get("loja_aberta", True):
        set_mensagem("mensagem_carrinho", config.get("mensagem_loja_fechada", "A loja está fechada no momento."))
        return redirect("/carrinho")

    nome = request.form.get("nome", "").strip()
    telefone = request.form.get("telefone", "").strip()
    endereco = request.form.get("endereco", "").strip()
    destinatario = normalizar_destinatario(request.form.get("destinatario", session.get("destinatario_atual", "italo")))
    session["destinatario_atual"] = destinatario
    carrinho = session.get("carrinho", [])

    if pedidos_bloqueados_para(config, destinatario):
        set_mensagem("mensagem_carrinho", mensagem_bloqueio_destinatario(destinatario))
        return redirect("/carrinho")
    if not carrinho:
        set_mensagem("mensagem_carrinho", "Seu carrinho está vazio.")
        return redirect("/carrinho")
    if not nome:
        set_mensagem("mensagem_carrinho", "Informe seu nome para finalizar o pedido.")
        return redirect("/carrinho")

    agora = now_local()
    pedido_id = int(agora.timestamp() * 1000)

    itens_pedido = []
    total = Decimal("0.00")
    for item in carrinho:
        subtotal = money(float(item.get("preco", 0) or 0) * int(item.get("quantidade", 0) or 0))
        itens_pedido.append(
            {
                "nome": item.get("nome", ""),
                "quantidade": int(item.get("quantidade", 0) or 0),
                "preco_unitario": float(item.get("preco", 0) or 0),
                "subtotal": float(subtotal),
            }
        )
        total += subtotal

    try:
        reservar_estoque(itens_pedido, destinatario)
    except ValueError as e:
        set_mensagem("mensagem_carrinho", str(e))
        return redirect("/carrinho")

    pedido = {
        "id": pedido_id,
        "data": agora.strftime("%d/%m/%Y %H:%M"),
        "data_filtro": agora.strftime("%Y-%m-%d"),
        "cliente": {"nome": nome, "telefone": telefone, "endereco": endereco},
        "itens": itens_pedido,
        "total": float(total),
        "status": "pendente",
        "pagamento_status": "aguardando_pagamento",
        "destinatario": destinatario,
        "nome_vendedor": get_nome_vendedor(destinatario),
        "pagamento_link": "",
        "receipt_url": "",
        "transaction_nsu": "",
        "invoice_slug": "",
        "capture_method": "",
        "preference_id": "",
        "payment_id": "",
        "payment_method": "",
        "payment_detail": "",
        "estoque_devolvido": False,
        "oculto": False,
        "ocultado_em": "",
    }

    try:
        if infinitepay_ativo():
            checkout = criar_checkout_infinitepay(pedido)
            pedido["pagamento_link"] = checkout.get("checkout_url", "")
            pedido["invoice_slug"] = str(checkout.get("raw", {}).get("slug", ""))
    except Exception as e:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for item in itens_pedido:
                    campo = estoque_campo_destinatario(destinatario)
                    cur.execute(f"UPDATE sabores SET {campo} = {campo} + %s, estoque = GREATEST(COALESCE(estoque_italo,0) + COALESCE(estoque_karina,0),0) WHERE nome = %s", (item["quantidade"], item["nome"]))
            conn.commit()
        set_mensagem("mensagem_carrinho", f"Não foi possível iniciar o pagamento online agora. Detalhe: {str(e)[:180]}")
        return redirect("/carrinho")

    criar_pedido_db(pedido)

    mensagem = montar_mensagem_whatsapp(pedido, pedido.get("pagamento_link", ""))
    whatsapp_destino = criar_link_whatsapp(get_numero_vendedor(destinatario), mensagem)

    session["carrinho"] = []
    session.modified = True

    return render_template(
        "pedido_criado.html",
        pedido=pedido,
        whatsapp_destino=whatsapp_destino,
        responsavel_nome=get_nome_vendedor(destinatario),
        pagamento_link=pedido.get("pagamento_link", ""),
        pix_checkout_automatico=infinitepay_ativo(),
    )


def montar_mensagem_whatsapp(pedido, pagamento_link=""):
    cliente = pedido.get("cliente", {})
    itens = pedido.get("itens", [])
    linhas_itens = []
    for item in itens:
        linhas_itens.append(
            f"- {item.get('nome', '')} | Qtd: {item.get('quantidade', 0)} | Unit: R$ {float(item.get('preco_unitario', 0)):.2f} | Subtotal: R$ {float(item.get('subtotal', 0)):.2f}"
        )
    telefone = cliente.get("telefone", "")
    endereco = cliente.get("endereco", "")
    telefone_linha = f"📞 Telefone: {telefone}\n" if telefone else ""
    endereco_linha = f"📍 Endereço: {endereco}\n" if endereco else ""
    mensagem = (
        "🍦 *Novo Pedido - Geladinhos Gourmet*\n\n"
        f"🧾 Pedido: #{pedido.get('id')}\n"
        f"👤 Cliente: {cliente.get('nome', '')}\n"
        f"{telefone_linha}"
        f"{endereco_linha}"
        f"🏷️ Responsável: {pedido.get('nome_vendedor', '')}\n\n"
        "🛒 *Itens do pedido:*\n"
        + "\n".join(linhas_itens)
        + f"\n\n💰 *Total do pedido: R$ {float(pedido.get('total', 0)):.2f}*"
    )
    if pagamento_link:
        mensagem += f"\n\n💳 Link de pagamento InfinitePay: {pagamento_link}"
    if CHAVE_PIX:
        mensagem += f"\n\n📌 Chave Pix para pagamento: {CHAVE_PIX}"
        if NOME_PIX:
            mensagem += f"\nTitular Pix: {NOME_PIX}"
        if BANCO_PIX:
            mensagem += f"\nBanco: {BANCO_PIX}"
    return mensagem


def criar_link_whatsapp(numero, mensagem):
    return f"https://wa.me/{numero}?text={quote(mensagem)}"


@app.route("/pagamento/retorno")
def retorno_pagamento():
    order_nsu = request.args.get("order_nsu", "").strip()
    transaction_nsu = request.args.get("transaction_nsu", "").strip()
    slug = request.args.get("slug", "").strip()
    receipt_url = request.args.get("receipt_url", "").strip()
    capture_method = request.args.get("capture_method", "").strip()

    if order_nsu.isdigit():
        pedido_id = int(order_nsu)
        payment = consultar_pagamento_infinitepay(order_nsu, transaction_nsu=transaction_nsu, slug=slug) or {}
        if receipt_url and "receipt_url" not in payment:
            payment["receipt_url"] = receipt_url
        if capture_method and "capture_method" not in payment:
            payment["capture_method"] = capture_method
        atualizar_status_pagamento_infinitepay(pedido_id, payment)
        set_mensagem("mensagem_home", f"Retorno recebido do pedido #{pedido_id}.")
    else:
        set_mensagem("mensagem_home", "Retorno de pagamento recebido.")

    return redirect("/")


@app.route("/webhooks/infinitepay", methods=["POST"])
def webhook_infinitepay():
    body = request.get_json(silent=True) or {}
    order_nsu = str(body.get("order_nsu", "")).strip()
    if not order_nsu.isdigit():
        return jsonify({"ok": False, "message": "order_nsu inválido"}), 400
    atualizar_status_pagamento_infinitepay(int(order_nsu), body)
    return jsonify({"ok": True}), 200


# =========================
# ADMIN
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    erro = None
    redefinir_msg = pop_mensagem("mensagem_redefinir_senha")
    redefinir_erro = pop_mensagem("erro_redefinir_senha")
    if request.method == "POST":
        senha = request.form.get("senha", "").strip()
        if senha == get_admin_password():
            session["admin_logado"] = True
            return redirect("/admin")
        erro = "Senha incorreta."
    return render_template("admin_login.html", erro=erro, redefinir_msg=redefinir_msg, redefinir_erro=redefinir_erro)


@app.route("/admin/redefinir-senha", methods=["POST"])
def admin_redefinir_senha():
    senha_atual = request.form.get("senha_atual", "").strip()
    nova_senha = request.form.get("nova_senha", "").strip()
    confirmar = request.form.get("confirmar_senha", "").strip()
    if senha_atual != get_admin_password():
        set_mensagem("erro_redefinir_senha", "A senha antiga não confere.")
        return redirect("/admin/login")
    if len(nova_senha) < 4:
        set_mensagem("erro_redefinir_senha", "A nova senha precisa ter pelo menos 4 caracteres.")
        return redirect("/admin/login")
    if nova_senha != confirmar:
        set_mensagem("erro_redefinir_senha", "A confirmação da nova senha não confere.")
        return redirect("/admin/login")
    salvar_admin_password(nova_senha)
    set_mensagem("mensagem_redefinir_senha", "Senha do admin atualizada com sucesso.")
    return redirect("/admin/login")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logado", None)
    return redirect("/admin/login")


@app.route("/admin")
def admin():
    if not admin_logado():
        return redirect("/admin/login")

    pedidos = ler_pedidos()
    sabores = ler_sabores()
    config = ler_config()

    filtro_cliente = request.args.get("cliente", "").strip().lower()
    filtro_data = request.args.get("data", "").strip()
    if not filtro_data:
        filtro_data = now_local().strftime("%Y-%m-%d")
    filtro_status = request.args.get("status", "").strip().lower()
    filtro_pagamento = request.args.get("pagamento", "").strip().lower()

    aliases_status_rapidos = {"todos", "nao_pagos", "pagos", "cancelados", "ocultos"}
    status_rapido = filtro_status if filtro_status in aliases_status_rapidos else ""
    if status_rapido in {"nao_pagos", "pagos"} and not filtro_pagamento:
        filtro_pagamento = "aguardando_pagamento" if status_rapido == "nao_pagos" else "pago"
    if status_rapido == "cancelados":
        filtro_pagamento = filtro_pagamento if filtro_pagamento in {"cancelado"} else filtro_pagamento
    filtro_vendedor = request.args.get("vendedor", "").strip().lower()
    filtro_ocultos = request.args.get("ocultos", "ocultar").strip().lower()

    pedidos_filtrados = []
    for pedido in pedidos:
        nome_cliente = str(pedido.get("cliente", {}).get("nome", "")).lower()
        data_pedido = pedido.get("data_filtro", "")
        status_pedido = str(pedido.get("status", "pendente")).lower()
        pagamento_status = str(pedido.get("pagamento_status", "aguardando_pagamento")).lower()
        vendedor = str(pedido.get("destinatario", "italo")).lower()
        oculto = bool(pedido.get("oculto", False))
        if filtro_cliente and filtro_cliente not in nome_cliente:
            continue
        if filtro_data and filtro_data != data_pedido:
            continue
        if status_rapido == "cancelados":
            if not (status_pedido == "cancelado" or pagamento_status == "cancelado"):
                continue
        elif status_rapido == "ocultos":
            if not oculto:
                continue
        elif filtro_status and not status_rapido and filtro_status != status_pedido:
            continue

        if filtro_pagamento and pagamento_status != filtro_pagamento:
            continue
        if filtro_vendedor and filtro_vendedor != vendedor:
            continue
        if filtro_ocultos == "ocultar" and oculto:
            continue
        if filtro_ocultos == "somente" and not oculto:
            continue
        pedidos_filtrados.append(pedido)

    total_pedidos = len(pedidos_filtrados)
    faturamento_total = sum(float(p.get("total", 0) or 0) for p in pedidos_filtrados if p.get("pagamento_status") == "pago")
    hoje = now_local().strftime("%Y-%m-%d")
    faturamento_hoje = sum(float(p.get("total", 0) or 0) for p in pedidos if p.get("data_filtro") == hoje and p.get("pagamento_status") == "pago" and not p.get("oculto", False))
    total_nao_pago = sum(float(p.get("total", 0) or 0) for p in pedidos_filtrados if p.get("pagamento_status") == "aguardando_pagamento" and not p.get("oculto", False))

    metricas_responsavel = {
        "italo": {
            "total_pedidos": len([p for p in pedidos_filtrados if p.get("destinatario") == "italo"]),
            "faturamento_pago": sum(float(p.get("total", 0) or 0) for p in pedidos_filtrados if p.get("destinatario") == "italo" and p.get("pagamento_status") == "pago"),
            "faturamento_pago_hoje": sum(float(p.get("total", 0) or 0) for p in pedidos if p.get("data_filtro") == hoje and p.get("destinatario") == "italo" and p.get("pagamento_status") == "pago" and not p.get("oculto", False)),
            "total_nao_pago": sum(float(p.get("total", 0) or 0) for p in pedidos_filtrados if p.get("destinatario") == "italo" and p.get("pagamento_status") == "aguardando_pagamento" and not p.get("oculto", False)),
        },
        "karina": {
            "total_pedidos": len([p for p in pedidos_filtrados if p.get("destinatario") == "karina"]),
            "faturamento_pago": sum(float(p.get("total", 0) or 0) for p in pedidos_filtrados if p.get("destinatario") == "karina" and p.get("pagamento_status") == "pago"),
            "faturamento_pago_hoje": sum(float(p.get("total", 0) or 0) for p in pedidos if p.get("data_filtro") == hoje and p.get("destinatario") == "karina" and p.get("pagamento_status") == "pago" and not p.get("oculto", False)),
            "total_nao_pago": sum(float(p.get("total", 0) or 0) for p in pedidos_filtrados if p.get("destinatario") == "karina" and p.get("pagamento_status") == "aguardando_pagamento" and not p.get("oculto", False)),
        },
    }

    sabores_vendidos = {}
    for pedido in pedidos_filtrados:
        for item in pedido.get("itens", []):
            nome = item.get("nome", "Sem nome")
            quantidade = int(item.get("quantidade", 0) or 0)
            sabores_vendidos[nome] = sabores_vendidos.get(nome, 0) + quantidade
    ranking_sabores = sorted(sabores_vendidos.items(), key=lambda x: x[1], reverse=True)

    pedidos_italo = [p for p in pedidos_filtrados if p.get("destinatario") == "italo"]
    pedidos_karina = [p for p in pedidos_filtrados if p.get("destinatario") == "karina"]

    pedidos_visiveis = [p for p in pedidos_filtrados if not p.get("oculto", False)]
    pedidos_ocultos = [p for p in pedidos_filtrados if p.get("oculto", False)]
    abas_pedidos = {
        "todos": pedidos_visiveis,
        "nao_pagos": [p for p in pedidos_visiveis if p.get("pagamento_status") == "aguardando_pagamento"],
        "pagos": [p for p in pedidos_visiveis if p.get("pagamento_status") == "pago"],
        "cancelados": [p for p in pedidos_visiveis if p.get("status") == "cancelado" or p.get("pagamento_status") == "cancelado"],
        "ocultos": pedidos_ocultos,
    }

    ultimo_pedido_id = max([int(p.get("id", 0) or 0) for p in pedidos], default=0)
    resumo_status = resumo_status_pedidos(pedidos_filtrados)

    return render_template(
        "admin.html",
        pedidos=pedidos_filtrados,
        pedidos_italo=pedidos_italo,
        pedidos_karina=pedidos_karina,
        total_pedidos=total_pedidos,
        faturamento_total=faturamento_total,
        faturamento_hoje=faturamento_hoje,
        total_nao_pago=total_nao_pago,
        ranking_sabores=ranking_sabores,
        resumo_status=resumo_status,
        filtro_data_label=parse_data_filtro_admin(request.args.get("data", "")),
        filtro_cliente=request.args.get("cliente", ""),
        filtro_data=request.args.get("data", ""),
        filtro_status=filtro_status,
        filtro_pagamento=request.args.get("pagamento", ""),
        filtro_vendedor=request.args.get("vendedor", ""),
        filtro_ocultos=filtro_ocultos,
        sabores=sabores,
        config=config,
        mensagem=pop_mensagem("mensagem_admin"),
        infinitepay_ativo=infinitepay_ativo(),
        abas_pedidos=abas_pedidos,
        ultimo_pedido_id=ultimo_pedido_id,
        metricas_responsavel=metricas_responsavel,
        request_path=request.full_path if request.query_string else request.path,
        quick_order_url="/admin/pedido_rapido",
    )


@app.route("/admin/sabores")
def admin_sabores():
    if not admin_logado():
        return redirect("/admin/login")
    return render_template(
        "admin_sabores.html",
        sabores=ler_sabores(),
        mensagem=pop_mensagem("mensagem_admin"),
        request_path=request.full_path if request.query_string else request.path,
    )


def filtrar_pedidos_analise(pedidos, data_inicial='', data_final='', responsavel='', pagamento='', incluir_ocultos=False):
    resultado = []
    for pedido in pedidos:
        data_pedido = str(pedido.get('data_filtro', '') or '')
        if data_inicial and data_pedido < data_inicial:
            continue
        if data_final and data_pedido > data_final:
            continue
        if responsavel and str(pedido.get('destinatario', '')).lower() != responsavel:
            continue
        if pagamento and str(pedido.get('pagamento_status', '')).lower() != pagamento:
            continue
        if not incluir_ocultos and pedido.get('oculto', False):
            continue
        resultado.append(pedido)
    return resultado


@app.route("/admin/analise")
def admin_analise():
    if not admin_logado():
        return redirect('/admin/login')
    pedidos = ler_pedidos()
    hoje = now_local().strftime('%Y-%m-%d')
    periodo = request.args.get('periodo', 'todos').strip().lower()
    data_inicial = request.args.get('data_inicial', '').strip()
    data_final = request.args.get('data_final', '').strip()
    responsavel = request.args.get('responsavel', '').strip().lower()
    pagamento = request.args.get('pagamento', '').strip().lower()
    incluir_ocultos = request.args.get('ocultos', '').strip().lower() == '1'

    if periodo == 'hoje' and not data_inicial and not data_final:
        data_inicial = hoje
        data_final = hoje
    elif periodo == '7dias' and not data_inicial and not data_final:
        base = now_local()
        data_inicial = (base - timedelta(days=6)).strftime('%Y-%m-%d')
        data_final = hoje
    elif periodo == '30dias' and not data_inicial and not data_final:
        base = now_local()
        data_inicial = (base - timedelta(days=29)).strftime('%Y-%m-%d')
        data_final = hoje
    elif periodo == 'mes' and not data_inicial and not data_final:
        base = now_local()
        data_inicial = base.replace(day=1).strftime('%Y-%m-%d')
        data_final = hoje
    elif periodo in ('todos', 'custom') and not data_inicial and not data_final:
        data_inicial = ''
        data_final = ''

    filtrados = filtrar_pedidos_analise(pedidos, data_inicial, data_final, responsavel, pagamento, incluir_ocultos)
    total_pedidos = len(filtrados)
    total_pago = sum(float(p.get('total', 0) or 0) for p in filtrados if p.get('pagamento_status') == 'pago')
    total_pendente = sum(float(p.get('total', 0) or 0) for p in filtrados if p.get('pagamento_status') == 'aguardando_pagamento')
    faturamento_bruto = sum(float(p.get('total', 0) or 0) for p in filtrados)
    ticket_medio = (faturamento_bruto / total_pedidos) if total_pedidos else 0

    comparativo = {}
    for resp in ['italo', 'karina']:
        subset = [p for p in filtrados if p.get('destinatario') == resp]
        comparativo[resp] = {
            'total_pedidos': len(subset),
            'pago': sum(float(p.get('total', 0) or 0) for p in subset if p.get('pagamento_status') == 'pago'),
            'pendente': sum(float(p.get('total', 0) or 0) for p in subset if p.get('pagamento_status') == 'aguardando_pagamento'),
        }

    sabores = {}
    faturamento_sabor = {}
    pedidos_por_dia = {}
    devedores = {}
    for pedido in filtrados:
        pedidos_por_dia[pedido.get('data_filtro', '')] = pedidos_por_dia.get(pedido.get('data_filtro', ''), {'pedidos': 0, 'faturamento': 0.0})
        pedidos_por_dia[pedido.get('data_filtro', '')]['pedidos'] += 1
        pedidos_por_dia[pedido.get('data_filtro', '')]['faturamento'] += float(pedido.get('total', 0) or 0)
        cliente = pedido.get('cliente', {}).get('nome', 'Cliente')
        telefone = pedido.get('cliente', {}).get('telefone', '')
        chave = (cliente, telefone)
        if chave not in devedores:
            devedores[chave] = {'cliente': cliente, 'telefone': telefone, 'total': 0.0, 'pago': 0.0, 'nao_pago': 0.0, 'pedidos': 0}
        devedores[chave]['total'] += float(pedido.get('total', 0) or 0)
        devedores[chave]['pedidos'] += 1
        if pedido.get('pagamento_status') == 'pago':
            devedores[chave]['pago'] += float(pedido.get('total', 0) or 0)
        elif pedido.get('pagamento_status') == 'aguardando_pagamento':
            devedores[chave]['nao_pago'] += float(pedido.get('total', 0) or 0)
        for item in pedido.get('itens', []):
            nome = item.get('nome', 'Sem nome')
            qtd = int(item.get('quantidade', 0) or 0)
            subtotal = float(item.get('subtotal', 0) or 0)
            sabores[nome] = sabores.get(nome, 0) + qtd
            faturamento_sabor[nome] = faturamento_sabor.get(nome, 0.0) + subtotal

    ranking_sabores = [
        {'nome': nome, 'quantidade': quantidade, 'faturamento': faturamento_sabor.get(nome, 0.0)}
        for nome, quantidade in sorted(sabores.items(), key=lambda x: x[1], reverse=True)
    ]
    max_qtd = max([item['quantidade'] for item in ranking_sabores], default=1)
    for item in ranking_sabores:
        item['percentual'] = round((item['quantidade'] / max_qtd) * 100, 1) if max_qtd else 0

    pedidos_dia_lista = [
        {'data': data, 'pedidos': info['pedidos'], 'faturamento': round(info['faturamento'], 2)}
        for data, info in sorted(pedidos_por_dia.items())
    ]

    saldo_devedor = [
        {**info, 'saldo_devedor': round(info['nao_pago'], 2)}
        for info in devedores.values() if info['nao_pago'] > 0
    ]
    saldo_devedor.sort(key=lambda x: x['saldo_devedor'], reverse=True)

    return render_template(
        'admin_analise.html',
        total_pedidos=total_pedidos,
        total_pago=total_pago,
        total_pendente=total_pendente,
        faturamento_bruto=faturamento_bruto,
        ticket_medio=ticket_medio,
        comparativo=comparativo,
        ranking_sabores=ranking_sabores,
        pedidos_dia_lista=pedidos_dia_lista,
        saldo_devedor=saldo_devedor,
        pedidos_filtrados=filtrados,
        periodo=periodo,
        data_inicial=data_inicial,
        data_final=data_final,
        responsavel=responsavel,
        pagamento=pagamento,
        incluir_ocultos=incluir_ocultos,
        request_path=request.full_path if request.query_string else request.path,
    )


@app.route("/admin/excluir-analise/<int:pedido_id>", methods=['POST'])
def excluir_pedido_analise(pedido_id):
    if not admin_logado():
        return redirect('/admin/login')
    pedido = buscar_pedido(pedido_id)
    if pedido and pedido.get('pagamento_status') != 'pago' and not pedido.get('estoque_devolvido'):
        restaurar_estoque_do_pedido(pedido)
    if excluir_pedido_db(pedido_id):
        set_mensagem('mensagem_admin', f'Pedido #{pedido_id} excluído permanentemente.')
    return redirect_admin_back('/admin/analise')


@app.route("/admin/ranking")
def admin_ranking():
    if not admin_logado():
        return redirect("/admin/login")

    pedidos = [p for p in ler_pedidos() if not p.get("oculto", False)]
    sabores_vendidos = {}
    faturamento_por_sabor = {}
    for pedido in pedidos:
        for item in pedido.get("itens", []):
            nome = item.get("nome", "Sem nome")
            quantidade = int(item.get("quantidade", 0) or 0)
            subtotal = float(item.get("subtotal", 0) or 0)
            sabores_vendidos[nome] = sabores_vendidos.get(nome, 0) + quantidade
            faturamento_por_sabor[nome] = faturamento_por_sabor.get(nome, 0.0) + subtotal

    ranking = []
    max_qtd = max(sabores_vendidos.values(), default=1)
    for nome, quantidade in sorted(sabores_vendidos.items(), key=lambda x: x[1], reverse=True):
        ranking.append({
            "nome": nome,
            "quantidade": quantidade,
            "faturamento": faturamento_por_sabor.get(nome, 0.0),
            "percentual": round((quantidade / max_qtd) * 100, 1) if max_qtd else 0,
        })

    return render_template("admin_ranking.html", ranking=ranking, total_sabores=len(ranking), total_itens=sum(sabores_vendidos.values()))


@app.route("/admin/configuracoes", methods=["POST"])
def admin_configuracoes():
    if not admin_logado():
        return redirect("/admin/login")

    config = ler_config()
    config["mensagem_loja_fechada"] = request.form.get("mensagem_loja_fechada", "").strip() or configuracao_padrao()["mensagem_loja_fechada"]
    config["infinitepay_ativo"] = request.form.get("infinitepay_ativo") == "on"
    config["bloquear_italo"] = request.form.get("bloquear_italo") == "on"
    config["bloquear_karina"] = request.form.get("bloquear_karina") == "on"
    salvar_config(config)
    set_mensagem("mensagem_admin", "Configurações atualizadas com sucesso.")
    return redirect_admin_back("/admin")


@app.route("/admin/pedido/<int:pedido_id>/editar")
def admin_editar_pedido(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    pedido = obter_pedido_db(pedido_id)
    if not pedido:
        set_mensagem("mensagem_admin", "Pedido não encontrado.")
        return redirect("/admin")
    destinatario = normalizar_destinatario(pedido.get("destinatario", "italo"))
    sabores_disponiveis = []
    for sabor in ler_sabores():
        if not sabor_ativo_para_destinatario(sabor, destinatario):
            continue
        estoque_disp = estoque_para_destinatario(sabor, destinatario)
        if estoque_disp <= 0:
            continue
        sabores_disponiveis.append({
            "nome": sabor.get("nome"),
            "preco": float(sabor.get("preco", 0) or 0),
            "estoque": estoque_disp,
        })
    return render_template(
        "admin_pedido_editar.html",
        pedido=pedido,
        sabores_disponiveis=sabores_disponiveis,
        mensagem=pop_mensagem("mensagem_admin"),
        request_path=request.full_path if request.query_string else request.path,
    )


@app.route("/admin/pedido/<int:pedido_id>/salvar", methods=["POST"])
def admin_salvar_edicao_pedido(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    pedido = obter_pedido_db(pedido_id)
    if not pedido:
        set_mensagem("mensagem_admin", "Pedido não encontrado.")
        return redirect("/admin")

    cliente_nome = request.form.get("cliente_nome", pedido.get("cliente", {}).get("nome", "")).strip()
    cliente_telefone = request.form.get("cliente_telefone", pedido.get("cliente", {}).get("telefone", "")).strip()
    cliente_endereco = request.form.get("cliente_endereco", pedido.get("cliente", {}).get("endereco", "")).strip()
    quantidades = {}
    for item in pedido.get("itens", []):
        chave = f"quantidade_{item['id']}"
        valor = request.form.get(chave, str(item.get("quantidade", 0)))
        try:
            quantidades[str(item["id"])] = max(0, int(valor))
        except (TypeError, ValueError):
            quantidades[str(item["id"])] = int(item.get("quantidade", 0) or 0)
    novo_item_nome = request.form.get("novo_item_nome", "").strip()
    novo_item_quantidade = request.form.get("novo_item_quantidade", "0")
    try:
        atualizar_pedido_edicao_db(pedido_id, cliente_nome, cliente_telefone, cliente_endereco, quantidades, novo_item_nome, novo_item_quantidade)
        set_mensagem("mensagem_admin", f"Pedido #{pedido_id} atualizado com sucesso.")
    except Exception as e:
        set_mensagem("mensagem_admin", str(e))
    return redirect(f"/admin/pedido/{pedido_id}/editar")


@app.route("/admin/loja/toggle", methods=["POST"])
def admin_toggle_loja():
    if not admin_logado():
        return redirect("/admin/login")
    config = ler_config()
    config["loja_aberta"] = not config.get("loja_aberta", True)
    salvar_config(config)
    status = "aberta" if config["loja_aberta"] else "fechada"
    set_mensagem("mensagem_admin", f"Loja {status} com sucesso.")
    return redirect_admin_back("/admin")


@app.route("/admin/pago/<int:pedido_id>", methods=["POST"])
def marcar_pago(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    atualizar_pedido_db(pedido_id, pagamento_status="pago")
    return redirect_admin_back("/admin")


@app.route("/admin/nao_pago/<int:pedido_id>", methods=["POST"])
def marcar_nao_pago(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    atualizar_pedido_db(pedido_id, pagamento_status="aguardando_pagamento")
    return redirect_admin_back("/admin")


@app.route("/admin/entregue/<int:pedido_id>", methods=["POST"])
def marcar_entregue(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    atualizar_pedido_db(pedido_id, status="entregue")
    return redirect_admin_back("/admin")


@app.route("/admin/pendente/<int:pedido_id>", methods=["POST"])
def marcar_pendente(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    atualizar_pedido_db(pedido_id, status="pendente")
    return redirect_admin_back("/admin")


@app.route("/admin/cancelar/<int:pedido_id>", methods=["POST"])
def cancelar_pedido(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    pedido = buscar_pedido(pedido_id)
    if not pedido:
        set_mensagem("mensagem_admin", "Pedido não encontrado.")
        return redirect("/admin")
    atualizar_pedido_db(pedido_id, status="cancelado", pagamento_status="cancelado")
    if pedido.get("pagamento_status") != "pago":
        restaurar_estoque_do_pedido(pedido)
    set_mensagem("mensagem_admin", f"Pedido #{pedido_id} cancelado.")
    return redirect_admin_back("/admin")


@app.route("/admin/ocultar/<int:pedido_id>", methods=["POST"])
def ocultar_pedido(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    pedido = buscar_pedido(pedido_id)
    if not pedido:
        set_mensagem("mensagem_admin", "Pedido não encontrado.")
        return redirect("/admin")
    atualizar_pedido_db(pedido_id, oculto=True, ocultado_em=now_local())
    set_mensagem("mensagem_admin", f"Pedido #{pedido_id} ocultado do painel.")
    return redirect_admin_back("/admin")


@app.route("/admin/reexibir/<int:pedido_id>", methods=["POST"])
def reexibir_pedido(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    pedido = buscar_pedido(pedido_id)
    if not pedido:
        set_mensagem("mensagem_admin", "Pedido não encontrado.")
        return redirect("/admin")
    atualizar_pedido_db(pedido_id, oculto=False, ocultado_em=None)
    set_mensagem("mensagem_admin", f"Pedido #{pedido_id} voltou a aparecer no painel.")
    return redirect_admin_back("/admin")


@app.route("/admin/notificacoes")
def admin_notificacoes():
    if not admin_logado():
        return jsonify({"erro": "não autorizado"}), 401
    try:
        ultimo_visto = int(request.args.get("ultimo_id", 0) or 0)
    except ValueError:
        ultimo_visto = 0
    novos = []
    for pedido in ler_pedidos():
        pedido_id = int(pedido.get("id", 0) or 0)
        if pedido_id > ultimo_visto and not pedido.get("oculto", False):
            novos.append({
                "id": pedido_id,
                "cliente": pedido.get("cliente", {}).get("nome", "Cliente"),
                "total": float(pedido.get("total", 0) or 0),
                "vendedor": pedido.get("nome_vendedor", "Italo"),
                "data": pedido.get("data", ""),
            })
    novos.sort(key=lambda item: item["id"])
    return jsonify({
        "novos": novos,
        "ultimo_id": max([ultimo_visto] + [item["id"] for item in novos]),
    })




def excluir_pedido_db(pedido_id):
    if not db_enabled():
        pedidos = ler_json(ARQUIVO_PEDIDOS, [])
        novos = [p for p in pedidos if int(p.get("id", 0) or 0) != int(pedido_id)]
        if len(novos) == len(pedidos):
            return False
        salvar_json(ARQUIVO_PEDIDOS, novos)
        return True
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pedidos WHERE id = %s", (int(pedido_id),))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


@app.route("/admin/excluir/<int:pedido_id>", methods=["POST"])
def excluir_pedido(pedido_id):
    if not admin_logado():
        return redirect("/admin/login")
    pedido = buscar_pedido(pedido_id)
    if not pedido:
        set_mensagem("mensagem_admin", "Pedido não encontrado.")
        return redirect("/admin")
    if pedido.get("pagamento_status") != "pago" and not pedido.get("estoque_devolvido"):
        restaurar_estoque_do_pedido(pedido)
    if excluir_pedido_db(pedido_id):
        set_mensagem("mensagem_admin", f"Pedido #{pedido_id} excluído permanentemente.")
    else:
        set_mensagem("mensagem_admin", "Não foi possível excluir o pedido.")
    return redirect_admin_back("/admin")


@app.route("/pedido/<int:pedido_id>/pagar")
def pagar_pedido(pedido_id):
    pedido = buscar_pedido(pedido_id)
    if not pedido or not pedido.get("pagamento_link"):
        set_mensagem("mensagem_home", "Link de pagamento não encontrado para este pedido.")
        return redirect("/")
    return redirect(pedido.get("pagamento_link"))


def montar_pedido_rapido_admin(cliente_nome, cliente_telefone, cliente_endereco, destinatario, pagamento_status, status, itens):
    agora = now_local()
    pedido_id = int(agora.timestamp() * 1000)
    total = sum(money(item["subtotal"]) for item in itens)
    return {
        "id": pedido_id,
        "data": agora.strftime("%d/%m/%Y %H:%M"),
        "data_filtro": agora.strftime("%Y-%m-%d"),
        "cliente": {"nome": cliente_nome, "telefone": cliente_telefone, "endereco": cliente_endereco},
        "itens": itens,
        "total": float(money(total)),
        "status": status,
        "pagamento_status": pagamento_status,
        "destinatario": destinatario,
        "nome_vendedor": get_nome_vendedor(destinatario),
        "pagamento_link": "",
        "receipt_url": "",
        "transaction_nsu": "",
        "invoice_slug": "",
        "capture_method": "",
        "preference_id": "",
        "payment_id": "",
        "payment_method": "pedido_rapido_admin",
        "payment_detail": "Pedido rápido criado pelo painel administrativo sem reserva de estoque",
        "estoque_devolvido": False,
        "oculto": False,
        "ocultado_em": "",
    }


@app.route("/admin/pedido_rapido")
def admin_pedido_rapido():
    if not admin_logado():
        return redirect("/admin/login")
    sabores = []
    for sabor in ler_sabores():
        sabores.append({
            "nome": sabor.get("nome"),
            "preco": float(sabor.get("preco", 0) or 0),
            "ativo_italo": bool(sabor.get("ativo_italo", sabor.get("disponivel", True))),
            "ativo_karina": bool(sabor.get("ativo_karina", sabor.get("disponivel", True))),
        })
    return render_template(
        "admin_pedido_rapido.html",
        sabores=sabores,
        mensagem=pop_mensagem("mensagem_admin"),
        request_path=request.full_path if request.query_string else request.path,
    )


@app.route("/admin/pedido_rapido/criar", methods=["POST"])
def admin_pedido_rapido_criar():
    if not admin_logado():
        return redirect("/admin/login")

    cliente_nome = request.form.get("cliente_nome", "").strip() or "Cliente balcão"
    cliente_telefone = request.form.get("cliente_telefone", "").strip()
    cliente_endereco = request.form.get("cliente_endereco", "").strip()
    destinatario = normalizar_destinatario(request.form.get("destinatario", "italo"))
    pagamento_status = str(request.form.get("pagamento_status", "aguardando_pagamento") or "aguardando_pagamento").strip().lower()
    status = str(request.form.get("status", "pendente") or "pendente").strip().lower()

    nomes = request.form.getlist("item_nome[]")
    quantidades = request.form.getlist("item_quantidade[]")
    sabores_db = {s.get("nome"): s for s in ler_sabores()}
    itens = []
    for nome, quantidade in zip(nomes, quantidades):
        nome = str(nome or "").strip()
        if not nome:
            continue
        try:
            qtd = max(0, int(quantidade or 0))
        except (TypeError, ValueError):
            qtd = 0
        if qtd <= 0:
            continue
        sabor = sabores_db.get(nome)
        if not sabor:
            continue
        preco = float(sabor.get("preco", 0) or 0)
        itens.append({
            "nome": nome,
            "quantidade": qtd,
            "preco_unitario": preco,
            "subtotal": float(money(Decimal(str(preco)) * Decimal(qtd))),
        })

    if not itens:
        set_mensagem("mensagem_admin", "Adicione pelo menos um item válido no pedido rápido.")
        return redirect("/admin/pedido_rapido")

    pedido = montar_pedido_rapido_admin(
        cliente_nome=cliente_nome,
        cliente_telefone=cliente_telefone,
        cliente_endereco=cliente_endereco,
        destinatario=destinatario,
        pagamento_status=pagamento_status if pagamento_status in {"aguardando_pagamento", "pago", "cancelado"} else "aguardando_pagamento",
        status=status if status in {"pendente", "entregue", "cancelado"} else "pendente",
        itens=itens,
    )
    criar_pedido_db(pedido)
    set_mensagem("mensagem_admin", f"Pedido rápido #{pedido['id']} criado com sucesso.")
    return redirect("/admin")


@app.route("/admin/exportar_excel")
def exportar_excel():
    if not admin_logado():
        return redirect("/admin/login")
    pedidos = ler_pedidos()
    return render_template(
        "admin_export_excel.html",
        total_pedidos=len(pedidos),
        request_path=request.full_path if request.query_string else request.path,
    )


@app.route("/admin/exportar_excel/baixar")
def exportar_excel_baixar():
    if not admin_logado():
        return redirect("/admin/login")
    pedidos = ler_pedidos()
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Pedidos"
    ws.append([
        "ID Pedido", "Data", "Status Entrega", "Status Pagamento", "Cliente", "Telefone", "Endereço",
        "Vendedor", "Item", "Quantidade", "Preço Unitário", "Subtotal Item", "Total Pedido",
        "Link Pagamento", "Payment ID", "Método", "Detalhe"
    ])
    for pedido in pedidos:
        itens = pedido.get("itens", []) or [{}]
        for item in itens:
            ws.append([
                pedido.get("id", ""), pedido.get("data", ""), pedido.get("status", ""), pedido.get("pagamento_status", ""),
                pedido.get("cliente", {}).get("nome", ""), pedido.get("cliente", {}).get("telefone", ""), pedido.get("cliente", {}).get("endereco", ""),
                pedido.get("nome_vendedor", ""), item.get("nome", ""), item.get("quantidade", ""), item.get("preco_unitario", ""), item.get("subtotal", ""),
                pedido.get("total", 0), pedido.get("pagamento_link", ""), pedido.get("payment_id", ""), pedido.get("payment_method", ""), pedido.get("payment_detail", "")
            ])
    arquivo = BytesIO()
    wb.save(arquivo)
    arquivo.seek(0)
    return send_file(
        arquivo,
        as_attachment=True,
        download_name=f"pedidos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/sabores/adicionar", methods=["POST"])
def admin_adicionar_sabor():
    if not admin_logado():
        return redirect("/admin/login")
    nome = request.form.get("nome", "").strip()
    preco = request.form.get("preco", type=float)
    img = request.form.get("img", "").strip()
    estoque_italo = request.form.get("estoque_italo", type=int)
    estoque_karina = request.form.get("estoque_karina", type=int)
    if not nome or preco is None or not img or estoque_italo is None or estoque_karina is None or estoque_italo < 0 or estoque_karina < 0:
        set_mensagem("mensagem_admin", "Preencha nome, preço, imagem e os estoques corretamente.")
        return redirect("/admin")
    if buscar_sabor_por_nome(nome):
        set_mensagem("mensagem_admin", "Já existe um sabor com esse nome.")
        return redirect("/admin")
    inserir_sabor({
        "id": next_sabor_id(),
        "nome": nome,
        "preco": preco,
        "img": img,
        "disponivel": True,
        "ativo_italo": True,
        "ativo_karina": True,
        "estoque": estoque_italo + estoque_karina,
        "estoque_italo": estoque_italo,
        "estoque_karina": estoque_karina,
    })
    set_mensagem("mensagem_admin", "Sabor adicionado com sucesso.")
    return redirect_admin_back("/admin/sabores")


@app.route("/admin/sabores/estoque/<int:sabor_id>", methods=["POST"])
def admin_atualizar_estoque(sabor_id):
    if not admin_logado():
        return redirect("/admin/login")
    try:
        estoque_italo = int(str(request.form.get("estoque_italo", "")).strip())
        estoque_karina = int(str(request.form.get("estoque_karina", "")).strip())
    except ValueError:
        set_mensagem("mensagem_admin", "Informe estoques válidos para Italo e Karina.")
        return redirect_admin_back("/admin/sabores")
    if estoque_italo < 0 or estoque_karina < 0:
        set_mensagem("mensagem_admin", "Os estoques não podem ser negativos.")
        return redirect_admin_back("/admin/sabores")
    atualizar_sabor(sabor_id, estoque_italo=estoque_italo, estoque_karina=estoque_karina, estoque=estoque_italo + estoque_karina)
    set_mensagem("mensagem_admin", "Estoques atualizados com sucesso.")
    return redirect_admin_back("/admin/sabores")


@app.route("/admin/sabores/toggle/<int:sabor_id>", methods=["POST"])
def admin_toggle_sabor(sabor_id):
    if not admin_logado():
        return redirect("/admin/login")
    sabor = next((s for s in ler_sabores() if s.get("id") == sabor_id), None)
    if not sabor:
        set_mensagem("mensagem_admin", "Sabor não encontrado.")
        return redirect_admin_back("/admin/sabores")
    atualizar_sabor(sabor_id, disponivel=not sabor.get("disponivel", True))
    set_mensagem("mensagem_admin", "Disponibilidade do sabor atualizada.")
    return redirect_admin_back("/admin/sabores")


@app.route("/admin/sabores/toggle-destinatario/<int:sabor_id>", methods=["POST"])
def admin_toggle_sabor_destinatario(sabor_id):
    if not admin_logado():
        return redirect("/admin/login")
    destinatario = normalizar_destinatario(request.form.get("destinatario", "italo"))
    sabor = next((s for s in ler_sabores() if s.get("id") == sabor_id), None)
    if not sabor:
        set_mensagem("mensagem_admin", "Sabor não encontrado.")
        return redirect_admin_back("/admin/sabores")
    campo = ativo_campo_destinatario(destinatario)
    novo_status = not bool(sabor.get(campo, sabor.get("disponivel", True)))
    atualizar_sabor(sabor_id, **{campo: novo_status})
    nome_resp = get_nome_vendedor(destinatario)
    set_mensagem("mensagem_admin", f"Exibição do sabor para {nome_resp} atualizada com sucesso.")
    return redirect_admin_back("/admin/sabores")


@app.route("/admin/sabores/excluir/<int:sabor_id>", methods=["POST"])
def admin_excluir_sabor(sabor_id):
    if not admin_logado():
        return redirect("/admin/login")
    excluir_sabor_db(sabor_id)
    set_mensagem("mensagem_admin", "Sabor excluído com sucesso.")
    return redirect_admin_back("/admin/sabores")


if db_enabled():
    ensure_database()
    migrate_json_to_db_once()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)