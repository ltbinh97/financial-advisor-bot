"""Storage layer — PostgreSQL schema + repository functions.

The AgentBase runtime is stateless and may autoscale, so all financial state
lives in an external Postgres database (DATABASE_URL).
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor

import config

logger = logging.getLogger("pfm.db")

_pool: ThreadedConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    amount      NUMERIC(16,2) NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('income','expense')),
    merchant    TEXT,
    category    TEXT NOT NULL DEFAULT 'khac',
    note        TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    raw         TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tx_user_ts ON transactions(user_id, ts);

CREATE TABLE IF NOT EXISTS budgets (
    user_id   TEXT NOT NULL,
    category  TEXT NOT NULL,
    period    TEXT NOT NULL DEFAULT 'monthly',
    amount    NUMERIC(16,2) NOT NULL,
    PRIMARY KEY (user_id, category, period)
);

CREATE TABLE IF NOT EXISTS goals (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    target_amount NUMERIC(16,2) NOT NULL,
    saved_amount  NUMERIC(16,2) NOT NULL DEFAULT 0,
    deadline      DATE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id        BIGSERIAL PRIMARY KEY,
    user_id   TEXT NOT NULL,
    alert_key TEXT NOT NULL,
    tier      TEXT NOT NULL,
    sent_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_user_key ON alerts_log(user_id, alert_key, sent_at);

CREATE TABLE IF NOT EXISTS settings (
    user_id        TEXT PRIMARY KEY,
    monthly_income NUMERIC(16,2),
    report_freq    TEXT NOT NULL DEFAULT 'weekly'
);
"""


def init_db() -> None:
    """Create the connection pool and ensure the schema exists."""
    global _pool
    if _pool is None:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured")
        # keepalives keep idle connections alive against serverless Postgres
        # (Neon) which drops idle SSL connections.
        _pool = ThreadedConnectionPool(
            1, 8, dsn=config.DATABASE_URL,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
            connect_timeout=10,
        )
        logger.info("DB pool created")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    logger.info("DB schema ready")


@contextmanager
def get_conn():
    assert _pool is not None, "init_db() must be called first"
    conn = _pool.getconn()
    broken = False
    try:
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Stale/closed connection (e.g. Neon dropped it) — drop it from the pool.
        broken = True
        raise
    finally:
        try:
            _pool.putconn(conn, close=broken)
        except Exception:
            logger.warning("putconn failed", exc_info=True)


def _q(sql: str, params=(), fetch: str | None = None, _retries: int = 3):
    """Run a query, retrying with a fresh connection if a pooled one is stale."""
    last_err = None
    for attempt in range(_retries):
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, params)
                    out = None
                    if fetch == "one":
                        out = cur.fetchone()
                    elif fetch == "all":
                        out = cur.fetchall()
                conn.commit()
            return out
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last_err = e
            logger.warning("DB op failed (attempt %d/%d), retrying with fresh connection: %s",
                           attempt + 1, _retries, e)
    raise last_err


def ping() -> bool:
    try:
        _q("SELECT 1", fetch="one")
        return True
    except Exception:
        logger.exception("DB ping failed")
        return False


# --- Transactions ---------------------------------------------------------

def add_transaction(user_id, amount, ttype, merchant=None, category="khac",
                    note=None, source="manual", raw=None, ts=None):
    row = _q(
        """INSERT INTO transactions (user_id, ts, amount, type, merchant, category, note, source, raw)
           VALUES (%s, COALESCE(%s, now()), %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, ts, amount, type, merchant, category, note, source""",
        (user_id, ts, amount, ttype, merchant, category, note, source, raw),
        fetch="one",
    )
    return row


def find_duplicate(user_id, amount, ttype, merchant, ts=None):
    """Find a likely-duplicate transaction: same user/amount/type/merchant on the
    same calendar day (when a date is known) or within the last 24h otherwise.
    Used to suppress re-ingested receipts/bank SMS."""
    if ts is not None:
        return _q(
            """SELECT id, ts, source FROM transactions
               WHERE user_id=%s AND type=%s AND amount=%s
                 AND COALESCE(lower(merchant),'')=COALESCE(lower(%s),'')
                 AND DATE(ts)=DATE(%s)
               ORDER BY ts DESC LIMIT 1""",
            (user_id, ttype, amount, merchant, ts), fetch="one")
    since = datetime.utcnow() - timedelta(hours=24)
    return _q(
        """SELECT id, ts, source FROM transactions
           WHERE user_id=%s AND type=%s AND amount=%s
             AND COALESCE(lower(merchant),'')=COALESCE(lower(%s),'')
             AND ts>=%s
           ORDER BY ts DESC LIMIT 1""",
        (user_id, ttype, amount, merchant, since), fetch="one")


