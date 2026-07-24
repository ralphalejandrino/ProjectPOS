"""Bulk-attach product images to Items, matched by slugified filename.

Each image file's name (minus extension) is slugified and paired to the Item
whose slugified name matches, then set as Item.photo. So real source files
named like "Fruit Tea - Four Season.jpg" map to the item "Fruit Tea - Four
Season" with no manual renaming. Files that match no encoded Item (e.g. drinks
not on this branch's menu) are reported and skipped — the PROD item list is the
source of truth.

For a consistent POS grid, --normalize fits every image onto an identical
square canvas (same size + background) and writes JPEG, so mixed source sizes/
shapes still render uniformly.

Always preview first:
  python manage.py import_product_photos --photos-dir ./photos --dry-run
  python manage.py import_product_photos --photos-dir ./photos --normalize
  python manage.py import_product_photos --photos-dir ./photos --normalize --overwrite

--dry-run reports each match (with source dimensions), unmatched files, and
items still missing a photo. Existing photos are kept unless --overwrite.
"""
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from pos.models import Item

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")


class Command(BaseCommand):
    help = "Attach product images to Items, matched by slugified filename."

    def add_arguments(self, parser):
        parser.add_argument("--photos-dir", required=True,
                            help="directory of product image files (any naming)")
        parser.add_argument("--dry-run", action="store_true",
                            help="show matches/dimensions, change nothing")
        parser.add_argument("--overwrite", action="store_true",
                            help="replace photos on items that already have one")
        parser.add_argument("--include-archived", action="store_true",
                            help="also match is_active=False items")
        parser.add_argument("--normalize", action="store_true",
                            help="fit each image to a uniform square canvas (consistency)")
        parser.add_argument("--size", type=int, default=1000,
                            help="square edge in px when --normalize (default 1000)")
        parser.add_argument("--fit", choices=("contain", "cover"), default="contain",
                            help="contain = pad to square (no crop); cover = center-crop")
        parser.add_argument("--bg", default="white",
                            help="pad background color for --fit contain (default white)")

    def handle(self, *args, **opts):
        photos_dir = Path(opts["photos_dir"]).expanduser()
        if not photos_dir.is_dir():
            raise CommandError(f"--photos-dir not found: {photos_dir}")

        dry = opts["dry_run"]
        # slugified filename stem -> source path (last one wins on dup slug)
        files: dict[str, Path] = {}
        for p in sorted(photos_dir.iterdir()):
            if p.suffix.lower() in IMG_EXTS:
                files[slugify(p.stem)] = p
        if not files:
            raise CommandError(f"No images ({', '.join(IMG_EXTS)}) in {photos_dir}")

        qs = Item.objects.all()
        if not opts["include_archived"]:
            qs = qs.filter(is_active=True)
        by_slug: dict[str, list[Item]] = {}
        for item in qs:
            by_slug.setdefault(slugify(item.name or ""), []).append(item)

        set_count = skip_existing = ambiguous = no_item = 0
        matched_slugs = set()

        for slug, path in files.items():
            items = by_slug.get(slug, [])
            if not items:
                self.stdout.write(self.style.WARNING(f"  not on menu  {path.name}"))
                no_item += 1
                continue
            if len(items) > 1:
                names = ", ".join(i.name for i in items)
                self.stdout.write(self.style.ERROR(
                    f"  AMBIGUOUS   {path.name} -> {names}; skipped"))
                ambiguous += 1
                continue

            item = items[0]
            matched_slugs.add(slug)
            if item.photo and not opts["overwrite"]:
                self.stdout.write(f"  has photo   {item.name} (use --overwrite); skipped")
                skip_existing += 1
                continue

            dims = self._dims(path)
            verb = "WOULD SET" if dry else "set"
            self.stdout.write(self.style.SUCCESS(
                f"  {verb}   {item.name} <- {path.name} {dims}"))
            if not dry:
                content, fname = self._payload(path, slug, opts)
                item.photo.save(fname, content, save=True)
            set_count += 1

        for slug, items in by_slug.items():
            if slug not in matched_slugs:
                for item in items:
                    self.stdout.write(self.style.WARNING(f"  NO FILE     {item.name}"))

        head = "DRY RUN — no changes. " if dry else ""
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{head}set={set_count} skipped_existing={skip_existing} "
            f"not_on_menu={no_item} ambiguous={ambiguous} files={len(files)}"))

    def _dims(self, path: Path) -> str:
        try:
            from PIL import Image
            with Image.open(path) as im:
                return f"({im.width}x{im.height})"
        except Exception:  # noqa: BLE001
            return ""

    def _payload(self, path: Path, slug: str, opts):
        """Raw bytes, or a normalized uniform-square JPEG when --normalize."""
        if not opts["normalize"]:
            return ContentFile(path.read_bytes(), name=f"{slug}{path.suffix.lower()}"), \
                f"{slug}{path.suffix.lower()}"

        from PIL import Image, ImageColor
        size = opts["size"]
        with Image.open(path) as im:
            im = im.convert("RGBA")
            if opts["fit"] == "cover":
                # scale to fill, center-crop to square
                scale = size / min(im.width, im.height)
                im = im.resize((round(im.width * scale), round(im.height * scale)))
                left = (im.width - size) // 2
                top = (im.height - size) // 2
                im = im.crop((left, top, left + size, top + size))
                canvas = Image.new("RGB", (size, size), ImageColor.getrgb(opts["bg"]))
                canvas.paste(im, (0, 0), im)
            else:  # contain: scale to fit, pad to square
                scale = size / max(im.width, im.height)
                im = im.resize((round(im.width * scale), round(im.height * scale)))
                canvas = Image.new("RGB", (size, size), ImageColor.getrgb(opts["bg"]))
                canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
            buf = BytesIO()
            canvas.save(buf, format="JPEG", quality=88, optimize=True)
            return ContentFile(buf.getvalue(), name=f"{slug}.jpg"), f"{slug}.jpg"
