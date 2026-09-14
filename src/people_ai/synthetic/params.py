"""
All knobs for the synthetic company in one place.

Anything listed under PLANTED SIGNALS is a deliberate pattern with a known answer. Evals and
talent-review demos rely on these, so change them only together with docs/Synthetic_Talent_Lifecycle_Schema.md.
"""
from datetime import date

SEED = 42
FOUNDED = date(2014, 1, 1)          # org units exist from here; initial employees hired between here and START
START = date(2021, 1, 1)            # simulation window
END = date(2025, 12, 31)
OPEN_ENDED = date(9999, 12, 31)

INITIAL_HEADCOUNT = 3000

# ----------------------------------------------------------------------------
# Workforce dynamics (annual rates before multipliers)
# ----------------------------------------------------------------------------
GROWTH_BY_YEAR = {2021: 0.12, 2022: 0.14, 2023: 0.02, 2024: 0.09, 2025: 0.06}
BASE_VOLUNTARY_ANNUAL = 0.066
BASE_INVOLUNTARY_ANNUAL = 0.022
LEAVE_ANNUAL = 0.03
TRANSFER_ANNUAL = 0.05
RELOCATION_SHARE_OF_TRANSFERS = 0.10
BACKFILL_RATE = 0.85
MAX_SPAN = 10                        # a line manager takes new reports until this many

# promotion probability per cycle (March 15 and September 15) by latest rating, for eligible ICs
PROMOTION_PROB_BY_RATING = {1: 0.0, 2: 0.0, 3: 0.03, 4: 0.13, 5: 0.30}
MERIT_BY_RATING = {1: 0.0, 2: 0.01, 3: 0.03, 4: 0.045, 5: 0.06}   # March 1

# ratings: latent performance + noise, cut into 1..5 at roughly 3/12/55/22/8 percent
RATING_CUTS = (-2.19, -1.21, 0.61, 1.64)

# ----------------------------------------------------------------------------
# Locations, jobs, pay
# ----------------------------------------------------------------------------
LOCATIONS = [
    # id, city, country, region, cost_of_living_index, hiring weight
    (1, "Seattle", "US", "AMER", 1.15, 0.22),
    (2, "San Diego", "US", "AMER", 1.10, 0.10),
    (3, "Austin", "US", "AMER", 1.00, 0.12),
    (4, "Toronto", "CA", "AMER", 0.95, 0.08),
    (5, "Dublin", "IE", "EMEA", 1.05, 0.10),
    (6, "London", "UK", "EMEA", 1.20, 0.12),
    (7, "Bangalore", "IN", "APAC", 0.45, 0.20),
    (8, "Tokyo", "JP", "APAC", 1.10, 0.06),
]

FAMILIES = {
    # family: (IC title, manager domain, pay premium)
    "Engineering": ("Software Engineer", "Engineering", 1.15),
    "Data": ("Data Scientist", "Data Science", 1.12),
    "Product": ("Product Manager", "Product", 1.10),
    "Design": ("Product Designer", "Design", 1.00),
    "Sales": ("Account Executive", "Sales", 0.95),
    "Marketing": ("Marketing Manager", "Marketing", 0.90),
    "Customer Success": ("Customer Success Manager", "Customer Success", 0.85),
    "People": ("People Partner", "People", 0.85),
    "Finance": ("Financial Analyst", "Finance", 0.90),
    "Legal": ("Counsel", "Legal", 1.05),
}
IC_LEVEL_PREFIX = {3: "Associate", 4: "", 5: "Senior", 6: "Staff", 7: "Principal"}
M_LEVEL_TITLE = {6: "Manager, {d}", 7: "Senior Manager, {d}", 8: "Director, {d}", 9: "VP, {d}"}
LEVEL_MID_2021 = {3: 90_000, 4: 120_000, 5: 160_000, 6: 205_000, 7: 255_000, 8: 300_000, 9: 380_000, 10: 600_000}
MANAGER_TRACK_PREMIUM = 1.05
BAND_ANNUAL_INCREASE = 0.035
NEW_HIRE_LEVEL_WEIGHTS = {3: 0.20, 4: 0.33, 5: 0.27, 6: 0.15, 7: 0.05}

POSITION_LEVEL = {"line": 6, "lead": 7, "org_head": 8, "div_head": 9, "ceo": 10}

