"""Roles, modules and actions for gym staff.

This is the single source of truth for "who may do what". The Flutter
apps read the result from /api/v1/me/ to decide which screens to show,
but every API endpoint also checks it on the server: hiding a button is
never the security.

Defaults follow page 03 of the approved proposal
("FitDesk Pro Everywhere", 26 Oct 2026).
"""

ROLE_OWNER = "owner"
ROLE_MANAGER = "manager"
ROLE_RECEPTIONIST = "receptionist"
ROLE_TRAINER = "trainer"

ROLE_CHOICES = [
    (ROLE_OWNER, "Owner"),
    (ROLE_MANAGER, "Manager"),
    (ROLE_RECEPTIONIST, "Receptionist"),
    (ROLE_TRAINER, "Trainer"),
]
ROLES = [r for r, _ in ROLE_CHOICES]
INVITABLE_ROLES = [ROLE_MANAGER, ROLE_RECEPTIONIST, ROLE_TRAINER]

VIEW, ADD, EDIT, DELETE, EXPORT = "view", "add", "edit", "delete", "export"
ACTIONS = [VIEW, ADD, EDIT, DELETE, EXPORT]
ALL = frozenset(ACTIONS)

# Module codes are stable identifiers shared with the apps. Never rename
# one once shipped; add a new one instead.
MODULES = [
    ("dashboard", "Dashboard"),
    ("members", "Members"),
    ("attendance", "Attendance"),
    ("billing", "Billing & renewals"),
    ("plans", "Membership plans"),
    ("receipts", "Receipts / WhatsApp"),
    ("enquiries", "Enquiries"),
    ("trainers", "Trainers"),
    ("diet", "Diet & nutrition"),
    ("staff", "Staff & payroll"),
    ("salaries", "Salaries & payroll amounts"),
    ("expenses", "Expenses"),
    ("reports", "Reports & export"),
    ("settings", "Gym settings"),
    ("team", "Team & access"),
    ("licence", "Licence & plan"),
    ("support", "Support"),
]
MODULE_CODES = [m for m, _ in MODULES]

# Modules only the Owner may ever touch. A per-person override can never
# grant these to anyone else.
OWNER_ONLY_MODULES = frozenset({"team", "licence"})

# Billing: "delete" means refund / cancel a bill (owner-only by default).
ROLE_DEFAULTS = {
    ROLE_OWNER: {m: ALL for m in MODULE_CODES} | {"licence": frozenset({VIEW, EDIT})},
    ROLE_MANAGER: {
        "dashboard": {VIEW},
        "members": {VIEW, ADD, EDIT},
        "attendance": ALL,
        "billing": {VIEW, ADD, EDIT},
        "plans": {VIEW, ADD, EDIT},
        "receipts": {VIEW, ADD, EDIT},
        "enquiries": ALL,
        "trainers": {VIEW, EDIT},
        "diet": ALL,
        "staff": {VIEW},
        "expenses": {VIEW, ADD},
        "reports": {VIEW, EXPORT},
        "settings": {VIEW},
        "support": {VIEW, ADD},
    },
    ROLE_RECEPTIONIST: {
        "dashboard": {VIEW},
        "members": {VIEW, ADD, EDIT},
        "attendance": {VIEW, ADD},
        "billing": {VIEW, ADD},
        "plans": {VIEW},
        "receipts": {VIEW, ADD},
        "enquiries": {VIEW, ADD, EDIT},
        "trainers": {VIEW},
        "support": {VIEW, ADD},
    },
    ROLE_TRAINER: {
        "dashboard": {VIEW},
        "members": {VIEW},
        "attendance": {VIEW, ADD},
        "trainers": {VIEW},
        "diet": {VIEW, ADD, EDIT},
        "support": {VIEW, ADD},
    },
}

# Where a role only sees its own records (e.g. a trainer sees only the
# members assigned to them). Phase 3 data endpoints apply this filter.
SCOPE_ALL = "all"
SCOPE_OWN = "own"
ROLE_SCOPES = {
    ROLE_TRAINER: {"members": SCOPE_OWN, "attendance": SCOPE_OWN, "diet": SCOPE_OWN, "trainers": SCOPE_OWN},
}


# Who may see / pay salaries is the owner's choice per person (decided
# 28 Oct 2026): nobody but the owner by default; the owner grants it with
# set_salary_access() when inviting someone or later from Team & access.
SALARY_ACCESS = {"none": (), "view": (VIEW,), "manage": (VIEW, EDIT)}


def salary_access(user):
    p = effective_permissions(user).get("salaries", ())
    return "manage" if EDIT in p and VIEW in p else ("view" if VIEW in p else "none")


def role_defaults(role):
    return {m: set(ROLE_DEFAULTS.get(role, {}).get(m, ())) for m in MODULE_CODES}


def effective_permissions(user):
    """dict module -> sorted list of allowed actions, after per-person
    overrides. Owner always gets everything and can't be overridden."""
    perms = role_defaults(user.role)
    if user.role != ROLE_OWNER:
        for ov in user.permission_overrides.all():
            if ov.module in OWNER_ONLY_MODULES or ov.module not in perms or ov.action not in ACTIONS:
                continue
            if ov.allowed:
                perms[ov.module].add(ov.action)
            else:
                perms[ov.module].discard(ov.action)
    return {m: sorted(a, key=ACTIONS.index) for m, a in perms.items()}


def scope_for(user, module):
    return ROLE_SCOPES.get(user.role, {}).get(module, SCOPE_ALL)


def has_perm(user, module, action):
    return action in effective_permissions(user).get(module, ())
