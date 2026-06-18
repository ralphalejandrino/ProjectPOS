from django.db import migrations, models


def explode_variant_only_rows(apps, schema_editor):
    """BUG-013: backfill existing variant-only recipe lines (item NULL) into
    per-item rows so the new recipe_item_required constraint holds and per-item
    variant recipes are isolated.

    A legacy variant-only row depleted for EVERY item whose effective variant
    groups include that option (services._deplete_ingredients filtered on
    variant alone). To preserve that exact behaviour, each such row is cloned
    once per effective item (item set, same variant/ingredient/quantity/mode),
    and the original item-NULL row is deleted. A row whose option is effective
    on no item could never be selected on a sale, so it is simply dropped.
    """
    RecipeIngredient = apps.get_model('canteen', 'RecipeIngredient')
    Item = apps.get_model('canteen', 'Item')
    CategoryVariantGroup = apps.get_model('canteen', 'CategoryVariantGroup')
    ProductVariantGroup = apps.get_model('canteen', 'ProductVariantGroup')

    def items_for_group(group_id):
        cat_ids = list(
            CategoryVariantGroup.objects.filter(group_id=group_id)
            .values_list('category_id', flat=True)
        )
        item_ids = set(
            Item.objects.filter(category_id__in=cat_ids).values_list('id', flat=True)
        ) if cat_ids else set()
        for pid, enabled in ProductVariantGroup.objects.filter(
            group_id=group_id
        ).values_list('product_id', 'enabled'):
            if enabled:
                item_ids.add(pid)
            else:
                item_ids.discard(pid)
        return item_ids

    legacy = RecipeIngredient.objects.filter(
        item__isnull=True, variant__isnull=False
    ).select_related('variant')
    for row in legacy:
        for item_id in items_for_group(row.variant.group_id):
            RecipeIngredient.objects.create(
                item_id=item_id,
                variant_id=row.variant_id,
                ingredient_id=row.ingredient_id,
                quantity_used=row.quantity_used,
                depletion_mode=row.depletion_mode,
            )
        row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('canteen', '0042_recipeingredient_depletion_mode'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='recipeingredient',
            name='recipe_item_or_variant_not_both',
        ),
        migrations.RunPython(
            explode_variant_only_rows,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='recipeingredient',
            constraint=models.CheckConstraint(
                check=models.Q(item__isnull=False),
                name='recipe_item_required',
            ),
        ),
    ]
