# TARSIER_POS.md
**Full TarsierPOS Technical Context**
**Version:** 1.2 | **Date:** 2026-05-10

---

## Overview
TarsierPOS is an offline-first point-of-sale system for Philippine SME cafes and restaurants.
- **Model:** One-time purchase, no subscription
- **Hardware:** Dell OptiPlex 3060, ESC/POS thermal printer
- **Repo:** Dev machine only — `/home/ralph/TarsierPOS`
- **Access URL:** `https://100.100.100.100/` (Tailscale — from laptop)
- **Never work on this from the laptop**

---

## Stack
| Layer | Technology |
|---|---|
| Backend | Django 5.2 |
| Database | SQLite (busy_timeout = 30000ms) |
| Frontend | Vanilla JS PWA |
| Service Worker | tarsierpos-v43 |
| Server | gunicorn (port 9000) + nginx (HTTPS) |
| OS | Ubuntu 24.04 |
| Auth | JWT (djangorestframework-simplejwt) |
| Printer | ESC/POS thermal, PC437 encoding |
| Venv | `/home/ralph/TarsierPOS/venv` |

---

## Repo Structure
```
/home/ralph/TarsierPOS/
├── pos_config/           # Django project settings (WSGI, URLs, settings.py)
├── canteen/              # Main Django app
│   ├── models.py         # All data models
│   ├── views.py          # API endpoints + HTML views
│   ├── services.py       # Business logic (discounts, transactions)
│   ├── serializers.py    # DRF serializers
│   ├── urls.py           # URL routing
│   ├── auth_views.py     # Auth endpoints
│   ├── admin.py          # Django admin config
│   ├── permissions.py    # Role-based permissions
│   ├── validators.py     # Input validators
│   ├── receipt_service.py # Thermal receipt logic
│   ├── payment_adapters.py # GCash/Maya adapters
│   ├── management/       # Management commands (seed_demo)
│   ├── migrations/       # DB migrations, head: 0014
│   └── templates/canteen/ # HTML templates (verify path)
├── frontend/             # Static JS/CSS assets
├── media/                # Uploaded images (product photos, QR codes)
├── venv/                 # Python virtualenv
├── certs/                # Tailscale SSL certs
├── manage.py
├── requirements.txt
├── db.sqlite3
├── tarsierpos.service  # systemd unit (gunicorn)
├── tarsierpos-frontend.service # systemd unit (disabled — nginx handles)
└── nginx.conf
```

---

## Models (canteen/models.py)

| Model | Purpose |
|---|---|
| `BusinessProfile` | Site name, receipt config, OR prefix, SC/PWD/VAT rates |
| `Category` | Product categories |
| `Item` | Products — name, price, category, barcode, `track_inventory`, has custom `save()` for barcode image gen |
| `ProductVariantGroup` | Groups of variants (e.g. Size) |
| `ProductVariant` | Individual variants (e.g. Small/Medium/Large), price modifier |
| `PaymentGatewayConfig` | GCash/Maya config, `qr_image` field (migration 0014) |
| `PosTransaction` | Completed transactions, OR number via `_generate_or_number()`, uses `dj_tz.now()` |
| `PosTransactionItem` | Line items — `unit_price`, `subtotal`, `base_price`, `final_price`, `variant_selections` related |
| `TransactionItemVariant` | Stores variant selections as `group_name`/`option_name` CharFields (see FLAG-003) |
| `OfficialReceiptCounter` | Read-only in admin, auto-incremented per OR |
| `UserProfile` | Extends User, `role` field (admin/manager/cashier), initials/avatar for quick login |

---

## Authentication
- JWT-based via `djangorestframework-simplejwt`
- 3 roles: **admin**, **manager**, **cashier**
- Quick login: user selects avatar (initials-based), enters PIN
- Auth guard enforced on all protected routes

---

## Network & Services

```bash
# Services
tarsierpos.service   # gunicorn on port 9000 — ACTIVE
tarsierpos-frontend.service  # disabled — nginx handles HTTPS (FIX-047)
nginx                        # HTTPS reverse proxy — ACTIVE

# Access
https://100.100.100.100/      # via Tailscale from laptop (red-eye, Windows)
https://localhost/            # local on OptiPlex

# Firewall (ufw) — Tailscale ports opened 2026-05-10
sudo ufw allow from 100.64.0.0/10 to any port 443
sudo ufw allow from 100.64.0.0/10 to any port 80
```

---

## MCP Configuration (Claude Code CLI — OptiPlex)
Configured via `claude mcp add` — stored in `~/.claude.json`:
- `github` → `@modelcontextprotocol/server-github` — for commits, rollbacks
- `notion` → `@notionhq/notion-mcp-server` — for Kanban + CRM

Note: Nemotron (free model via OpenRouter) drops ~189 skill descriptions — MCP tools may not be available inside Nemotron agent sessions. Use Claude chat for GitHub commits and Notion updates after each Nemotron phase.

