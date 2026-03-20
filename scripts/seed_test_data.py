#!/usr/bin/env python3
"""Seed test data: activities with hours across time frames, follow-ups."""
import sqlite3
import os
from datetime import date, timedelta

DB_PATH = os.path.expanduser("~/.policydb/policydb.sqlite")

# client_id → name mapping (from existing data)
CLIENTS = {
    1: "Meridian Development Group",
    2: "Apex Data Centers",
    3: "Skyline Residential Partners",
    4: "TowerLink Infrastructure",
    5: "Cornerstone Commercial RE",
}

# policy_id → (policy_uid, client_id)
POLICIES = {
    1: ("POL-001", 1),
    2: ("POL-002", 1),
    4: ("POL-004", 2),
    5: ("POL-005", 2),
    7: ("POL-007", 3),
    9: ("POL-009", 4),
}

def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Verify duration_hours column exists
    cols = [r[1] for r in conn.execute("PRAGMA table_info(activity_log)").fetchall()]
    if "duration_hours" not in cols:
        print("ERROR: duration_hours column missing — run `policydb serve` once to apply migration 031 first.")
        return

    activities = [
        # TODAY (days_ago=0)
        dict(client_id=1, policy_id=1, activity_type="Phone Call", subject="Renewal discussion",
             details="Spoke with risk manager about upcoming GL renewal. They want higher limits.",
             activity_date=days_ago(0), duration_hours=0.5, follow_up_date=None),
        dict(client_id=2, policy_id=4, activity_type="Email", subject="Cyber policy documents sent",
             details="Forwarded binder and policy jacket to IT director for review.",
             activity_date=days_ago(0), duration_hours=0.2, follow_up_date=days_ago(-3)),

        # 2 DAYS AGO
        dict(client_id=3, policy_id=7, activity_type="Meeting", subject="Annual review meeting",
             details="Reviewed all lines in force. Client satisfied with current program. Discussed adding Umbrella.",
             activity_date=days_ago(2), duration_hours=1.5, follow_up_date=days_ago(-7)),
        dict(client_id=5, policy_id=None, activity_type="Phone Call", subject="New client intake call",
             details="Initial call to understand their current coverage gaps. They have 3 commercial properties.",
             activity_date=days_ago(2), duration_hours=0.75, follow_up_date=None),

        # 5 DAYS AGO
        dict(client_id=4, policy_id=9, activity_type="Email", subject="Renewal quote follow-up",
             details="Sent reminder to carrier for quote. Deadline is end of month.",
             activity_date=days_ago(5), duration_hours=0.3, follow_up_date=days_ago(-2)),
        dict(client_id=1, policy_id=2, activity_type="Underwriting", subject="Submitted renewal application",
             details="Completed ACORD 125 and submitted to carrier with loss runs.",
             activity_date=days_ago(5), duration_hours=2.0, follow_up_date=None),

        # 10 DAYS AGO
        dict(client_id=2, policy_id=5, activity_type="Phone Call", subject="Claims follow-up call",
             details="Checked in on open GL claim from December. Adjuster assigned.",
             activity_date=days_ago(10), duration_hours=0.5, follow_up_date=None),
        dict(client_id=3, policy_id=7, activity_type="Meeting", subject="Coverage review — property",
             details="Walked through property schedule. Several locations need updated replacement cost values.",
             activity_date=days_ago(10), duration_hours=1.0, follow_up_date=days_ago(-5)),

        # 15 DAYS AGO
        dict(client_id=5, policy_id=None, activity_type="Underwriting", subject="Prepared renewal package",
             details="Compiled 5 years loss runs, completed supplement, drafted cover letter.",
             activity_date=days_ago(15), duration_hours=3.0, follow_up_date=None),
        dict(client_id=4, policy_id=9, activity_type="Email", subject="Carrier market search",
             details="Reached out to 3 markets for E&S GL quote. Two responded.",
             activity_date=days_ago(15), duration_hours=0.5, follow_up_date=None),

        # 20 DAYS AGO
        dict(client_id=1, policy_id=1, activity_type="Phone Call", subject="Premium financing discussion",
             details="Client asked about payment options. Referred to Premium Finance Co.",
             activity_date=days_ago(20), duration_hours=0.25, follow_up_date=None),
        dict(client_id=2, policy_id=4, activity_type="Meeting", subject="Cyber risk workshop",
             details="Facilitated 90-min workshop with their IT and legal teams on cyber exposure.",
             activity_date=days_ago(20), duration_hours=1.5, follow_up_date=None),

        # 25 DAYS AGO
        dict(client_id=3, policy_id=7, activity_type="Email", subject="Certificate request",
             details="Processed 4 certificate requests for lender compliance.",
             activity_date=days_ago(25), duration_hours=0.5, follow_up_date=None),
        dict(client_id=5, policy_id=None, activity_type="Phone Call", subject="Broker of record discussion",
             details="Client called to discuss transferring their program to our agency.",
             activity_date=days_ago(25), duration_hours=0.5, follow_up_date=days_ago(-1)),

        # 30 DAYS AGO
        dict(client_id=1, policy_id=2, activity_type="Underwriting", subject="Loss control visit prep",
             details="Coordinated loss control inspection with carrier for property location.",
             activity_date=days_ago(30), duration_hours=1.0, follow_up_date=None),
        dict(client_id=4, policy_id=9, activity_type="Meeting", subject="Quarterly service call",
             details="Quarterly stewardship call. Reviewed open items, claims status, upcoming renewals.",
             activity_date=days_ago(30), duration_hours=1.0, follow_up_date=None),

        # ─── 30–90 DAY RANGE (exercises 30d / 90d briefing filters) ──────────

        # 35 DAYS AGO
        dict(client_id=2, policy_id=5, activity_type="Meeting", subject="Stewardship meeting",
             details="Annual stewardship presentation. Covered loss history, market trends, and service plan.",
             activity_date=days_ago(35), duration_hours=2.0, follow_up_date=None),
        dict(client_id=1, policy_id=1, activity_type="Email", subject="Certificate batch — lender requirements",
             details="Processed 6 certificate requests for two new financing packages.",
             activity_date=days_ago(35), duration_hours=0.75, follow_up_date=None),

        # 42 DAYS AGO
        dict(client_id=3, policy_id=7, activity_type="Phone Call", subject="Claim check-in — roof damage",
             details="Adjuster report received. Subrogation pending against contractor.",
             activity_date=days_ago(42), duration_hours=0.5, follow_up_date=None),
        dict(client_id=5, policy_id=None, activity_type="Site Visit", subject="Site survey — new prospect location",
             details="Toured 3 commercial properties. Noted fire protection and security features for submission.",
             activity_date=days_ago(42), duration_hours=3.0, follow_up_date=None),

        # 50 DAYS AGO
        dict(client_id=4, policy_id=9, activity_type="Internal Strategy", subject="Placement strategy — WC renewal",
             details="Internal meeting to review experience mod and loss development. Targeting 3 markets.",
             activity_date=days_ago(50), duration_hours=1.5, follow_up_date=None),
        dict(client_id=1, policy_id=2, activity_type="Email", subject="Carrier loss run request sent",
             details="Requested 5-year valued loss runs from Travelers for renewal submission.",
             activity_date=days_ago(50), duration_hours=0.25, follow_up_date=None),

        # 60 DAYS AGO
        dict(client_id=2, policy_id=4, activity_type="Meeting", subject="Cyber tabletop exercise",
             details="Led tabletop ransomware scenario with IT and legal teams. Identified response plan gaps.",
             activity_date=days_ago(60), duration_hours=3.0, follow_up_date=None),
        dict(client_id=3, policy_id=7, activity_type="Renewal Check-In", subject="Property renewal — 120 day check",
             details="Touched base on upcoming property renewal. Client wants to add 2 locations to schedule.",
             activity_date=days_ago(60), duration_hours=0.5, follow_up_date=None),

        # 75 DAYS AGO
        dict(client_id=5, policy_id=None, activity_type="Phone Call", subject="Competitor intel — incumbent agent call",
             details="Spoke with outgoing agent. Current program has GL, property, auto, WC, umbrella.",
             activity_date=days_ago(75), duration_hours=0.5, follow_up_date=None),
        dict(client_id=4, policy_id=9, activity_type="Email", subject="Exposure data request — annual audit",
             details="Sent carrier audit worksheet to client CFO for payroll and revenue confirmation.",
             activity_date=days_ago(75), duration_hours=0.3, follow_up_date=None),

        # 85 DAYS AGO
        dict(client_id=1, policy_id=1, activity_type="Claim Discussion", subject="GL claim review — slip and fall",
             details="Reviewed claim reserve increase with adjuster. Discussed litigation strategy.",
             activity_date=days_ago(85), duration_hours=1.0, follow_up_date=None),
        dict(client_id=2, policy_id=5, activity_type="Meeting", subject="Risk engineering visit debrief",
             details="Reviewed carrier risk engineering report. 3 recommendations requiring client action.",
             activity_date=days_ago(85), duration_hours=1.5, follow_up_date=None),

        # 90 DAYS AGO (boundary — should appear in 90d filter only)
        dict(client_id=3, policy_id=7, activity_type="Other", subject="Policy issuance review",
             details="Received and reviewed issued policies. Identified 2 endorsements needed.",
             activity_date=days_ago(90), duration_hours=1.0, follow_up_date=None),
        dict(client_id=4, policy_id=9, activity_type="Phone Call", subject="Market conditions briefing",
             details="Called client to discuss hardening market conditions and potential rate impact.",
             activity_date=days_ago(90), duration_hours=0.5, follow_up_date=None),
    ]

    inserted = 0
    for a in activities:
        follow_up_date = a.get("follow_up_date")
        # Only set follow_up_date if it's in the future (negative days_ago)
        if follow_up_date and follow_up_date < date.today().isoformat():
            follow_up_date = None  # past follow-ups don't make sense as open items

        conn.execute("""
            INSERT INTO activity_log
                (client_id, policy_id, activity_type, subject, details,
                 activity_date, duration_hours, follow_up_date, follow_up_done)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            a["client_id"], a.get("policy_id"), a["activity_type"],
            a["subject"], a["details"], a["activity_date"],
            a["duration_hours"], follow_up_date,
        ))
        inserted += 1

    # Add a few open follow-ups (future dates) for the follow-ups page test
    followups = [
        dict(client_id=1, policy_id=1, activity_type="Phone Call", subject="Confirm renewal terms accepted",
             details="Client needs to sign off on the proposed renewal structure before we bind.",
             activity_date=days_ago(3), duration_hours=0.5,
             follow_up_date=(date.today() + timedelta(days=2)).isoformat()),
        dict(client_id=2, policy_id=4, activity_type="Email", subject="Request updated cyber questionnaire",
             details="Carrier requires updated cyber app before quoting renewal. Need to send to IT director.",
             activity_date=days_ago(6), duration_hours=0.3,
             follow_up_date=(date.today() + timedelta(days=5)).isoformat()),
        dict(client_id=3, policy_id=7, activity_type="Meeting", subject="Follow up on replacement cost appraisals",
             details="Client agreed to order property appraisals. Need to confirm they've engaged appraiser.",
             activity_date=days_ago(8), duration_hours=1.0,
             follow_up_date=(date.today() + timedelta(days=1)).isoformat()),
        dict(client_id=5, policy_id=None, activity_type="Phone Call", subject="BOR letter status",
             details="Prospect said they'd have board approval by end of week. Check back Monday.",
             activity_date=days_ago(4), duration_hours=0.5,
             follow_up_date=(date.today() + timedelta(days=3)).isoformat()),
        # One overdue follow-up
        dict(client_id=4, policy_id=9, activity_type="Email", subject="Outstanding quote from carrier",
             details="Sent RFQ to Markel 2 weeks ago. No response. Need to escalate.",
             activity_date=days_ago(14), duration_hours=0.2,
             follow_up_date=(date.today() - timedelta(days=4)).isoformat()),
    ]

    for f in followups:
        conn.execute("""
            INSERT INTO activity_log
                (client_id, policy_id, activity_type, subject, details,
                 activity_date, duration_hours, follow_up_date, follow_up_done)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            f["client_id"], f.get("policy_id"), f["activity_type"],
            f["subject"], f["details"], f["activity_date"],
            f["duration_hours"], f["follow_up_date"],
        ))
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Inserted {inserted} test activity records.")
    print("  - 16 activities spanning today → 30 days ago")
    print("  - 14 activities spanning 35 → 90 days ago (exercises 30d/90d briefing filters)")
    print("  - 4 open follow-ups (future dates)")
    print("  - 1 overdue follow-up (4 days past due)")
    print()
    print("Filter coverage:")
    print("  7d  filter: ~4 activities (today + 2d + 5d)")
    print("  14d filter: ~8 activities (above + 10d)")
    print("  30d filter: ~16 activities (above + 15d + 20d + 25d + 30d)")
    print("  90d filter: ~30 activities (above + 35d–90d range)")

if __name__ == "__main__":
    main()
