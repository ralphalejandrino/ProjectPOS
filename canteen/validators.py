from django.core.exceptions import ValidationError


def validate_non_negative_price(value):
    if value is None:
        return
    if value < 0:
        raise ValidationError("Price must not be negative.")


def validate_non_negative_quantity(value):
    if value is None:
        return
    if value < 0:
        raise ValidationError("Quantity must not be negative.")
