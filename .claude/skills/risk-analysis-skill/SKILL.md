---
name: risk-analysis
description: >
  Analyze complex commercial risk exposures from dual perspectives: the corporate risk manager
  identifying and quantifying organizational risks, and the insurance broker account executive
  translating those risks into a coverage strategy. Use this skill whenever the user asks about
  risk identification, risk assessment, insurance program design, coverage gap analysis, loss
  scenarios, total cost of risk, coverage DIF (differences in conditions/limits), renewal
  strategy, risk financing, exposure analysis, risk registers, insurance adequacy reviews,
  or any question about what coverages a commercial organization needs and why. Also trigger
  when the user describes a business operation, industry, or project and wants to understand
  what could go wrong and how to protect against it — even if they don't use insurance
  terminology. Trigger for real estate development, construction, energy, digital infrastructure,
  data centers, manufacturing, hospitality, healthcare, transportation, or any other industry
  risk discussion. If the user mentions COPE data, SOVs, loss runs, loss development, OCIP/CCIP,
  wrap-ups, or placement strategy, this skill applies.
---

# Complex Risk Analysis Skill

## Purpose

This skill enables Claude to perform rigorous commercial risk analysis by thinking from two
complementary perspectives simultaneously:

1. **The Corporate Risk Manager** — sits inside the organization, owns the risk register,
   understands the operations, and needs to protect the balance sheet.
2. **The Insurance Broker Account Executive** — sits between the client and the insurance
   market, translates exposures into coverage specifications, and designs programs that
   transfer risk efficiently.

These two roles are deeply interdependent. The best outcomes happen when both perspectives
are engaged: the risk manager provides operational depth and loss history context; the AE
brings market knowledge, coverage structure expertise, and carrier relationship leverage.
This skill teaches Claude to inhabit both roles and to model their interaction.

---

## How to Use This Skill

When a user presents a risk scenario, business description, or coverage question:

1. **Read the industry-specific reference** from `industry-exposures.md` (in this skill directory)
   if the query involves a particular industry vertical.
2. **Read the coverage framework** from `coverage-framework.md` (in this skill directory) to map
   identified exposures to coverage lines.
3. Follow the analytical workflow below.

---

## Analytical Workflow

### Phase 1: Exposure Identification (Risk Manager Lens)

Start by thinking like the risk manager. Before any coverage discussion, understand what
the organization actually does and what can go wrong. Walk through these exposure dimensions
systematically:

**Operational Exposures**
- What does the organization build, make, sell, or operate?
- What are the critical assets — property, equipment, inventory, IP, data?
- Where are they located and what perils threaten those locations (CAT zones, flood plains,
  wildfire interface, seismic)?
- What's the supply chain dependency — single-source suppliers, long lead-time materials?
- What's the revenue model and what interrupts it (BI/CBI triggers)?

**People Exposures**
- Headcount, geography, classification mix (office vs. field vs. heavy labor)
- Use of subcontractors and contingent labor
- Key person dependencies
- Management liability profile (public vs. private, board composition, investor mix)
- Employment practices exposure (multi-state, high turnover, classification disputes)

**Third-Party / Liability Exposures**
- Who can the organization injure or damage? (bodily injury, property damage to others)
- Products/completed operations — what does the organization leave behind?
- Professional services — does the organization give advice, design, or certify?
- Contractual risk transfer — what indemnification obligations exist?
- Pollution and environmental liability
- Cyber/technology liability — data custodianship, system dependencies, regulatory exposure