# ----------------------------------------------------------------------------
# Org design. Ids are assigned in this order: company=1, divisions 2-5, orgs 6-18, teams from 19.
# The first team listed ("App Experience") is therefore org_unit_id 19, which reorg 1 moves.
# ----------------------------------------------------------------------------
COMPANY = "Acme Corp"
ORG_DESIGN = {
    "Consumer": {
        "Mobile": ["App Experience", "iOS", "Android", "Mobile Platform"],
        "Growth": ["Acquisition", "Activation", "Lifecycle Marketing", "Experimentation"],
        "Payments": ["Checkout", "Risk & Fraud", "Billing"],
    },
    "Enterprise": {
        "Cloud Sales": ["Enterprise AMER", "Enterprise EMEA", "Enterprise APAC", "Sales Development"],
        "Solutions": ["Solutions Engineering", "Customer Success", "Professional Services"],
        "Partner": ["Alliances", "Channel Sales", "Marketplace"],
    },
    "Platform": {
        "Infrastructure": ["Compute", "Networking", "Reliability", "Developer Productivity"],
        "Data Platform": ["Data Engineering", "Analytics Engineering", "Data Governance"],
        "AI Platform": ["Model Serving", "ML Training", "Applied ML", "Evaluation"],
        "Security": ["AppSec", "Security Operations", "Identity"],
    },
    "Corporate": {
        "People": ["Talent Acquisition", "HR Business Partners", "People Analytics", "Total Rewards"],
        "Finance": ["FP&A", "Accounting", "Procurement"],
        "Legal": ["Commercial Legal", "Privacy & Compliance", "Corporate Legal"],
    },
}
DIVISION_FAMILY = {"Consumer": "Engineering", "Enterprise": "Sales", "Platform": "Engineering", "Corporate": "Finance"}

ORG_FAMILY_MIX = {
    "Mobile": {"Engineering": .62, "Product": .12, "Design": .14, "Data": .06, "Marketing": .06},
    "Growth": {"Engineering": .50, "Product": .14, "Data": .16, "Design": .08, "Marketing": .12},
    "Payments": {"Engineering": .65, "Product": .12, "Data": .15, "Design": .08},
    "Cloud Sales": {"Sales": .85, "Marketing": .05, "Customer Success": .10},
    "Solutions": {"Customer Success": .55, "Engineering": .35, "Product": .10},
    "Partner": {"Sales": .60, "Marketing": .20, "Customer Success": .20},
    "Infrastructure": {"Engineering": .85, "Product": .08, "Data": .07},
    "Data Platform": {"Data": .45, "Engineering": .50, "Product": .05},
    "AI Platform": {"Engineering": .50, "Data": .42, "Product": .08},
    "Applied AI": {"Engineering": .50, "Data": .42, "Product": .08},
    "Security": {"Engineering": .90, "Product": .10},
    "People": {"People": 1.0},
    "Finance": {"Finance": 1.0},
    "Legal": {"Legal": 1.0},
}
TEAM_FAMILY_MIX = {
    "Lifecycle Marketing": {"Marketing": .70, "Data": .10, "Engineering": .20},
    "Risk & Fraud": {"Data": .45, "Engineering": .45, "Product": .10},
    "Customer Success": {"Customer Success": .90, "Sales": .10},
    "Sales Development": {"Sales": 1.0},
}
TEAM_REGION = {"Enterprise AMER": "AMER", "Enterprise EMEA": "EMEA", "Enterprise APAC": "APAC"}
ORG_SIZE_WEIGHT = {"Cloud Sales": 1.3, "People": 0.6, "Finance": 0.5, "Legal": 0.35}
TEAM_SIZE_WEIGHT = {"People Analytics": 0.35, "HR Business Partners": 0.5, "Total Rewards": 0.5}
ORG_GROWTH_WEIGHT = {"AI Platform": 2.5, "Applied AI": 2.5, "Data Platform": 1.4, "Talent Acquisition": 1.0}

# Reorg 1: move a team between orgs. Reorg 2: split an org in two.
REORG_MOVE = dict(when=date(2023, 4, 1), team="App Experience", new_parent="Growth")
REORG_SPLIT = dict(when=date(2024, 9, 1), from_org="AI Platform", new_org="Applied AI",
                   teams=["Applied ML", "Evaluation"])

# ----------------------------------------------------------------------------
# Recruiting
# ----------------------------------------------------------------------------
STAGES = ["applied", "recruiter_screen", "hiring_manager_screen", "onsite", "offer"]
SOURCE_WEIGHTS = {"inbound": 0.45, "referral": 0.18, "sourced": 0.22, "agency": 0.05, "campus": 0.10}
SHARE_INTERNAL = 0.035
SHARE_BOOMERANG = 0.015
SHARE_REPEAT_CANDIDATE = 0.10
APPLICANTS_PER_BATCH = (3.45, 0.45)       # lognormal mu, sigma  (~34 per batch)
MAX_SOURCING_BATCHES = 3
BUSINESS_CANCEL_RATE = 0.05
WITHDRAW_PROB = 0.04

