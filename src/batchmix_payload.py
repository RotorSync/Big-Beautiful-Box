"""BatchMix payload validation helpers shared by runtime and tests."""

import copy


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

        has_rate = "rate_per_acre" in product
        has_rate_unit = "rate_unit" in product
        if has_rate != has_rate_unit:
            return f"Product {index} rate_per_acre and rate_unit must be sent together"

        if has_rate:
            try:
                rate = float(product["rate_per_acre"])
            except (TypeError, ValueError):
                return f"Product {index} rate_per_acre must be numeric"

            if rate <= 0:
                return f"Product {index} rate_per_acre must be positive"

            rate_unit = product.get("rate_unit")
            if not isinstance(rate_unit, str):
                return f"Product {index} rate_unit must be a string"

            expected_rate_unit = "oz/ac" if amount_key == "amount_oz" else "lb/ac"
            if rate_unit.strip() != expected_rate_unit:
                return f"Product {index} rate_unit must be {expected_rate_unit}"

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


def _positive_float(value, field_name):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc

    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _nonnegative_float(value, field_name):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc

    if parsed < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return parsed


def _scale_numeric_field(data, key, ratio):
    if key not in data:
        return None
    try:
        data[key] = float(data[key]) * ratio
        return data[key]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _recalculate_product_amount_from_rate(product, total_acres):
    if total_acres is None or "rate_per_acre" not in product:
        return False

    rate = _positive_float(product.get("rate_per_acre"), "rate_per_acre")
    rate_unit = product.get("rate_unit")
    if isinstance(rate_unit, str):
        rate_unit = rate_unit.strip()
    if "amount_oz" in product and rate_unit == "oz/ac":
        product["amount_oz"] = rate * total_acres
        return True
    if "amount_lb" in product and rate_unit == "lb/ac":
        product["amount_lb"] = rate * total_acres
        return True
    return False


def scaled_batchmix_payload_for_water(data, new_water_needed):
    """Return a copy of a BatchMix payload scaled to a new water target."""
    old_water_needed = _positive_float(data.get("water_needed"), "water_needed")
    new_water_needed = _nonnegative_float(new_water_needed, "new_water_needed")
    ratio = new_water_needed / old_water_needed

    scaled = copy.deepcopy(data)
    scaled["water_needed"] = new_water_needed

    total_acres = _scale_numeric_field(scaled, "total_acres", ratio)
    _scale_numeric_field(scaled, "total_liquid", ratio)

    products = scaled.get("products", [])
    if isinstance(products, list):
        for product in products:
            if not isinstance(product, dict):
                continue
            if _recalculate_product_amount_from_rate(product, total_acres):
                continue
            _scale_numeric_field(product, "amount_oz", ratio)
            _scale_numeric_field(product, "amount_lb", ratio)

    return scaled