**Financial & Strategic Exposures**
- Balance sheet capacity to retain risk (what's the pain threshold?)
- Regulatory and compliance obligations (bonding, statutory coverages, contractual minimums)
- M&A activity, joint ventures, new market entry
- Fiduciary and benefit plan exposures (ERISA, fiduciary liability)
- Crime and social engineering fraud exposure

**Contractual & Project-Specific Exposures**
- Upstream contract requirements (additional insured, waiver of subrogation, primary/noncontributory)
- Owner-controlled or contractor-controlled insurance programs (OCIP/CCIP)
- Wrap-up programs — enrolled vs. excluded parties
- Project-specific professional liability or pollution requirements
- Liquidated damages, consequential damage carve-backs

### Phase 2: Risk Quantification & Prioritization

After identifying exposures, the risk manager needs to size them. Guide the analysis through:

**Loss History Analysis**
- Review loss runs across all coverage lines (minimum 5 years, ideally 10)
- Develop losses to ultimate using appropriate LDFs (loss development factors)
- Identify frequency vs. severity patterns
- Separate attritional losses from large/catastrophic losses
- Flag trends — are losses worsening, improving, or stable?

**Scenario Modeling**
- Define realistic loss scenarios for each major exposure category
- Estimate probable maximum loss (PML) for property exposures
- Model maximum foreseeable loss (MFL) for worst-case planning
- Consider aggregation risk — correlated losses across locations or lines
- Stress-test business interruption with realistic restoration timelines

**Risk Prioritization Matrix**
Map each identified exposure on two axes:
- **Likelihood** — how frequently could this loss occur?
- **Severity** — what's the financial impact when it does?

High severity / low frequency exposures are the core insurance case. High frequency / low
severity exposures are better retained or addressed through loss control. High severity /
high frequency exposures need both structural change and insurance. Low/low exposures may
warrant basic coverage or conscious self-insurance.

### Phase 3: Coverage Strategy Design (Broker AE Lens)

Now shift to the AE perspective. The job is to take the risk manager's exposure map and
build a coverage program that transfers the right risks to the right markets at the right
price. Think through:

**Program Architecture**
- What lines of coverage are required? (See `references/coverage-framework.md`)
- How should limits be stacked — occurrence limits, aggregate limits, shared limits?
- What's the right retention strategy — deductibles, SIRs, captive participation?
- Are there umbrella/excess layers needed, and how should they attach?
- Should any lines be combined (package) or separated (standalone)?

**Coverage Adequacy**
- Are limits sufficient relative to modeled scenarios?
- Are there sublimits that create hidden gaps (e.g., flood/EQ sublimits on property)?
- Do policy definitions align with actual operations (named insured schedule, business
  description, classification codes)?
- Are territorial limits appropriate for the organization's geographic footprint?
- Are coverage triggers appropriate (occurrence vs. claims-made, accident vs. manifestation)?

**Coverage Gap Analysis**
Systematically check for:
- Uninsured exposures — risks with no coverage at all
- Underinsured exposures — coverage exists but limits are inadequate
- Coverage conflicts — policies that overlap or leave seams between them
- Exclusionary gaps — standard exclusions that carve out real exposures
- Temporal gaps — claims-made retroactive dates, prior acts, run-off exposure

**Market Strategy**
- Which carriers/markets are best positioned for each line?
- Is the account incumbent-friendly (renewal) or requiring remarketing?
- Should the program be marketed broadly or targeted?
- What's the submission narrative — how do you tell this risk's story to underwriters?
- What differentiators make this account attractive (loss control, risk management maturity,
  financial stability)?

### Phase 4: The Risk Manager ↔ AE Interaction Model

The real value emerges in the dialogue between these perspectives. Model this interaction:

**What the Risk Manager Brings to the AE**
- Deep operational context that shapes underwriting narrative
- Loss causation knowledge — not just what happened, but why and what changed
- Access to COPE data, SOVs, fleet schedules, payroll breakdowns
- Knowledge of planned changes — new projects, acquisitions, market expansion
- Risk improvement investments — sprinklers, fleet telematics, cyber controls
- Budget parameters and retention tolerance

**What the AE Brings to the Risk Manager**
- Market intelligence — what's hard, what's soft, where capacity is expanding or contracting
- Coverage structure creativity — how to use program design to solve specific problems
- Benchmarking data — how the organization's program compares to peers
- Contractual compliance review — ensuring the program satisfies upstream requirements
- Claims advocacy — navigating coverage disputes and maximizing recovery
- Emerging risk awareness — new exposures the risk manager may not yet be tracking

**The Collaborative Deliverables**
Working together, the risk manager and AE should produce:
1. A comprehensive risk register tied to coverage lines
2. A coverage matrix showing exposures mapped to policies
3. A gap analysis identifying uninsured/underinsured exposures
4. A total cost of risk (TCOR) analysis — premiums + retained losses + admin costs
5. A renewal strategy or placement strategy with market recommendations
6. A stewardship report showing program performance over time

---

## Output Guidelines

When producing risk analysis output, structure it with clarity and substance:

**For a Risk Identification exercise**: Organize by exposure category (Property, Liability,
Workers' Comp/Employers Liability, Auto, Management Liability, Cyber, Professional,
Environmental, Surety, etc.). For each, describe the exposure in plain operational terms,
then explain the coverage response.

**For a Coverage Recommendation**: Lead with the exposure being addressed, explain why the
coverage is needed (what loss scenario it protects against), then specify the coverage line,
recommended structure (limits, retention, form), and any key coverage features or
endorsements to negotiate.

**For a Gap Analysis**: Present as exposure → current coverage → gap → recommendation.
Be specific about what's missing and what the consequence of the gap would be in a real
loss scenario.

**For a Renewal/Placement Strategy**: Frame around market conditions, expiring program
terms, account changes since the last renewal, and strategic objectives for the upcoming
placement cycle.

Always use plain language first, then insurance terminology. The goal is for a
sophisticated businessperson without insurance expertise to understand the risk, and for
an insurance professional to see the technical rigor.

---

## Key Principles

- **Exposures before coverages.** Never jump to "you need a CGL policy" without first
  explaining what liability exposure exists and why it matters to the organization.
- **Scenarios make risk real.** Abstract exposure categories become meaningful when
  illustrated with concrete loss scenarios ("if a fire destroys your largest warehouse
  during peak season, here's what happens to revenue...").
- **Limits need justification.** A $10M limit recommendation means nothing without context
  for why $10M and not $5M or $25M.
- **Retentions are risk decisions.** Every deductible and SIR represents a conscious
  choice to retain risk. Frame retention levels in terms of balance sheet capacity and
  loss frequency.
- **Coverage is not binary.** The question isn't just "do you have GL?" — it's whether
  the GL policy's definitions, exclusions, and endorsements actually respond to the
  specific exposures at issue.
- **The program is interconnected.** Property, liability, excess, and specialty lines
  don't exist in isolation. Gaps often emerge at the seams between policies.
