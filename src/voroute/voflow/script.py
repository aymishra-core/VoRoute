"""Hinglish confirmation script. Isolated so LAI's Hinglish logic can port in here later."""

from voroute.models import Order


def format_amount(amount: float) -> str:
    if float(amount).is_integer():
        return str(int(amount))
    return f"{amount:.2f}"


def build_script(order: Order) -> str:
    amount = format_amount(order.amount)
    return (
        f"Namaste {order.customer_name} ji, aapka ₹{amount} ka COD order "
        "confirm karna tha. Order chahiye? Haan ya na?"
    )