---

## Deployment

```bash
# 1. Pull
cd /home/ralph/TarsierPOS
git pull

# 2. Activate venv
source venv/bin/activate

# 3. Dependencies
pip install -r requirements.txt

# 4. Migrate
python manage.py migrate

# 5. (Optional) Demo data
python manage.py seed_demo

# 6. Static files
python manage.py collectstatic

# 7. Restart services
sudo systemctl restart tarsierpos.service
sudo systemctl reload nginx
```

---

## Migrations
| Migration | Change |
|---|---|
| 0009 | Restore discount and shift fields |
| 0010 | Item variant |
| 0011 | Variant system rework |
| 0012 | Sequential OR numbering, OfficialReceiptCounter |
| 0013 | VAT exempt + VAT amount fields on PosTransaction |
| 0014 | `qr_image` field on PaymentGatewayConfig |
| **Head** | **0014** |

---

## Business Logic (services.py)
- SC (Senior Citizen) / PWD discount: 20% per RA 9994, VAT-exempt
- Promo discounts applied per transaction item
- `select_for_update()` used for concurrent transaction safety
- **FIX-PENDING-05 (open):** `BusinessProfile.objects.first()` called twice per discounted transaction

---

## Receipt Printing
- ESC/POS thermal printer, PC437 character encoding
- PHP prefix on OR number (PC437-safe)
- SC/PWD/VAT labels pull from BusinessProfile configured rates
- `variant_selections` displayed on receipt via `TransactionItemVariant`
- Sequential OR numbers from OfficialReceiptCounter
- **ISSUE-099:** transport set by `BusinessProfile.printer_mode`
  (`disabled`/`usb`/`network`). USB uses the local `/dev/usb/lp*` device —
  the old `printer_ip = 127.0.0.1` "enable" workaround is **no longer
  needed**; existing rows auto-migrated (migration 0031). Column count is
  driven by `paper_width` × `printer_font` (see `WIDTH_FONT_COLS`).

---

## Service Worker
- Cache name: `tarsierpos-v43`
- Offline-first: caches all static assets on install
- Dynamic SW image caching handled in `dashboard.js`
- Increment cache version when deploying static asset changes

---

## Feature Inventory

### Complete / Shipped
- Full variant system: variant picker modal (FEATURE-002v2)
  - Modal: pinned footer, right-aligned buttons, Add to Cart green
- Sequential OR numbering with OfficialReceiptCounter (FEATURE-003, migration 0012)
- Quick login with initials avatars and auth guard (FEATURE-004)
- Dashboard gated to manager/admin (FEATURE-001)
- SC/PWD discount + BIR VAT exemption per RA 9994 (ISSUE-002)
- Barcode scanner respects `track_inventory` flag (ISSUE-007)
- SC/PWD/VAT receipt labels read configured rates from BusinessProfile (ISSUE-009)
- Void with stock reversal + promo cap hint (ISSUE-013)
- nginx X-Forwarded-For config (ISSUE-014)
- Z-report + X-report void filter aligned (ISSUE-015)
- SQLite `busy_timeout` 30s (ISSUE-018)
- `select_for_update` in services.py and adjust_stock (ISSUE-006, ISSUE-020)
- PHP prefix on thermal receipt using PC437-safe chars (ISSUE-022)
- `qr_image` field on PaymentGatewayConfig (migration 0014)
- `seed_demo` management command — comprehensive demo data
- Dashboard: 30-day default + dynamic SW image caching
- Txn history: defaults to last 30 days on load
- `alert()`/`confirm()` replaced with custom modals (ISSUE-005)

### Not Yet Built
- Ingredient inventory engine (raw ingredient tracking, depletion per sale, restock log, supplier tracking, par levels)
- Variants 2.0 (UX revamp, multi-select groups, display_order, availability toggle, variant-level recipe overrides)
- Cloud sync / multi-device support
- Online ordering integration
- Customer loyalty / points system
- Advanced reporting (beyond X/Z reports)
- Multi-branch support

---

## Known Open Issues

| Ticket | Location | Issue |
|---|---|---|
| FIX-PENDING-03 | views.py | Z-report void list needs `order_by('voided_at')` |
| FIX-PENDING-05 | services.py | `BusinessProfile.objects.first()` called twice per discounted transaction |
| FIX-PENDING-06 | inventory.html | `z-50` → `z-[50]` normalization on two modals |
| PAGINATION-WARN | views.py | `UnorderedObjectListWarning` on ProductVariantGroup QuerySet |
| FLAG-003 | models.py | `TransactionItemVariant` stores `group_name`/`option_name` as raw CharFields — risks orphaned strings if variants renamed/deleted. Fix in Variants 2.0. |
| FLAG-005 | models.py | `discount_type` uses blank string `''` for None instead of explicit `'none'` value |

