"""Hinglish confirmation script. Isolated so LAI's Hinglish logic can port in here later."""

from voroute.models import Order


def format_amount(amount: float) -> str:
    if float(amount).is_integer():
        return str(int(amount))
    return f"{amount:.2f}"


def build_script(order: Order) -> str:
    amount = format_amount(order.amount)
    return (
        f"Namaste {order.customer_name} ji. Aapka ₹{amount} ka cash on delivery "
        "order confirm karne ke liye call kiya hai. Kya aap yeh order confirm "
        "karna chahenge? Haan ya na."
    )


def build_closing(name: str, status: str) -> str:
    """Spoken after the outcome is already recorded. UNCLEAR does not say which way it went."""

    if status == "CONFIRMED":
        return f"Dhanyawaad {name} ji, aapka order confirm kar diya gaya hai."
    if status == "DECLINED":
        return (
            f"Theek hai {name} ji, aapka order cancel kar diya gaya hai. Dhanyawaad."
        )
    if status == "UNCLEAR":
        return "Dhanyawaad, hum aapse dobara sampark karenge."
    raise ValueError(f"no closing line for {status}")
