#!/usr/bin/env python3
# FEATURE-042: pre-commit dead-class check.
#
# Rationale: TarsierPOS's styles.css is a *purged* Tailwind v3.4.1 build —
# it contains only the classes that existed in the HTML the last time someone
# ran the Tailwind CLI. There is no build step wired into the workflow, so that
# purge never re-runs. The result: a Tailwind utility typed into HTML *after*
# the last build (e.g. `p-2`, `tracking-wide`, a new `dark:` variant) silently
# has no CSS rule and renders unstyled, and nothing flags it. This script is the
# manual substitute for Tailwind's content scan: it reports class tokens used in
# HTML that resolve to no style rule, and exits non-zero so a pre-commit hook
# can block the commit.
#
# Usage:  python3 scripts/check_dead_classes.py      (exit 0 clean / 1 = dead)
#
# A class is considered ALIVE if it is:
#   - defined in styles.css or shared-styles.css (the two CSS sources of truth),
#   - defined in a page-local <style> block (e.g. denied.html's .denied-*),
#   - referenced as a behavioural hook in page JavaScript — querySelector('.x'),
#     classList.add('x'), etc. (functional classes intentionally without a rule),
#   - in ALLOWLIST (base utilities assumed always present).
# Everything else used in a class="..." attribute is reported as dead.
#
# Exclusions: ingredients.html (loads the Tailwind Play CDN, which generates
# utilities just-in-time, so the CSS files are not its source of truth) and
# tokens built by JS interpolation (${...}) inside a class attribute.

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "frontend" / "public"
CSS_FILES = [PUBLIC / "styles.css", PUBLIC / "shared-styles.css"]

EXCLUDED_HTML = {"ingredients.html"}  # Tailwind Play CDN (JIT) — not our CSS

ALLOWLIST = {
    "flex", "grid", "hidden", "block", "inline", "inline-block", "contents",
    "container", "sr-only", "dark",
}

# A CSS class selector: a dot then a run of [A-Za-z0-9_-] or backslash-escaped
# chars. Tailwind variant selectors are escaped, e.g. .hover\:bg-gray-100 .
CSS_CLASS_RE = re.compile(r"\.((?:[A-Za-z0-9_-]|\\.)+)")
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
SCRIPT_BLOCK_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r'class\s*=\s*"([^"]*)"|class\s*=\s*\'([^\']*)\'')
INTERP_RE = re.compile(r"\$\{[^}]*\}|\{\{[^}]*\}\}")  # JS / template interpolation
DOTTED_CLASS_RE = re.compile(r"\.([A-Za-z0-9_-]+)")
# Targeted DOM-API calls — reliable even amid template literals, unlike a broad
# quoted-string scan. Captures the selector/class-name string argument.
SELECTOR_CALL_RE = re.compile(
    r"""(?:querySelector|querySelectorAll|closest|matches)\(\s*['"`]([^'"`]*)['"`]""")
CLASSNAME_CALL_RE = re.compile(
    r"""(?:getElementsByClassName|classList\.(?:add|remove|toggle|contains|replace))"""
    r"""\(([^)]*)\)""")
ARG_TOKEN_RE = re.compile(r"""['"]([^'"]*)['"]""")


def classes_from_css(text):
    text = CSS_COMMENT_RE.sub("", text)
    return {raw.replace("\\", "") for raw in CSS_CLASS_RE.findall(text)}


def defined_classes(html_texts):
    classes = set()
    for css in CSS_FILES:
        classes |= classes_from_css(css.read_text(encoding="utf-8"))
    # Page-local <style> blocks are real CSS rules too.
    for text in html_texts.values():
        for block in STYLE_BLOCK_RE.findall(text):
            classes |= classes_from_css(block)
    return classes


def js_hook_classes(html_texts):
    """Classes referenced by JavaScript as behavioural hooks, not for styling:
    `.foo` inside a selector string, or a classList.add/remove/... argument."""
    hooks = set()
    for text in html_texts.values():
        for block in SCRIPT_BLOCK_RE.findall(text):
            # querySelector('.foo .bar') etc. — pull class names out of selectors.
            for selector in SELECTOR_CALL_RE.findall(block):
                hooks.update(DOTTED_CLASS_RE.findall(selector))
            # getElementsByClassName('foo') / classList.add('foo') — bare names.
            for args in CLASSNAME_CALL_RE.findall(block):
                for token in ARG_TOKEN_RE.findall(args):
                    token = token.strip().lstrip(".")
                    if token and " " not in token:
                        hooks.add(token)
    return hooks


def used_classes(html_texts):
    """Maps class token -> set of html files using it (in class="..." attrs)."""
    used = {}
    for name, text in html_texts.items():
        for dq, sq in CLASS_ATTR_RE.findall(text):
            value = INTERP_RE.sub(" ", dq or sq)  # drop interpolated regions
            for token in value.split():
                used.setdefault(token, set()).add(name)
    return used


def main():
    html_texts = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(PUBLIC.glob("*.html"))
        if p.name not in EXCLUDED_HTML
    }

    alive = defined_classes(html_texts) | js_hook_classes(html_texts) | ALLOWLIST
    used = used_classes(html_texts)

    dead = {cls: files for cls, files in used.items() if cls not in alive}

    if not dead:
        print(f"OK: no dead classes ({len(used)} class tokens checked).")
        return 0

    print(f"DEAD CLASSES: {len(dead)} class token(s) used in HTML but defined "
          f"in no CSS rule (and not a JS hook):\n")
    for cls in sorted(dead):
        print(f"  {cls}  ({', '.join(sorted(dead[cls]))})")
    print("\nFix: add the rule to styles.css/shared-styles.css (and re-run the "
          "Tailwind build if applicable), correct the typo, or remove the class.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
