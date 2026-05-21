# DEPLOY-003 — Client Seed Receipt

Fill this in per client when you run `seed_client` on a fresh install. Keep it
with the deployment record. **Never write the generated PINs here** — hand those
off separately (they are printed to the terminal once and are not stored in git).

## Install details

| Field            | Value                                  |
|------------------|----------------------------------------|
| Client / site    | `____________________`                 |
| Business name    | `____________________` (BusinessProfile.business_name) |
| OR prefix        | `____________________` (e.g. `OR-PROD`) |
| Deployed by      | `____________________`                 |
| Date deployed    | `____________________`                 |
| Box / device     | `____________________` (e.g. M710q)    |

## Mode

**UNOFFICIAL** — all BIR identity fields (TIN, MIN, Machine Serial, POS
Accreditation No., POS Permit No.) are left blank at seed time per decision #31.
With a blank MIN, Z-reports finalize as `is_official=False` (UNOFFICIAL stamp).
The client configures BIR fields later in **Settings** once accredited; address
is also left blank for the client to fill in at install.

## OR series

The OR counter is reset at seed time, so the first receipt of each day starts at
`0001`. The current build's OR generator emits the fixed format
`OR-YYYYMMDD-NNNN`; the OR prefix recorded above is captured for the client
record/handoff and does not change the literal characters in generated OR
numbers (no prefix field exists; wiring one would need a migration).

## Accounts (names + roles only — NO PINs)

| Username   | Role    |
|------------|---------|
| `________` | admin   |
| cashier1   | cashier |
| cashier2   | cashier |
| `________` | cashier |

(Add/remove rows to match `--cashiers`. PINs are handed off out-of-band.)

## Exact command invocation used

```bash
source venv/bin/activate
python3 manage.py seed_client \
    --business-name "____________________" \
    --or-prefix "____________________" \
    --cashiers 2 \
    --admin-user admin
# add --force only when intentionally re-seeding a box that already has a profile
```

## What this produces

- Exactly **1** BusinessProfile (unofficial mode, blank BIR fields, address blank, currency PHP)
- A clean OR series (next receipt of the day = `0001`)
- **1** admin account + **N** cashier accounts (`cashier1..cashierN`), each with a random 4–6 digit PIN
- **No** demo categories, items, or transactions
