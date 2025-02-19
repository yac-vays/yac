import datetime


async def now() -> datetime.datetime:
    return datetime.datetime.now()


async def timedelta(**kwargs) -> datetime.timedelta:
    return datetime.timedelta(**kwargs)


async def date_range_pattern(
    start_date: str, *, days: int = 0, weeks: int = 0, years: int = 0
) -> str:
    """
    Given a start date and a range in days/weeks/years,
    this function returns a regex (as a string) that matches only dates
    in the format YYYY-MM-DD which fall within the specified range.
    """

    def _interval_pattern(low: int, high: int) -> str:
        """
        Given two integers low and high (for days as two-digit numbers,
        e.g. 3 means "03"), return a regex pattern that matches any two-digit
        number in between.
        """
        low_tens, low_unit = low // 10, low % 10
        high_tens, high_unit = high // 10, high % 10

        if low_tens == high_tens:
            # Both numbers are in the same tens group.
            return f"{low_tens}[{low_unit}-{high_unit}]"
        else:
            patterns = []
            # For the low tens group: from the low_unit up through 9.
            patterns.append(f"{low_tens}[{low_unit}-9]")
            # If there are full tens groups in between, add those.
            for tens in range(low_tens + 1, high_tens):
                patterns.append(f"{tens}\\d")
            # The last tens group: from 0 to high_unit.
            patterns.append(f"{high_tens}[0-{high_unit}]")
            return f"(?:{'|'.join(patterns)})"

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end = start + datetime.timedelta(days=days, weeks=(weeks + 52 * years))
    if start >= end:
        raise ValueError("delta (either days, weeks or years) must be greater than 0")

    current = start
    allowed_dates = []
    while current <= end:
        allowed_dates.append(current.strftime("%Y-%m-%d"))
        current += datetime.timedelta(days=1)

    groups = {}
    for date in allowed_dates:
        prefix = date[:8]  # "YYYY-MM-"
        day = date[8:]  # "DD"
        groups.setdefault(prefix, []).append(day)

    group_patterns = []
    # For each month group, compress the allowed day numbers.
    for prefix, day_list in groups.items():
        # Convert day strings to integers for sorting and processing.
        day_nums = sorted(int(day) for day in day_list)
        # Create intervals from the sorted list.
        intervals = []
        start_day = day_nums[0]
        prev_day = day_nums[0]
        for num in day_nums[1:]:
            if num == prev_day + 1:
                prev_day = num  # extend the interval
            else:
                intervals.append((start_day, prev_day))
                start_day = num
                prev_day = num
        intervals.append((start_day, prev_day))

        # For each interval, produce a regex fragment.
        day_patterns = []
        for lo, hi in intervals:
            if lo == hi:
                day_patterns.append(f"{lo:02d}")
            else:
                day_patterns.append(_interval_pattern(lo, hi))

        if len(day_patterns) == 1:
            day_regex = day_patterns[0]
        else:
            day_regex = f"(?:{'|'.join(day_patterns)})"

        group_patterns.append(prefix + day_regex)

    if len(group_patterns) == 1:
        return f"^{group_patterns[0]}$"

    return f"^(?:{'|'.join(group_patterns)})$"
