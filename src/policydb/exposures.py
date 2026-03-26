"""Exposure-policy linkage: CRUD, rate calculation, find-or-create."""
from datetime import datetime, timezone


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _calc_rate(premium, amount, denominator):
    """Calculate rate = premium / (amount / denominator). Returns None if inputs invalid."""
    if not premium or not amount or amount == 0 or denominator == 0:
        return None
    return premium / (amount / denominator)


def create_exposure_link(conn, policy_uid, exposure_id, *, is_primary=False):
    """Create a link between a policy and an exposure row. Returns the link dict.
    If the link already exists, updates is_primary and recalculates rate."""
    if is_primary:
        # Clear any existing primary for this policy
        conn.execute(
            "UPDATE policy_exposure_links SET is_primary=0 WHERE policy_uid=? AND is_primary=1",
            (policy_uid,),
        )
    # Get premium and exposure data for rate calc
    pol = conn.execute("SELECT premium FROM policies WHERE policy_uid=?", (policy_uid,)).fetchone()
    exp = conn.execute("SELECT amount, denominator FROM client_exposures WHERE id=?", (exposure_id,)).fetchone()
    rate = _calc_rate(
        pol["premium"] if pol else None,
        exp["amount"] if exp else None,
        exp["denominator"] if exp else 1,
    )
    now = _utcnow()
    existing = conn.execute(
        "SELECT id FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE policy_exposure_links SET is_primary=?, rate=?, rate_updated_at=?
               WHERE policy_uid=? AND exposure_id=?""",
            (1 if is_primary else 0, rate, now if rate is not None else None, policy_uid, exposure_id),
        )
    else:
        conn.execute(
            """INSERT INTO policy_exposure_links (policy_uid, exposure_id, is_primary, rate, rate_updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (policy_uid, exposure_id, 1 if is_primary else 0, rate, now if rate is not None else None),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    ).fetchone()
    return dict(row)


def delete_exposure_link(conn, policy_uid, exposure_id):
    """Remove a policy-exposure link."""
    conn.execute(
        "DELETE FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    )
    conn.commit()


def set_primary_exposure(conn, policy_uid, exposure_id):
    """Set one exposure as primary for a policy, clearing others."""
    conn.execute(
        "UPDATE policy_exposure_links SET is_primary=0 WHERE policy_uid=?",
        (policy_uid,),
    )
    conn.execute(
        "UPDATE policy_exposure_links SET is_primary=1 WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    )
    conn.commit()


def recalc_exposure_rate(conn, *, link_id=None, policy_uid=None, exposure_id=None):
    """Recalculate cached rate on policy_exposure_links rows.

    Pass one of:
    - link_id: recalc a single link
    - policy_uid: recalc all links for a policy (e.g., premium changed)
    - exposure_id: recalc all links to an exposure (e.g., amount changed)
    """
    if link_id:
        where, params = "pel.id=?", (link_id,)
    elif policy_uid:
        where, params = "pel.policy_uid=?", (policy_uid,)
    elif exposure_id:
        where, params = "pel.exposure_id=?", (exposure_id,)
    else:
        return

    rows = conn.execute(
        f"""SELECT pel.id, p.premium, ce.amount, ce.denominator
            FROM policy_exposure_links pel
            JOIN policies p ON p.policy_uid = pel.policy_uid
            JOIN client_exposures ce ON ce.id = pel.exposure_id
            WHERE {where}""",
        params,
    ).fetchall()

    now = _utcnow()
    for r in rows:
        rate = _calc_rate(r["premium"], r["amount"], r["denominator"])
        conn.execute(
            "UPDATE policy_exposure_links SET rate=?, rate_updated_at=? WHERE id=?",
            (rate, now if rate is not None else None, r["id"]),
        )
    conn.commit()


def get_policy_exposures(conn, policy_uid):
    """Get all exposure links for a policy, with exposure details."""
    rows = conn.execute(
        """SELECT pel.*, ce.exposure_type, ce.amount, ce.denominator, ce.year,
                  ce.unit, ce.project_id, ce.client_id
           FROM policy_exposure_links pel
           JOIN client_exposures ce ON ce.id = pel.exposure_id
           WHERE pel.policy_uid=?
           ORDER BY pel.is_primary DESC, ce.exposure_type""",
        (policy_uid,),
    ).fetchall()
    return [dict(r) for r in rows]


def find_or_create_exposure(conn, *, client_id, project_id, exposure_type, year, amount, denominator=1):
    """Find an existing client_exposures row or create one. Returns the exposure id."""
    row = conn.execute(
        """SELECT id FROM client_exposures
           WHERE client_id=? AND COALESCE(project_id,0)=COALESCE(?,0)
           AND exposure_type=? AND year=?""",
        (client_id, project_id, exposure_type, year),
    ).fetchone()
    if row:
        return row["id"]
    conn.execute(
        """INSERT INTO client_exposures (client_id, project_id, exposure_type, year, amount, denominator)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, project_id, exposure_type, year, amount, denominator),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
