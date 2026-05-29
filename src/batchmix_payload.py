"""BatchMix payload validation helpers shared by runtime and tests."""


def is_hex_color(value):
    """Return True for #RRGGBB color strings."""
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value[1:])


def parse_field_color(value):
    """Return a normalized solid or two-color stripe tuple, or None if invalid."""
    if is_hex_color(value):
        return ("solid", value)

    if isinstance(value, str) and value.count("/") == 1:
        first, second = value.split("/", 1)
        if is_hex_color(first) and is_hex_color(second):
            return ("stripe", first, second)

    return None


def batchmix_validation_error(data):
    """Return an error string when a BatchMix payload does not match the box contract."""
    if not isinstance(data, dict):
        return "Payload must be a JSON object"

    products = data.get("products")
    if not isinstance(products, list):
        return "products must be a list"

    product_count = data.get("product_count")
    if product_count != len(products):
        return f"Product count mismatch: expected {product_count}, got {len(products)}"

    for index, product in enumerate(products, start=1):
        if not isinstance(product, dict):
            return f"Product {index} must be an object"

        legacy_jug_keys = [key for key in product.keys() if "jug" in str(key).lower()]
        if legacy_jug_keys:
            return f"Product {index} has legacy jug field: {legacy_jug_keys[0]}"

        amount_keys = [key for key in ("amount_oz", "amount_lb") if key in product]
        if len(amount_keys) != 1:
            return f"Product {index} must include exactly one amount_oz or amount_lb"

        amount_key = amount_keys[0]
        try:
            amount = float(product[amount_key])
        except (TypeError, ValueError):
            return f"Product {index} {amount_key} must be numeric"

        if amount <= 0:
            return f"Product {index} {amount_key} must be positive"

    field_colors = data.get("field_colors", [])
    if field_colors is None:
        return None
    if not isinstance(field_colors, list):
        return "field_colors must be a list"

    for index, color_entry in enumerate(field_colors, start=1):
        if not isinstance(color_entry, dict):
            return f"field_colors[{index}] must be an object"
        color = color_entry.get("color")
        if color is not None and parse_field_color(color) is None:
            return f"field_colors[{index}].color must be #RRGGBB or #RRGGBB/#RRGGBB"

    return None
