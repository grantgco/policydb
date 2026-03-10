"""Seed data: 5 clients, 20-25 policies, activity entries, premium history."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from policydb.db import next_policy_uid


def _days_from_now(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def run_seed(conn: sqlite3.Connection) -> None:
    # Guard: don't re-seed if data exists
    existing = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
    if existing["n"] > 0:
        import click
        if not click.confirm("Seed data already exists. Overwrite with fresh seed?", default=False):
            return
        conn.execute("DELETE FROM premium_history")
        conn.execute("DELETE FROM activity_log")
        conn.execute("DELETE FROM policies")
        conn.execute("DELETE FROM clients")
        conn.commit()

    account_exec = "Grant"

    # ─── CLIENTS ─────────────────────────────────────────────────────────────
    clients = [
        ("Meridian Development Group", "Real Estate Development", "Sarah Chen", "schen@meridiandev.com", "(404) 555-0101", "1200 Peachtree St NE, Atlanta, GA 30309", "Regional RE developer focused on mixed-use in the SE. Strong relationship — on account 4 years."),
        ("Apex Data Centers", "Digital Infrastructure", "James Holloway", "jholloway@apexdc.com", "(512) 555-0187", "4500 Capital of Texas Hwy, Austin, TX 78746", "Colocation and managed hosting. Complex program — 12+ policies. Key renewal April."),
        ("Skyline Residential Partners", "Real Estate Development", "Michelle Torres", "mtorres@skylineres.com", "(305) 555-0244", "800 Brickell Ave, Miami, FL 33131", "Multi-family developer in FL and GA. Builders risk is the main complexity."),
        ("TowerLink Infrastructure", "Digital Infrastructure", "Derek Okafor", "dokafor@towerlinkinfra.com", "(214) 555-0312", "3200 McKinney Ave, Dallas, TX 75204", "Wireless tower owner/operator. Workers comp and auto are the main concerns."),
        ("Cornerstone Commercial RE", "Real Estate Development", "Patricia Wyatt", "pwyatt@cornerstonecre.com", "(770) 555-0401", "5555 Glenridge Connector, Atlanta, GA 30342", "Commercial RE — office and retail. Smaller account, steady relationship."),
    ]

    client_ids = {}
    for name, industry, contact, email, phone, address, notes in clients:
        cursor = conn.execute(
            """INSERT INTO clients (name, industry_segment, primary_contact, contact_email,
               contact_phone, address, notes, account_exec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, industry, contact, email, phone, address, notes, account_exec),
        )
        client_ids[name] = cursor.lastrowid
    conn.commit()

    # ─── POLICIES ─────────────────────────────────────────────────────────────
    # Helper to insert and return uid
    def add_policy(client_name, policy_type, carrier, pol_number, eff, exp, premium,
                   limit_amount=None, deductible=None, description=None, coverage_form=None,
                   layer_position="Primary", tower_group=None, is_standalone=0,
                   colleague=None, uw_name=None, uw_contact=None,
                   renewal_status="Not Started", commission_rate=None, prior_premium=None,
                   notes=None):
        uid = next_policy_uid(conn)
        conn.execute(
            """INSERT INTO policies
               (policy_uid, client_id, policy_type, carrier, policy_number,
                effective_date, expiration_date, premium, limit_amount, deductible,
                description, coverage_form, layer_position, tower_group, is_standalone,
                placement_colleague, underwriter_name, underwriter_contact,
                renewal_status, commission_rate, prior_premium, account_exec, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, client_ids[client_name], policy_type, carrier, pol_number,
             eff, exp, premium, limit_amount, deductible, description, coverage_form,
             layer_position, tower_group, is_standalone,
             colleague, uw_name, uw_contact,
             renewal_status, commission_rate, prior_premium, account_exec, notes),
        )
        return uid

    # ── MERIDIAN DEVELOPMENT GROUP ──────────────────────────────────────────
    add_policy("Meridian Development Group",
        "General Liability", "Zurich North America", "GL-2024-MDG-001",
        _days_ago(90), _days_from_now(275), 85000, 2000000, 25000,
        "Covers general liability for all construction and development operations across the SE portfolio. Includes completed operations.",
        "Occurrence", "Primary", "GL Tower", 0, "Lisa Park", "Amy Chen", "achen@zurich.com",
        "In Progress", 0.115, 78500)

    add_policy("Meridian Development Group",
        "Umbrella / Excess", "Swiss Re", "UMB-2024-MDG-001",
        _days_ago(90), _days_from_now(275), 32000, 5000000, None,
        "First excess layer over GL and auto primary. $5M xs $2M.",
        "Occurrence", "$5M xs $2M (Layer 1)", "GL Tower", 0, "Lisa Park", "Tom Walsh", None,
        "In Progress", 0.10, 29500)

    add_policy("Meridian Development Group",
        "Umbrella / Excess", "Everest National", "UMB-2024-MDG-002",
        _days_ago(90), _days_from_now(275), 18000, 5000000, None,
        "Second excess layer. $5M xs $7M.",
        "Occurrence", "$5M xs $7M (Layer 2)", "GL Tower", 0, "Lisa Park", "Rachel Kim", None,
        "In Progress", 0.10, 16000)

    add_policy("Meridian Development Group",
        "Property / Builders Risk", "AIG", "BR-2024-MDG-001",
        _days_ago(60), _days_from_now(120), 145000, 28000000, 50000,
        "Builders risk for the Peachtree Commons project — 220-unit multifamily, under construction through Q4 2025.",
        "Occurrence", "Primary", None, 1, "Lisa Park", "Brad Foster", "bfoster@aig.com",
        "Pending Bind", 0.115, 131000, "Renewal quote received from AIG — reviewing.")

    add_policy("Meridian Development Group",
        "Professional Liability / E&O", "Chubb", "PL-2024-MDG-001",
        _days_ago(180), _days_from_now(185), 42000, 5000000, 25000,
        "Design and professional liability for development management services and project oversight activities.",
        "Claims-Made", "Primary", None, 0, "Lisa Park", "Janet Lee", None,
        "Not Started", 0.12, 39000)

    add_policy("Meridian Development Group",
        "Workers Compensation", "Hartford", "WC-2024-MDG-001",
        _days_ago(90), _days_from_now(275), 68000, None, None,
        "Workers compensation and employers liability for all SE operations. Experience mod: 0.92.",
        None, "Primary", None, 0, "Lisa Park", "Mark Davis", None,
        "In Progress", 0.10, 64000)

    add_policy("Meridian Development Group",
        "Commercial Auto", "Zurich North America", "CA-2024-MDG-001",
        _days_ago(90), _days_from_now(275), 22000, 1000000, 2500,
        "Commercial auto for company-owned vehicles and hired/non-owned auto.",
        "Occurrence", "Primary", None, 0, "Lisa Park", "Amy Chen", "achen@zurich.com",
        "In Progress", 0.115, 19500)

    # ── APEX DATA CENTERS (12+ policies) ────────────────────────────────────
    add_policy("Apex Data Centers",
        "General Liability", "Liberty Mutual", "GL-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 118000, 2000000, 10000,
        "Commercial general liability for colocation and managed hosting facilities in TX, AZ, and VA. Includes personal injury and advertising.",
        "Occurrence", "Primary", "GL Tower", 0, "Marcus Webb", "Sandra Hill", "shill@libertymutual.com",
        "Pending Bind", 0.10, 105000)

    add_policy("Apex Data Centers",
        "Umbrella / Excess", "Travelers", "UMB-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 55000, 10000000, None,
        "First excess layer over GL, auto, and employers liability. $10M xs $2M.",
        "Occurrence", "$10M xs $2M (Layer 1)", "GL Tower", 0, "Marcus Webb", "Chris Vance", None,
        "Pending Bind", 0.10, 49000)

    add_policy("Apex Data Centers",
        "Umbrella / Excess", "AIG", "UMB-2025-APEX-002",
        _days_ago(10), _days_from_now(80), 28000, 10000000, None,
        "Second excess layer. $10M xs $12M.",
        "Occurrence", "$10M xs $12M (Layer 2)", "GL Tower", 0, "Marcus Webb", "Brad Foster", "bfoster@aig.com",
        "Pending Bind", 0.10, 24000)

    add_policy("Apex Data Centers",
        "Cyber / Tech E&O", "Coalition", "CYBER-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 195000, 10000000, 100000,
        "Cyber liability and technology E&O covering network security, data breach response, ransomware, and business interruption for all colocation operations.",
        "Claims-Made", "Primary", None, 0, "Marcus Webb", "Devon Sharp", "dsharp@coalitioninc.com",
        "Bound", 0.08, 178000)

    add_policy("Apex Data Centers",
        "Property / Builders Risk", "FM Global", "PROP-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 312000, 125000000, 250000,
        "All-risk property for three owned data center facilities: Austin TX (45MW), Phoenix AZ (30MW), Ashburn VA (20MW). Includes business interruption and extra expense.",
        "Occurrence", "Primary", None, 0, "Marcus Webb", "Helen Torres", "htorres@fmglobal.com",
        "Bound", 0.09, 288000)

    add_policy("Apex Data Centers",
        "Workers Compensation", "Zurich North America", "WC-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 44000, None, None,
        "Workers compensation and employers liability for all facility and operations staff across TX, AZ, and VA.",
        None, "Primary", None, 0, "Marcus Webb", "Amy Chen", "achen@zurich.com",
        "Bound", 0.10, 40000)

    add_policy("Apex Data Centers",
        "Commercial Auto", "Liberty Mutual", "CA-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 18500, 1000000, 1000,
        "Commercial auto for company vehicles and non-owned auto used in facility operations.",
        "Occurrence", "Primary", None, 0, "Marcus Webb", "Sandra Hill", "shill@libertymutual.com",
        "Bound", 0.10, 17000)

    add_policy("Apex Data Centers",
        "Directors & Officers", "Chubb", "DO-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 85000, 10000000, 50000,
        "D&O liability for board and executive team. Side A/B/C coverage. Includes employment practices and fiduciary.",
        "Claims-Made", "Primary", None, 0, "Marcus Webb", "Janet Lee", "jlee@chubb.com",
        "Bound", 0.12, 78000)

    add_policy("Apex Data Centers",
        "Environmental", "AIG", "ENV-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 28000, 5000000, 25000,
        "Pollution legal liability and environmental impairment for fuel storage (diesel backup generators) at all three facilities.",
        "Claims-Made", "Primary", None, 1, "Marcus Webb", "Brad Foster", "bfoster@aig.com",
        "Bound", 0.12, 25000)

    add_policy("Apex Data Centers",
        "Crime / Fidelity", "Travelers", "CR-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 22000, 5000000, 25000,
        "Commercial crime — employee dishonesty, computer fraud, funds transfer fraud, and client property coverage.",
        "Claims-Made", "Primary", None, 1, "Marcus Webb", "Chris Vance", None,
        "Bound", 0.10, 19500)

    add_policy("Apex Data Centers",
        "Equipment Breakdown", "Hartford", "EB-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 35000, 50000000, 10000,
        "Equipment breakdown and mechanical/electrical breakdown coverage for UPS systems, cooling infrastructure, and critical data center equipment.",
        "Occurrence", "Primary", None, 1, "Marcus Webb", "Mark Davis", None,
        "Bound", 0.10, 31000)

    add_policy("Apex Data Centers",
        "Employment Practices Liability", "Chubb", "EPL-2025-APEX-001",
        _days_ago(10), _days_from_now(80), 42000, 5000000, 25000,
        "EPLI covering wrongful termination, harassment, discrimination for all employees.",
        "Claims-Made", "Primary", None, 1, "Marcus Webb", "Janet Lee", "jlee@chubb.com",
        "Bound", 0.12, 38500)

    # ── SKYLINE RESIDENTIAL PARTNERS ────────────────────────────────────────
    add_policy("Skyline Residential Partners",
        "General Liability", "Travelers", "GL-2024-SRP-001",
        _days_ago(120), _days_from_now(245), 62000, 1000000, 10000,
        "General liability for multifamily residential development and property management operations in FL and GA.",
        "Occurrence", "Primary", None, 0, "Lisa Park", "Chris Vance", None,
        "Not Started", 0.115, 57000)

    add_policy("Skyline Residential Partners",
        "Property / Builders Risk", "Zurich North America", "BR-2024-SRP-001",
        _days_ago(30), _days_from_now(60), 98000, 18000000, 50000,
        "Builders risk for Brickell Pointe — 180-unit luxury apartment tower under construction in Miami. Completion expected Q3 2025.",
        "Occurrence", "Primary", None, 1, "Lisa Park", "Amy Chen", "achen@zurich.com",
        "Urgent", 0.10, 89000, "Renewal approaching — project not yet complete. Confirm timeline with client.")

    add_policy("Skyline Residential Partners",
        "Professional Liability / E&O", "Markel", "PL-2024-SRP-001",
        _days_ago(120), _days_from_now(245), 28000, 3000000, 25000,
        "Professional liability for development management and construction oversight services.",
        "Claims-Made", "Primary", None, 0, "Lisa Park", "Susan Marks", None,
        "Not Started", 0.12, 25500)

    add_policy("Skyline Residential Partners",
        "Workers Compensation", "AmTrust", "WC-2024-SRP-001",
        _days_ago(120), _days_from_now(245), 38000, None, None,
        "Workers compensation for construction and property management staff in FL and GA.",
        None, "Primary", None, 0, "Lisa Park", "Paul Nguyen", None,
        "Not Started", 0.10, 34000)

    # ── TOWERLINK INFRASTRUCTURE ─────────────────────────────────────────────
    add_policy("TowerLink Infrastructure",
        "General Liability", "AIG", "GL-2024-TWR-001",
        _days_ago(200), _days_from_now(165), 55000, 1000000, 10000,
        "General liability for wireless tower ownership, leasing, and maintenance operations across TX, OK, and NM.",
        "Occurrence", "Primary", None, 0, "Marcus Webb", "Brad Foster", "bfoster@aig.com",
        "In Progress", 0.115, 51000)

    add_policy("TowerLink Infrastructure",
        "Workers Compensation", "Travelers", "WC-2024-TWR-001",
        _days_ago(200), _days_from_now(165), 112000, None, None,
        "Workers compensation for tower climbers and field technicians. High-hazard classification — experience mod: 1.18.",
        None, "Primary", None, 0, "Marcus Webb", "Chris Vance", None,
        "In Progress", 0.10, 98000, "Mod trending unfavorably — discuss safety program with Derek at next stewardship.")

    add_policy("TowerLink Infrastructure",
        "Commercial Auto", "Liberty Mutual", "CA-2024-TWR-001",
        _days_ago(200), _days_from_now(165), 78000, 1000000, 2500,
        "Commercial auto for large fleet — bucket trucks, pickups, and service vehicles. 85 vehicles.",
        "Occurrence", "Primary", None, 0, "Marcus Webb", "Sandra Hill", "shill@libertymutual.com",
        "In Progress", 0.10, 71000)

    add_policy("TowerLink Infrastructure",
        "Umbrella / Excess", "Swiss Re", "UMB-2024-TWR-001",
        _days_ago(200), _days_from_now(165), 38000, 10000000, None,
        "Umbrella over GL, auto, and WC. $10M xs $1M.",
        "Occurrence", "$10M xs $1M", None, 0, "Marcus Webb", "Tom Walsh", None,
        "In Progress", 0.10, 34000)

    # ── CORNERSTONE COMMERCIAL RE ────────────────────────────────────────────
    add_policy("Cornerstone Commercial RE",
        "General Liability", "Chubb", "GL-2024-CCR-001",
        _days_ago(150), _days_from_now(215), 32000, 1000000, 5000,
        "General liability for office and retail property ownership and management in metro Atlanta.",
        "Occurrence", "Primary", None, 0, "Lisa Park", "Janet Lee", "jlee@chubb.com",
        "Not Started", 0.115, 29500)

    add_policy("Cornerstone Commercial RE",
        "Property / Builders Risk", "Zurich North America", "PROP-2024-CCR-001",
        _days_ago(150), _days_from_now(215), 48000, 22000000, 25000,
        "Commercial property for owned office buildings and retail centers. Replacement cost valuation.",
        "Occurrence", "Primary", None, 0, "Lisa Park", "Amy Chen", "achen@zurich.com",
        "Not Started", 0.10, 44000)

    add_policy("Cornerstone Commercial RE",
        "Umbrella / Excess", "Hartford", "UMB-2024-CCR-001",
        _days_ago(150), _days_from_now(215), 15000, 5000000, None,
        "Umbrella over GL and auto. $5M xs $1M.",
        "Occurrence", "$5M xs $1M", None, 0, "Lisa Park", "Mark Davis", None,
        "Not Started", 0.10, 13500)

    conn.commit()

    # ─── ACTIVITY LOG ─────────────────────────────────────────────────────────
    activities = [
        # Meridian
        ("Meridian Development Group", _days_ago(5), "Meeting", "Sarah Chen", "Q1 stewardship — reviewed program, discussed Peachtree BR renewal", "Covered GL renewal progress and BR expiration.", None),
        ("Meridian Development Group", _days_ago(18), "Email", "Sarah Chen", "Sent updated premium estimates for renewal package", None, None),
        ("Meridian Development Group", _days_ago(32), "Renewal Check-In", "Lisa Park", "Internal renewal strategy call — Meridian GL/Umb tower", "Discussed Zurich pricing vs. market. Lisa to run alternative markets.", None),
        ("Meridian Development Group", _days_ago(75), "Call", "Sarah Chen", "Annual review scheduling — confirmed April meeting", None, None),
        # Apex
        ("Apex Data Centers", _days_ago(3), "Meeting", "James Holloway", "Bind confirmation — all policies confirmed bound for new term", "Confirmed all 12 policies bound. FM Global property took additional time due to schedule of values update.", None),
        ("Apex Data Centers", _days_ago(12), "Renewal Check-In", "Marcus Webb", "Pre-renewal strategy session — Apex program review", "Coalition cyber quote came in 8% above prior. Discussed retention options.", None),
        ("Apex Data Centers", _days_ago(25), "Email", "James Holloway", "Sent renewal submissions to key markets", None, None),
        ("Apex Data Centers", _days_ago(45), "Stewardship", "James Holloway", "Annual stewardship visit — Austin TX facility", "Toured new Phase 2 construction. Flagged potential need for inland marine for equipment in transit.", _days_from_now(14)),
        ("Apex Data Centers", _days_ago(60), "Internal Strategy", None, "Apex renewal kickoff — 120-day planning", "All lines due within 90 days. Marcus to lead placement.", None),
        # Skyline
        ("Skyline Residential Partners", _days_ago(8), "Call", "Michelle Torres", "Builders risk renewal — Brickell Pointe expiration discussion", "Project completion delayed to Q3. Need BR extension or renewal.", _days_from_now(7)),
        ("Skyline Residential Partners", _days_ago(22), "Email", "Michelle Torres", "Sent GL and E&O renewal summary to client", None, None),
        ("Skyline Residential Partners", _days_ago(55), "Meeting", "Michelle Torres", "Annual program review — Miami office", None, None),
        # TowerLink
        ("TowerLink Infrastructure", _days_ago(6), "Renewal Check-In", "Marcus Webb", "TowerLink GL/WC/Auto renewal in progress — status update", "Markets being approached. WC market tightening due to mod.", None),
        ("TowerLink Infrastructure", _days_ago(15), "Call", "Derek Okafor", "Discussed loss history for WC — safety program update", "Derek committed to implementing OSHA training for climbers by March.", _days_ago(5)),
        ("TowerLink Infrastructure", _days_ago(40), "Stewardship", "Derek Okafor", "Annual stewardship — Dallas TX", "Reviewed fleet safety program and driver MVRs.", None),
        ("TowerLink Infrastructure", _days_ago(80), "Email", "Derek Okafor", "Sent loss run summary to markets for early renewal indications", None, None),
        # Cornerstone
        ("Cornerstone Commercial RE", _days_ago(10), "Call", "Patricia Wyatt", "Quick check-in — confirmed no claims, renewal on calendar", None, None),
        ("Cornerstone Commercial RE", _days_ago(62), "Email", "Patricia Wyatt", "Sent annual program recap and renewal timeline", None, None),
        ("Cornerstone Commercial RE", _days_ago(90), "Meeting", "Patricia Wyatt", "Annual stewardship lunch", "Discussed potential new acquisition — 3 additional retail properties.", _days_ago(30)),
    ]

    for client_name, act_date, act_type, contact, subject, details, follow_up in activities:
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, activity_type, contact_person,
                subject, details, follow_up_date, account_exec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (act_date, client_ids[client_name], act_type, contact,
             subject, details, follow_up, account_exec),
        )
    conn.commit()

    # ─── PREMIUM HISTORY ──────────────────────────────────────────────────────
    history = [
        # Meridian GL — 3 years
        ("Meridian Development Group", "General Liability", "Zurich North America", "2022-01-15", "2023-01-15", 69000, 2000000, 25000, "Rate +4.5% on strong market"),
        ("Meridian Development Group", "General Liability", "Zurich North America", "2023-01-15", "2024-01-15", 75500, 2000000, 25000, "Rate +9.4% — general market hardening"),
        ("Meridian Development Group", "General Liability", "Zurich North America", "2024-01-15", "2025-01-15", 78500, 2000000, 25000, "Rate +4.0% — market stabilizing"),
        # Meridian Workers Comp — 2 years
        ("Meridian Development Group", "Workers Compensation", "Hartford", "2023-01-15", "2024-01-15", 60000, None, None, "Strong exp mod performance"),
        ("Meridian Development Group", "Workers Compensation", "Hartford", "2024-01-15", "2025-01-15", 64000, None, None, "Rate +6.7%"),
        # Apex Cyber — 3 years
        ("Apex Data Centers", "Cyber / Tech E&O", "Coalition", "2022-04-01", "2023-04-01", 142000, 5000000, 50000, "Limit increase from $5M to $10M at renewal"),
        ("Apex Data Centers", "Cyber / Tech E&O", "Coalition", "2023-04-01", "2024-04-01", 158000, 10000000, 100000, "Rate +11.3% — cyber market hardening"),
        ("Apex Data Centers", "Cyber / Tech E&O", "Coalition", "2024-04-01", "2025-04-01", 178000, 10000000, 100000, "Rate +12.7% — significant growth in operations"),
        # Apex Property — 2 years
        ("Apex Data Centers", "Property / Builders Risk", "FM Global", "2023-04-01", "2024-04-01", 245000, 95000000, 250000, "Added Phoenix facility mid-term"),
        ("Apex Data Centers", "Property / Builders Risk", "FM Global", "2024-04-01", "2025-04-01", 288000, 125000000, 250000, "Rate +17.6% — added Ashburn VA capacity"),
        # TowerLink WC — 2 years
        ("TowerLink Infrastructure", "Workers Compensation", "Travelers", "2023-06-01", "2024-06-01", 88000, None, None, "Mod 1.05 — one WC claim settled"),
        ("TowerLink Infrastructure", "Workers Compensation", "Travelers", "2024-06-01", "2025-06-01", 98000, None, None, "Mod 1.12 — loss history worsening"),
    ]

    for (client_name, pol_type, carrier, eff, exp, premium, limit_amount, deductible, notes) in history:
        try:
            conn.execute(
                """INSERT INTO premium_history
                   (client_id, policy_type, carrier, term_effective, term_expiration,
                    premium, limit_amount, deductible, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (client_ids[client_name], pol_type, carrier, eff, exp,
                 premium, limit_amount, deductible, notes),
            )
        except Exception:
            pass  # Skip duplicates
    conn.commit()
