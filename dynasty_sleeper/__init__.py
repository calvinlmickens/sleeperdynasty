from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pandas as pd


__version__ = "0.1.0"


_BaseJSONEncoder = json.JSONEncoder


class _PandasNumpySafeJSONEncoder(_BaseJSONEncoder):
    """Allow package JSON writes to safely serialize pandas/numpy scalar values."""

    def default(self, obj: Any) -> Any:
        if obj is pd.NA:
            return None

        if isinstance(obj, (pd.Timestamp, datetime, date)):
            return obj.isoformat()

        to_list = getattr(obj, "tolist", None)
        if callable(to_list):
            try:
                return to_list()
            except (TypeError, ValueError):
                pass

        item = getattr(obj, "item", None)
        if callable(item):
            try:
                value = item()
            except (TypeError, ValueError):
                pass
            else:
                if value is not obj:
                    return value

        if isinstance(obj, set):
            return list(obj)

        return super().default(obj)


# json.dump(..., indent=...) instantiates json.JSONEncoder at call time. Replacing
# the package process encoder here keeps every existing JSON write path intact
# while making numpy/pandas values safe for immutable weekly snapshots.
json.JSONEncoder = _PandasNumpySafeJSONEncoder