# advance probability at each stage by source (S3: referrals convert better)
STAGE_PASS = {
    "applied":               {"referral": .60, "inbound": .28, "sourced": .45, "agency": .50, "campus": .30, "internal": .70, "boomerang": .70},
    "recruiter_screen":      {"referral": .65, "inbound": .50, "sourced": .50, "agency": .50, "campus": .50, "internal": .70, "boomerang": .70},
    "hiring_manager_screen": {"referral": .60, "inbound": .52, "sourced": .52, "agency": .52, "campus": .50, "internal": .65, "boomerang": .65},
    "onsite":                {"referral": .55, "inbound": .42, "sourced": .42, "agency": .42, "campus": .40, "internal": .60, "boomerang": .60},
}
OFFER_ACCEPT = {"referral": .88, "inbound": .78, "sourced": .72, "agency": .65, "campus": .80, "internal": .95, "boomerang": .85}
STAGE_DWELL_DAYS = {"applied": (1, 7), "recruiter_screen": (3, 10), "hiring_manager_screen": (5, 12),
                    "onsite": (7, 14), "offer": (3, 10)}
DECLINE_REASONS = {"comp": .35, "competing_offer": .30, "role_scope": .15, "location": .10, "personal": .10}
EXPERIENCE_BY_LEVEL = {3: (0, 3), 4: (2, 6), 5: (5, 10), 6: (8, 14), 7: (12, 20)}
DEGREES = {"Bachelor's": .55, "Master's": .30, "PhD": .05, "Bootcamp / certificate": .05, "None listed": .05}
FAMILY_SKILLS = {
    "Engineering": ["Python", "Go", "Java", "TypeScript", "Kubernetes", "AWS", "distributed systems", "SQL", "React", "CI/CD", "system design", "Rust"],
    "Data": ["Python", "SQL", "statistics", "experimentation", "PyTorch", "dbt", "Spark", "causal inference", "LLM evaluation", "forecasting"],
    "Product": ["roadmapping", "user research", "SQL", "experimentation", "pricing", "stakeholder management", "API products", "AI products"],
    "Design": ["Figma", "interaction design", "prototyping", "design systems", "user research", "accessibility"],
    "Sales": ["enterprise sales", "Salesforce", "negotiation", "pipeline management", "MEDDIC", "account planning", "forecasting"],
    "Marketing": ["lifecycle marketing", "SEO", "paid acquisition", "content strategy", "marketing analytics", "brand"],
    "Customer Success": ["onboarding", "renewals", "Gainsight", "technical account management", "escalation management"],
    "People": ["recruiting", "employee relations", "HRIS", "Workday", "compensation", "people analytics", "org design"],
    "Finance": ["financial modeling", "Excel", "NetSuite", "FP&A", "revenue recognition", "audit"],
    "Legal": ["commercial contracts", "privacy law", "GDPR", "employment law", "negotiation", "compliance"],
}

# ----------------------------------------------------------------------------
# PLANTED SIGNALS (known answers for evals)
# ----------------------------------------------------------------------------
# S1 Platform attrition spike: AI talent market jump in 2024 bands (Data, Engineering) leaves Platform
#    below band, plus reorg uncertainty around the AI Platform split.
MARKET_BAND_JUMP = {2024: {"Data": 0.12, "Engineering": 0.06}}
PLATFORM_SHOCK = dict(division="Platform", start=date(2024, 6, 1), end=date(2025, 3, 31),
                      voluntary_multiplier=1.45, engagement_delta=-0.45)
# S2 One line manager in Checkout with very low manager quality; their reports quit several times faster than
#    the company until the manager is exited.
BAD_MANAGER = dict(team="Checkout", quality=-2.5, extra_voluntary_multiplier=1.8, exit_date=date(2025, 7, 15))
# S3 Referrals convert and accept better (see STAGE_PASS / OFFER_ACCEPT).
# S4 Bangalore candidates decline more often on comp; London hiring loops are slower.
BANGALORE_ACCEPT_MULTIPLIER = 0.82
LONDON_DWELL_MULTIPLIER = 1.4
# S5 High performers paid below band quit more (regretted); low performers exit involuntarily.
# S6 Hiring freeze: open growth reqs cancelled on Jan 15 2023, no growth reqs until April 2023.
HIRING_FREEZE = dict(start=date(2023, 1, 15), end=date(2023, 3, 31))
# S7 Restructuring in Enterprise: involuntary reduction on Feb 15 2023 (exit_reason 'reduction_in_force'), no backfills.
RIF = dict(when=date(2023, 2, 15), division="Enterprise", share=0.05)