### Closed / Confirmed Fixed
| Ticket | Status |
|---|---|
| FIX-PENDING-01 | CLOSED — `datetime.now()` never existed; `_generate_or_number()` uses `dj_tz.now()` correctly |
| SERIALIZER-REOPEN | CLOSED — no dead fields; all serializer fields map to real model fields |

---

## What NOT To Do
- **NEVER** run TarsierPOS work on the laptop — Django only on dev machine (OptiPlex)
- **NEVER** use npm/node in the TarsierPOS repo
- **NEVER** use Ruflo swarms for TarsierPOS (dev machine has no Ruflo)
- **NEVER** skip migrations — always `source venv/bin/activate` then `python manage.py migrate`
- **NEVER** hardcode tax rates — always read from `BusinessProfile`
- **NEVER** commit `.env` or secrets
- **NEVER** add duplicate helper functions
- **NEVER** make `OfficialReceiptCounter` editable in admin
- **NEVER** use `datetime.now()` — use `timezone.now()` or `dj_tz.now()`

---

## Git Workflow
```bash
cd /home/ralph/TarsierPOS
source venv/bin/activate
git add -A
git commit -m "pilot-XXX: description"
git push
```

## Ticket Naming
`ISSUE-XXX` | `FEATURE-XXX` | `pilot-XXX` | `BLOCKER-X` | `FIX-PENDING-XX` | `FLAG-XXX`

## Checkpoint Commits
| Hash | Date | Description |
|---|---|---|
| cbd3236 | 2026-05-10 | Pre-feature checkpoint — POS live, ufw Tailscale fixed, MCP configured |

# TarsierPOS — Recovery & Reboot Protocol
**Version:** 1.0 | **Date:** 2026-05-12
**Add this section to TARSIER_POS.md under a new "Recovery" heading.**

---

## Auto-Start (One-Time Setup — Already Done)

Both services are enabled to start on boot:
```bash
sudo systemctl enable tarsierpos.service
sudo systemctl enable nginx
```
gunicorn has `Restart=always` with `RestartSec=5` — it auto-restarts on crash without a reboot.

---

## After Any OptiPlex Reboot — Full Checklist

Run in this exact order:

```bash
# 1. Confirm both services came up
sudo systemctl status tarsierpos.service
sudo systemctl status nginx

# 2. Confirm ports are listening
sudo ss -tlnp | grep -E '443|9000'

# 3. Confirm Tailscale is up
tailscale status

# 4. Self-test from OptiPlex
curl -k https://localhost/ -o /dev/null -w "%{http_code}\n"
```

All four must pass before declaring the system live.

---

## Quick Restart (No Reboot)

```bash
sudo systemctl restart tarsierpos.service
sudo systemctl reload nginx
```

---

## Diagnosis Flowchart

**Browser shows ERR_CONNECTION_REFUSED**

```
1. Is gunicorn running?
   sudo systemctl status tarsierpos.service
   → Not running: sudo systemctl restart tarsierpos.service

2. Is nginx running and listening on 443?
   sudo ss -tlnp | grep 443
   → Not listening: sudo systemctl restart nginx

3. Can OptiPlex reach itself?
   curl -k https://localhost/ -o /dev/null -w "%{http_code}\n"
   → Not 200: check nginx config → sudo nginx -t

4. Is Tailscale up?
   tailscale status
   → Not active: sudo tailscale up

5. Can the laptop reach port 443?
   (Run on Windows laptop PowerShell)
   Test-NetConnection -ComputerName 100.100.100.100 -Port 443
   → TcpTestSucceeded: False → UFW issue (see below)
   → TcpTestSucceeded: True → Browser cache issue (see below)
```

**UFW fix (if TCP test fails from laptop):**
```bash
sudo ufw allow in on tailscale0 to any port 443
sudo ufw allow in on tailscale0 to any port 80
sudo ufw reload
```

**Browser cache fix (if TCP works but browser refuses):**
- Incognito window first to confirm site is live
- Main window: chrome://net-internals/#hsts → Delete domain → type `100.100.100.100`
- Or: Ctrl+Shift+Delete → Clear cached images and files

---

## Service File Reference

`/etc/systemd/system/tarsierpos.service`
- `Restart=always` — auto-restarts on crash
- `RestartSec=5` — 5s delay between restart attempts
- `After=network.target` — waits for network before starting
- `ExecStopPost=backup_db.sh` — DB backup runs on every stop

**Note:** `After=network.target` does NOT guarantee Tailscale is up.
If Tailscale takes longer than nginx to start after reboot, the UFW
tailscale0 rules will still apply — they are persistent via ufw.

---

## Access URLs
| From | URL |
|---|---|
| Laptop (red-eye) via Tailscale | https://100.100.100.100/ |
| OptiPlex locally | https://localhost/ |

---

## Verified Working State (2026-05-12)
- tarsierpos.service: enabled, Restart=always ✓
- nginx: enabled ✓
- UFW: 443/80 allowed on tailscale0 ✓
- Tailscale: active, direct connection to red-eye ✓
