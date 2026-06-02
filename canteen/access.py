"""FEATURE-044 — canonical per-user page-access definitions.

Single source of truth for which pages exist, their default role mapping, and
the effective/grantable-set computation. Role gating (the historical mechanism,
FEATURE-001 / ISSUE-066/087/097) is preserved EXACTLY as the default: a user
with no override (``User.allowed_pages is None``) gets precisely the pages their
role always granted — so no existing account changes access on deploy.

A per-user override (``User.allowed_pages`` = list of gateable page keys)
replaces the role default for the GATEABLE pages only. Non-gateable pages
('pos', 'status') always follow the role default.
"""

# key, label, default roles, sensitive (admin-only-grantable)
_PAGE_ROWS = [
    ('inventory',   'Inventory',     ('manager', 'admin'),               False),
    ('ingredients', 'Ingredients',   ('manager', 'admin'),               False),
    ('dashboard',   'Dashboard',     ('manager', 'admin'),               False),
    ('xreport',     'X-Report',      ('manager', 'admin'),               False),
    ('zreport',     'Z-Report',      ('manager', 'admin'),               False),
    ('settings',    'Settings',      ('admin',),                         True),
]

# Pages that participate in the per-user toggle UI / override.
GATEABLE_PAGES = [row[0] for row in _PAGE_ROWS]
PAGE_LABELS = {row[0]: row[1] for row in _PAGE_ROWS}
SENSITIVE_PAGES = {row[0] for row in _PAGE_ROWS if row[3]}

# Non-gateable pages always follow the role default (never user-overridable).
# 'pos' is the cashier hot path — everyone authenticated gets it.
# B13: period/insights are manager+admin reporting surfaces; remote is the
# admin-only phone ops view. Kept non-gateable (like 'status') — they follow
# role and never appear in the per-user toggle UI.
_NON_GATEABLE_DEFAULT_ROLES = {
    'pos':      ('cashier', 'manager', 'admin'),
    'status':   ('manager', 'admin'),
    'period':   ('manager', 'admin'),
    'insights': ('manager', 'admin'),
    'remote':   ('admin',),
    # FEATURE-035: one-time BIR accreditation reset — admin-only ops surface.
    'accreditation': ('admin',),
}

_DEFAULT_ROLES = {row[0]: row[2] for row in _PAGE_ROWS}
_DEFAULT_ROLES.update(_NON_GATEABLE_DEFAULT_ROLES)

ALL_PAGES = list(_NON_GATEABLE_DEFAULT_ROLES.keys()) + GATEABLE_PAGES


def role_default_gateable(role):
    """The gateable pages a role gets by default (the historical role gate)."""
    return {p for p in GATEABLE_PAGES if role in _DEFAULT_ROLES[p]}


def effective_pages(user):
    """The full set of page keys ``user`` may access right now (DB-authoritative).

    Server-side enforcement reads this (not the JWT claim) so revocation is
    immediate. 'pos' is always included for an authenticated user.
    """
    role = getattr(user, 'role', None)
    pages = {'pos'}
    # Non-gateable pages always come from the role default.
    for p, roles in _NON_GATEABLE_DEFAULT_ROLES.items():
        if role in roles:
            pages.add(p)
    allowed = getattr(user, 'allowed_pages', None)
    if allowed is None:
        pages |= role_default_gateable(role)
    else:
        pages |= (set(allowed) & set(GATEABLE_PAGES))
    return pages


def grantable_pages(requester):
    """Gateable pages ``requester`` is permitted to toggle for others.

    Bounded by the requester's own access ("no one can grant beyond their own")
    and by sensitivity (sensitive pages are admin-only-grantable). This is the
    guardrail that prevents a manager from self-/lateral-escalation.
    """
    grantable = effective_pages(requester) & set(GATEABLE_PAGES)
    if getattr(requester, 'role', None) != 'admin':
        grantable -= SENSITIVE_PAGES
    return grantable


def resolve_page_access(requester, target, requested):
    """Compute the new ``allowed_pages`` value for ``target``.

    ``requested`` is the (untrusted) list of page keys the requester wants the
    target to have. The requester may only flip bits within ``grantable_pages``;
    every other bit is preserved from the target's CURRENT effective set, so a
    manager can neither grant nor revoke pages outside their authority.

    Returns a sorted list, or ``None`` when the result equals the target's role
    default (so access keeps following role on a later role change).
    """
    manageable = grantable_pages(requester)
    current = effective_pages(target) & set(GATEABLE_PAGES)
    requested = set(requested or []) & set(GATEABLE_PAGES)
    new = (requested & manageable) | (current - manageable)
    if new == role_default_gateable(getattr(target, 'role', None)):
        return None
    return sorted(new)
