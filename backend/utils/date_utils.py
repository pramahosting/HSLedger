# backend/utils/date_utils.py
from datetime import datetime
from typing import Union

def parsedate(v) -> datetime:
    # lazy parse using dateutil
    from dateutil import parser
    return parser.parse(str(v))

def is_within_tolerance(date1: Union[str, datetime], date2: Union[str, datetime], days: int = 3) -> bool:
    """
    Return True if date1 and date2 are within +/- days.
    Accepts strings or datetimes.
    """
    if not isinstance(date1, datetime):
        date1 = parsedate(date1)
    if not isinstance(date2, datetime):
        date2 = parsedate(date2)
    diff = abs((date1.date() - date2.date()).days)
    return diff <= days
