"""NYSE trading sessions: which day the next morning scan is for."""
from __future__ import annotations

import pandas as pd
from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
                                    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday)
from pandas.tseries.offsets import CustomBusinessDay


class NYSE(AbstractHolidayCalendar):
    rules = [Holiday("New Year", month=1, day=1, observance=nearest_workday), USMartinLutherKingJr, USPresidentsDay,
             GoodFriday, USMemorialDay, Holiday("Juneteenth", month=6, day=19, start_date="2022-06-19", observance=nearest_workday),
             Holiday("Independence Day", month=7, day=4, observance=nearest_workday), USLaborDay, USThanksgivingDay,
             Holiday("Christmas", month=12, day=25, observance=nearest_workday)]


SESSION = CustomBusinessDay(calendar=NYSE())


def next_session(after) -> pd.Timestamp:
    """The first trading day strictly after `after`."""
    return (pd.Timestamp(after).normalize() + SESSION).normalize()


def add_pending_session(prices: pd.DataFrame, premarket: pd.DataFrame | None = None) -> pd.DataFrame:
    """Append one empty row per stock for the session after the last close on file, so the morning
    scan is for the day that has not traded yet and sees news published since that close. The row
    carries no prices (only the premarket print, if one was recorded): every feature for it comes
    from earlier closes, and its outcome is unknown. Done in memory at load time; never stored."""
    last = pd.Timestamp(prices["date"].max())
    if prices.loc[prices["date"] == last, "close"].isna().all():
        return prices                              # already has a pending row
    nxt = next_session(last)
    pending = pd.DataFrame({"date": nxt, "ticker": prices.loc[prices["date"] == last, "ticker"].values})
    if premarket is not None and len(premarket):
        pm = premarket[pd.to_datetime(premarket["date"]) == nxt][["ticker", "premarket"]]
        pending = pending.merge(pm, on="ticker", how="left")
    pending["date"] = pending["date"].astype(prices["date"].dtype)
    return pd.concat([prices, pending], ignore_index=True)


def closed_only(prices: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop the bar of a session that has not finished. A daily download made during market hours
    includes today's bar with the current price as its "close"; storing it would let the model treat
    an unfinished day as history. A session counts as closed from 16:15 New York time."""
    now = now or pd.Timestamp.now("America/New_York")
    today = now.tz_localize(None).normalize()
    last_closed = today if (now.hour, now.minute) >= (16, 15) else today - pd.Timedelta(days=1)
    return prices[pd.to_datetime(prices["date"]) <= last_closed]