def list_transactions(user_id, since=None, limit=200):
    if since:
        return _q(
            "SELECT * FROM transactions WHERE user_id=%s AND ts>=%s ORDER BY id DESC LIMIT %s",
            (user_id, since, limit), fetch="all")
    return _q("SELECT * FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT %s",
              (user_id, limit), fetch="all")


def get_last_transaction(user_id):
    # "gần nhất" = most recently ENTERED (insertion order = id), not the business
    # date (ts) — an OCR receipt can carry a past date but be entered just now.
    return _q("SELECT * FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT 1",
              (user_id,), fetch="one")


def delete_transaction(user_id, tx_id):
    """Delete one transaction owned by the user. Returns the deleted id or None."""
    return _q("DELETE FROM transactions WHERE id=%s AND user_id=%s RETURNING id",
              (tx_id, user_id), fetch="one")


def category_totals(user_id, since, ttype="expense"):
    """Sum amount per category since a timestamp."""
    rows = _q(
        """SELECT category, SUM(amount) AS total, COUNT(*) AS n
           FROM transactions
           WHERE user_id=%s AND type=%s AND ts>=%s
           GROUP BY category ORDER BY total DESC""",
        (user_id, ttype, since), fetch="all")
    return rows or []


def total_amount(user_id, since, ttype):
    row = _q(
        "SELECT COALESCE(SUM(amount),0) AS total FROM transactions WHERE user_id=%s AND type=%s AND ts>=%s",
        (user_id, ttype, since), fetch="one")
    return float(row["total"]) if row else 0.0


def category_avg_30d(user_id, category):
    """Average single-transaction amount for a category over the last 30 days."""
    since = datetime.utcnow() - timedelta(days=30)
    row = _q(
        """SELECT AVG(amount) AS avg_amt FROM transactions
           WHERE user_id=%s AND category=%s AND type='expense' AND ts>=%s""",
        (user_id, category, since), fetch="one")
    return float(row["avg_amt"]) if row and row["avg_amt"] is not None else None


# --- Budgets --------------------------------------------------------------

def set_budget(user_id, category, amount, period="monthly"):
    _q("""INSERT INTO budgets (user_id, category, period, amount)
          VALUES (%s,%s,%s,%s)
          ON CONFLICT (user_id, category, period)
          DO UPDATE SET amount=EXCLUDED.amount""",
       (user_id, category, period, amount))


def get_budgets(user_id, period="monthly"):
    return _q("SELECT * FROM budgets WHERE user_id=%s AND period=%s",
              (user_id, period), fetch="all") or []


def get_budget(user_id, category, period="monthly"):
    return _q("SELECT * FROM budgets WHERE user_id=%s AND category=%s AND period=%s",
              (user_id, category, period), fetch="one")


# --- Goals ----------------------------------------------------------------

def add_goal(user_id, name, target_amount, deadline=None):
    return _q("""INSERT INTO goals (user_id, name, target_amount, deadline)
                 VALUES (%s,%s,%s,%s) RETURNING *""",
              (user_id, name, target_amount, deadline), fetch="one")


def list_goals(user_id):
    return _q("SELECT * FROM goals WHERE user_id=%s ORDER BY created_at", (user_id,), fetch="all") or []


def add_to_goal(user_id, goal_id, amount):
    return _q("""UPDATE goals SET saved_amount = saved_amount + %s
                 WHERE id=%s AND user_id=%s RETURNING *""",
              (amount, goal_id, user_id), fetch="one")


# --- Settings -------------------------------------------------------------

def get_settings(user_id):
    row = _q("SELECT * FROM settings WHERE user_id=%s", (user_id,), fetch="one")
    if not row:
        _q("INSERT INTO settings (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        row = _q("SELECT * FROM settings WHERE user_id=%s", (user_id,), fetch="one")
    return row


def set_monthly_income(user_id, amount):
    _q("""INSERT INTO settings (user_id, monthly_income) VALUES (%s,%s)
          ON CONFLICT (user_id) DO UPDATE SET monthly_income=EXCLUDED.monthly_income""",
       (user_id, amount))


def list_active_users():
    rows = _q("SELECT DISTINCT user_id FROM transactions", fetch="all") or []
    return [r["user_id"] for r in rows]


# --- Alerts log (dedupe / cooldown) ---------------------------------------

def alert_recently_sent(user_id, alert_key, hours):
    since = datetime.utcnow() - timedelta(hours=hours)
    row = _q(
        "SELECT 1 FROM alerts_log WHERE user_id=%s AND alert_key=%s AND sent_at>=%s LIMIT 1",
        (user_id, alert_key, since), fetch="one")
    return row is not None


def log_alert(user_id, alert_key, tier):
    _q("INSERT INTO alerts_log (user_id, alert_key, tier) VALUES (%s,%s,%s)",
       (user_id, alert_key, tier))
