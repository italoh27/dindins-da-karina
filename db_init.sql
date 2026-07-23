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

CREATE TABLE IF NOT EXISTS clientes (
    id BIGSERIAL PRIMARY KEY,
    nome VARCHAR(150) NOT NULL,
    telefone VARCHAR(40) NOT NULL UNIQUE,
    email VARCHAR(180) NOT NULL DEFAULT '',
    senha_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS oculto BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS ocultado_em TIMESTAMP NULL;
ALTER TABLE sabores ADD COLUMN IF NOT EXISTS estoque_italo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sabores ADD COLUMN IF NOT EXISTS estoque_karina INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sabores ADD COLUMN IF NOT EXISTS ativo_italo BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE sabores ADD COLUMN IF NOT EXISTS ativo_karina BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_pedido_itens_pedido_id ON pedido_itens (pedido_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_data_filtro ON pedidos (data_filtro DESC);
CREATE INDEX IF NOT EXISTS idx_pedidos_destinatario_data ON pedidos (destinatario, data_filtro DESC);
CREATE INDEX IF NOT EXISTS idx_pedidos_pagamento_data ON pedidos (pagamento_status, data_filtro DESC);
CREATE INDEX IF NOT EXISTS idx_pedidos_visiveis_id ON pedidos (id DESC) WHERE oculto = FALSE;
CREATE INDEX IF NOT EXISTS idx_pagamentos_log_pedido_id ON pagamentos_log (pedido_id);
CREATE INDEX IF NOT EXISTS idx_pagamentos_log_payment_id ON pagamentos_log (payment_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_clientes_telefone ON clientes (telefone);
