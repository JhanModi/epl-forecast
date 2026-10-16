"""Kickoff times: the sources publish naive UK local time; comparing them with logged forecasts needs UTC.

A fixed offset is wrong for half the year (GMT in winter, BST in summer), so conversion uses the
Europe/London rules. pandas resolves them from the system time-zone database, or from the `tzdata`
package that pandas itself requires on Windows; the python:3.12-slim runtime image ships the
system database.
"""

import pandas as pd

UK = "Europe/London"


def uk_to_utc(local: pd.Series) -> pd.Series:
    """Naive UK local times -> timezone-aware UTC.

    A time that is ambiguous (the repeated hour when the clocks go back) or nonexistent (the
    skipped hour when they go forward) becomes NaT instead of being guessed; `validate_kickoffs`
    reports those before anything is written.
    """
    return pd.to_datetime(local).dt.tz_localize(UK, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
