"""Migração única e idempotente do Neon para o PostgreSQL do Railway."""

import os
import sys

import psycopg
from psycopg import sql


SOURCE_URL = os.environ.get("NEON_DATABASE_URL", "").strip()
DESTINATION_URL = os.environ.get("DATABASE_URL", "").strip()
MIGRATION_NAME = "neon_to_railway_v1"

TABLES = (
    "app_config",
    "sabores",
    "clientes",
    "pedidos",
    "pedido_itens",
    "pagamentos_log",
    "recuperacoes_senha",
)


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def row_count(conn, table):
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        return int(cur.fetchone()[0])


def migration_completed(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_history (
                name TEXT PRIMARY KEY,
                completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("SELECT 1 FROM migration_history WHERE name = %s", (MIGRATION_NAME,))
        return cur.fetchone() is not None


def reset_sequence(conn, table, column="id"):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f"public.{table}", column))
        sequence = cur.fetchone()[0]
        if not sequence:
            return
        cur.execute(
            sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {}").format(
                sql.Identifier(column), sql.Identifier(table)
            )
        )
        maximum = int(cur.fetchone()[0])
        if maximum > 0:
            cur.execute("SELECT setval(%s, %s, TRUE)", (sequence, maximum))
        else:
            cur.execute("SELECT setval(%s, 1, FALSE)", (sequence,))


def migrate():
    if not SOURCE_URL or not DESTINATION_URL:
        raise RuntimeError("NEON_DATABASE_URL e DATABASE_URL são obrigatórias.")
    if SOURCE_URL == DESTINATION_URL:
        raise RuntimeError("Origem e destino não podem ser o mesmo banco.")

    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_init.sql")
    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema_sql = schema_file.read()

    with psycopg.connect(SOURCE_URL, connect_timeout=30) as source:
        with psycopg.connect(DESTINATION_URL, connect_timeout=30) as destination:
            with destination.cursor() as cur:
                cur.execute(schema_sql)
            destination.commit()

            if migration_completed(destination):
                destination.commit()
                print("Migração já concluída anteriormente. Nenhuma alteração necessária.")
                return

            destination_counts = {
                table: row_count(destination, table)
                for table in TABLES
            }
            non_empty = {
                table: count
                for table, count in destination_counts.items()
                if count > 0
            }
            if non_empty:
                raise RuntimeError(
                    "O banco Railway já contém dados e a migração foi interrompida: "
                    + ", ".join(f"{table}={count}" for table, count in non_empty.items())
                )

            copied = {}
            for table in TABLES:
                source_columns = table_columns(source, table)
                destination_columns = set(table_columns(destination, table))
                columns = [column for column in source_columns if column in destination_columns]
                if not columns:
                    copied[table] = 0
                    continue

                select_query = sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.Identifier(table),
                )
                copy_query = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                )

                count = 0
                with source.cursor() as source_cur, destination.cursor() as destination_cur:
                    source_cur.execute(select_query)
                    with destination_cur.copy(copy_query) as copy:
                        for row in source_cur:
                            copy.write_row(row)
                            count += 1
                copied[table] = count

            for table in ("clientes", "pedido_itens", "pagamentos_log", "recuperacoes_senha"):
                reset_sequence(destination, table)

            for table, expected in copied.items():
                actual = row_count(destination, table)
                if actual != expected:
                    raise RuntimeError(
                        f"Validação falhou em {table}: origem={expected}, destino={actual}."
                    )

            with destination.cursor() as cur:
                cur.execute(
                    "INSERT INTO migration_history (name) VALUES (%s)",
                    (MIGRATION_NAME,),
                )
            destination.commit()

            print("Migração Neon → Railway concluída e validada.")
            for table, count in copied.items():
                print(f"{table}: {count} registro(s)")


if __name__ == "__main__":
    try:
        migrate()
    except Exception as exc:
        print(f"ERRO NA MIGRAÇÃO: {exc}", file=sys.stderr)
        raise
