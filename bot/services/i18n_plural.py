"""Russian plural rules. Telegram bot user-facing strings only.

Russian plural: 1→singular, 2-4→genitive singular, 5-20→genitive plural,
then key cycles by mod 10 (1→singular, 2-4→genitive singular, 5-9,0→genitive plural)
with exception for 11-14 which always go to genitive plural.
"""

def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """Returns the correct Russian plural form.

    forms: (singular, genitive_singular, genitive_plural) e.g. ("день","дня","дней").
    Examples:
        plural_ru(1, ("день","дня","дней")) → "день"
        plural_ru(2, ("день","дня","дней")) → "дня"
        plural_ru(5, ("день","дня","дней")) → "дней"
        plural_ru(21, ("день","дня","дней")) → "день"
        plural_ru(11, ("день","дня","дней")) → "дней"
    """
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return forms[2]
    last = n % 10
    if last == 1:
        return forms[0]
    if last in (2, 3, 4):
        return forms[1]
    return forms[2]


# Common form tuples
DAYS = ("день", "дня", "дней")
