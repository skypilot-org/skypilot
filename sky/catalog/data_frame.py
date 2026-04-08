"""Lightweight pandas-compatible DataFrame for catalog queries.

Replaces pandas for SkyPilot's catalog system. Operates on lists of dicts
and implements only the subset of the pandas API that SkyPilot uses.
"""
import copy
import csv
import functools
import io
import math
import re
from typing import (Any, Callable, Dict, Hashable, Iterator, List, Optional,
                    Sequence, Set, Tuple, Union)


def isna(value: Any) -> Any:
    """Check if a value is None or NaN.

    If *value* is a Series, returns a boolean Series (element-wise).
    Otherwise returns a scalar bool.
    """
    # Defer the isinstance check to avoid a circular reference at
    # module level (Series is defined later in this file).
    if hasattr(value, '_data') and hasattr(value, 'isna'):
        return value.isna()
    if value is None:
        return True
    try:
        return isinstance(value, float) and math.isnan(value)
    except (TypeError, ValueError):
        return False


isnull = isna


def _convert_value(value: Optional[str]) -> Any:
    """Convert a CSV string value to the appropriate Python type."""
    if value is None or value == '':
        return None
    low = value.lower()
    if low == 'nan':
        return None
    if low == 'true':
        return True
    if low == 'false':
        return False
    try:
        if '.' in value or 'e' in low:
            return float(value)
        return int(value)
    except (ValueError, OverflowError):
        return value


def _safe_cmp(a: Any, b: Any, op: Callable) -> bool:
    """Compare two values, returning False if either is None/NaN."""
    if isna(a) or isna(b):
        return False
    return op(a, b)


def _safe_arith(a: Any, b: Any, op: Callable) -> Any:
    """Arithmetic on two values, returning None if either is None/NaN."""
    if isna(a) or isna(b):
        return None
    return op(a, b)


def read_csv(path_or_buffer: Any,
             encoding: str = 'utf-8',
             **kwargs) -> 'DataFrame':
    """Read a CSV file into a DataFrame."""

    def _read(f):
        reader = csv.DictReader(f)
        data = [{k: _convert_value(v) for k, v in row.items()} for row in reader
               ]
        return DataFrame(data,
                         list(reader.fieldnames) if reader.fieldnames else None)

    if hasattr(path_or_buffer, 'read'):
        return _read(path_or_buffer)
    with open(path_or_buffer, encoding=encoding, newline='') as f:
        return _read(f)


def concat(frames: List['DataFrame'],
           ignore_index: bool = False) -> 'DataFrame':
    """Concatenate DataFrames."""
    result_data: List[dict] = []
    columns = None
    for frame in frames:
        if isinstance(frame, DataFrame):
            if columns is None:
                columns = list(frame._columns)
            result_data.extend(frame._data)
    return DataFrame(result_data, columns)


def merge(
    left: 'DataFrame',
    right: 'DataFrame',
    on: Any = None,
    how: str = 'inner',
    suffixes: Tuple[str, str] = ('_x', '_y')) -> 'DataFrame':
    """Merge two DataFrames."""
    return left.merge(right, on=on, how=how, suffixes=suffixes)


class Row:
    """A dict-like wrapper for a single DataFrame row."""
    __slots__ = ('_data',)

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self) -> str:
        return repr(self._data)


class _StringAccessor:
    """String methods for a Series (.str accessor)."""

    def __init__(self, series: 'Series'):
        self._data = series._data

    def lower(self) -> 'Series':
        return Series(
            [v.lower() if isinstance(v, str) else v for v in self._data])

    def upper(self) -> 'Series':
        return Series(
            [v.upper() if isinstance(v, str) else v for v in self._data])

    def strip(self) -> 'Series':
        return Series(
            [v.strip() if isinstance(v, str) else v for v in self._data])

    def contains(self,
                 pat: str,
                 case: bool = True,
                 regex: bool = True) -> 'Series':
        if regex:
            flags = 0 if case else re.IGNORECASE
            pattern = re.compile(pat, flags)
            return Series([
                bool(pattern.search(str(v))) if not isna(v) else False
                for v in self._data
            ])
        if not case:
            pat = pat.lower()
        return Series([(pat in (str(v) if case else str(v).lower()))
                       if not isna(v) else False for v in self._data])

    def fullmatch(self, pat: str, case: bool = True) -> 'Series':
        flags = 0 if case else re.IGNORECASE
        pattern = re.compile(pat, flags)
        return Series([
            bool(pattern.fullmatch(str(v))) if not isna(v) else False
            for v in self._data
        ])

    def startswith(self, prefix: Union[str, Tuple[str, ...]]) -> 'Series':
        return Series([
            str(v).startswith(prefix) if not isna(v) else False
            for v in self._data
        ])


class Series:
    """A column of data with pandas-compatible operations."""
    __slots__ = ('_data', '_name')

    def __init__(self, data: list, name: Optional[str] = None):
        self._data = data
        self._name = name

    @property
    def str(self) -> _StringAccessor:
        return _StringAccessor(self)

    @property
    def iloc(self) -> '_SeriesIloc':
        return _SeriesIloc(self)

    @property
    def values(self) -> list:
        return self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator:
        return iter(self._data)

    def __bool__(self) -> bool:
        if len(self._data) == 1:
            return bool(self._data[0])
        raise ValueError(
            'The truth value of a Series is ambiguous. Use a.all() or a.any().')

    # Comparison operators
    def __eq__(self, other: Any) -> 'Series':  # type: ignore[override]
        if isinstance(other, Series):
            return Series([a == b for a, b in zip(self._data, other._data)])
        return Series([v == other for v in self._data])

    def __ne__(self, other: Any) -> 'Series':  # type: ignore[override]
        if isinstance(other, Series):
            return Series([a != b for a, b in zip(self._data, other._data)])
        return Series([v != other for v in self._data])

    def __ge__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_cmp(a, b, lambda x, y: x >= y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_cmp(v, other, lambda x, y: x >= y) for v in self._data])

    def __le__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_cmp(a, b, lambda x, y: x <= y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_cmp(v, other, lambda x, y: x <= y) for v in self._data])

    def __gt__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_cmp(a, b, lambda x, y: x > y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_cmp(v, other, lambda x, y: x > y) for v in self._data])

    def __lt__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_cmp(a, b, lambda x, y: x < y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_cmp(v, other, lambda x, y: x < y) for v in self._data])

    # Boolean operators
    def __and__(self, other: 'Series') -> 'Series':
        return Series(
            [bool(a) and bool(b) for a, b in zip(self._data, other._data)])

    def __or__(self, other: 'Series') -> 'Series':
        return Series(
            [bool(a) or bool(b) for a, b in zip(self._data, other._data)])

    def __invert__(self) -> 'Series':
        return Series([not v for v in self._data])

    # Arithmetic operators
    def __add__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_arith(a, b, lambda x, y: x + y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_arith(v, other, lambda x, y: x + y) for v in self._data])

    def __radd__(self, other: Any) -> 'Series':
        return Series(
            [_safe_arith(other, v, lambda x, y: x + y) for v in self._data])

    def __sub__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_arith(a, b, lambda x, y: x - y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_arith(v, other, lambda x, y: x - y) for v in self._data])

    def __rsub__(self, other: Any) -> 'Series':
        return Series(
            [_safe_arith(other, v, lambda x, y: x - y) for v in self._data])

    def __mul__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_arith(a, b, lambda x, y: x * y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_arith(v, other, lambda x, y: x * y) for v in self._data])

    def __rmul__(self, other: Any) -> 'Series':
        return self.__mul__(other)

    def __truediv__(self, other: Any) -> 'Series':
        if isinstance(other, Series):
            return Series([
                _safe_arith(a, b, lambda x, y: x / y)
                for a, b in zip(self._data, other._data)
            ])
        return Series(
            [_safe_arith(v, other, lambda x, y: x / y) for v in self._data])

    def __abs__(self) -> 'Series':
        return Series([abs(v) if not isna(v) else None for v in self._data])

    # Aggregation / query methods
    def isna(self) -> 'Series':
        return Series([_isna_val(v) for v in self._data])

    def isnull(self) -> 'Series':
        return self.isna()

    def notna(self) -> 'Series':
        return Series([not _isna_val(v) for v in self._data])

    def fillna(self, value: Any) -> 'Series':
        return Series([value if isna(v) else v for v in self._data], self._name)

    def unique(self) -> list:
        seen: Set[Any] = set()
        result: list = []
        for v in self._data:
            if v not in seen:
                seen.add(v)
                result.append(v)
        return result

    def tolist(self) -> list:
        return list(self._data)

    def astype(self, dtype: type) -> 'Series':
        return Series([dtype(v) if not isna(v) else None for v in self._data],
                      self._name)

    def idxmin(self) -> Optional[int]:
        min_val = None
        min_idx = None
        for i, v in enumerate(self._data):
            if isna(v):
                continue
            if min_val is None or v < min_val:
                min_val = v
                min_idx = i
        return min_idx

    def min(self) -> Any:
        vals = [v for v in self._data if not isna(v)]
        return min(vals) if vals else None

    def all(self) -> bool:
        return all(self._data)

    def isin(self, values: Any) -> 'Series':
        vset = set(values)
        return Series([v in vset for v in self._data])

    def apply(self, func: Callable) -> 'Series':
        return Series([func(v) for v in self._data], self._name)

    def drop_duplicates(self) -> 'Series':
        seen: Set[Any] = set()
        result: list = []
        for v in self._data:
            if v not in seen:
                seen.add(v)
                result.append(v)
        return Series(result, self._name)


def _isna_val(v: Any) -> bool:
    """Fast isna for use inside list comprehensions."""
    if v is None:
        return True
    if isinstance(v, float):
        try:
            return math.isnan(v)
        except (TypeError, ValueError):
            pass
    return False


class _SeriesIloc:
    __slots__ = ('_s',)

    def __init__(self, s: Series):
        self._s = s

    def __getitem__(self, key: int) -> Any:
        return self._s._data[key]


class _IlocIndexer:
    __slots__ = ('_df',)

    def __init__(self, df: 'DataFrame'):
        self._df = df

    def __getitem__(self, key):
        if isinstance(key, int):
            return Row(self._df._data[key])
        if isinstance(key, list):
            return DataFrame([self._df._data[i] for i in key],
                             self._df._columns)
        if isinstance(key, slice):
            return DataFrame(self._df._data[key], self._df._columns)
        raise TypeError(f'Unsupported iloc key: {type(key)}')


class _LocIndexer:
    __slots__ = ('_df',)

    def __init__(self, df: 'DataFrame'):
        self._df = df

    def __getitem__(self, key):
        if isinstance(key, tuple):
            mask, col = key
            filtered = self._df[mask] if isinstance(
                mask, Series) else self._df[Series(list(mask))]
            return filtered[col]
        if isinstance(key, Series):
            return self._df[key]
        if isinstance(key, list):
            return DataFrame([self._df._data[i] for i in key],
                             self._df._columns)
        if isinstance(key, (int, type(None))):
            if key is None:
                raise KeyError('None')
            return Row(self._df._data[key])
        raise TypeError(f'Unsupported loc key: {type(key)}')


class DataFrame:
    """Lightweight pandas-compatible DataFrame."""

    def __init__(self, data: Any = None, columns: Optional[List[str]] = None):
        if data is None:
            data = []
        if isinstance(data, list):
            if data and isinstance(data[0], (list, tuple)):
                assert columns is not None, 'columns required for list-of-tuples'
                self._data: List[dict] = [
                    dict(zip(columns, row)) for row in data
                ]
            elif data and isinstance(data[0], dict):
                self._data = [dict(row) for row in data]
            else:
                self._data = []
        elif isinstance(data, dict):
            keys = list(data.keys())
            if not keys or not data[keys[0]]:
                self._data = []
            else:
                n = len(data[keys[0]])
                self._data = [{k: data[k][i] for k in keys} for i in range(n)]
            if columns is None:
                columns = keys
        else:
            self._data = list(data) if data else []

        if columns is not None:
            self._columns: List[str] = list(columns)
        elif self._data:
            self._columns = list(self._data[0].keys())
        else:
            self._columns = []

    @property
    def empty(self) -> bool:
        return len(self._data) == 0

    @property
    def columns(self) -> List[str]:
        return self._columns

    @property
    def iloc(self) -> _IlocIndexer:
        return _IlocIndexer(self)

    @property
    def loc(self) -> _LocIndexer:
        return _LocIndexer(self)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        if not self._data:
            return f'DataFrame(columns={self._columns})'
        preview = self._data[:3]
        suffix = ', ...' if len(self._data) > 3 else ''
        return f'DataFrame({preview}{suffix})'

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __getitem__(self, key):
        if isinstance(key, str):
            return Series([row.get(key) for row in self._data], name=key)
        if isinstance(key, list) and key and isinstance(key[0], str):
            return DataFrame(
                [{k: row.get(k) for k in key} for row in self._data], key)
        if isinstance(key, Series):
            return DataFrame(
                [row for row, m in zip(self._data, key._data) if m],
                self._columns)
        raise KeyError(f'Unsupported key: {key!r}')

    def __setitem__(self, key: str, value: Any):
        if isinstance(value, Series):
            vals = value._data
        elif isinstance(value, (list, tuple)):
            vals = list(value)
        else:
            vals = [value] * len(self._data)
        if key not in self._columns:
            self._columns = list(self._columns) + [key]
        for row, val in zip(self._data, vals):
            row[key] = val

    def copy(self) -> 'DataFrame':
        return DataFrame([dict(row) for row in self._data], list(self._columns))

    def sort_values(self, by: Any = None, ascending: Any = True) -> 'DataFrame':
        if isinstance(by, str):
            by = [by]
        if isinstance(ascending, bool):
            ascending = [ascending] * len(by)

        def _cmp(row_a, row_b):
            for col, asc in zip(by, ascending):
                a, b = row_a.get(col), row_b.get(col)
                a_na, b_na = isna(a), isna(b)
                if a_na and b_na:
                    continue
                if a_na:
                    return 1
                if b_na:
                    return -1
                if a < b:
                    return -1 if asc else 1
                if a > b:
                    return 1 if asc else -1
            return 0

        sorted_data = sorted(self._data, key=functools.cmp_to_key(_cmp))
        return DataFrame(sorted_data, self._columns)

    def groupby(self, by: str) -> 'GroupBy':
        return GroupBy(self, by)

    def merge(
        self,
        other: 'DataFrame',
        on: Any = None,
        how: str = 'inner',
        suffixes: Tuple[str, str] = ('_x', '_y')
    ) -> 'DataFrame':
        if isinstance(on, str):
            on = [on]
        on_set = set(on)
        left_suffix, right_suffix = suffixes
        left_non_key = [c for c in self._columns if c not in on_set]
        right_non_key = [c for c in other._columns if c not in on_set]
        overlap = set(left_non_key) & set(right_non_key)

        # Result column ordering
        result_columns = list(on)
        for c in left_non_key:
            result_columns.append(c + left_suffix if c in overlap else c)
        for c in right_non_key:
            result_columns.append(c + right_suffix if c in overlap else c)

        # Index the right side
        right_idx: Dict[tuple, list] = {}
        for row in other._data:
            key = tuple(row.get(k) for k in on)
            right_idx.setdefault(key, []).append(row)

        result: List[dict] = []
        for left_row in self._data:
            key = tuple(left_row.get(k) for k in on)
            right_rows = right_idx.get(key, [])
            if right_rows:
                for right_row in right_rows:
                    merged: dict = {k: left_row.get(k) for k in on}
                    for c in left_non_key:
                        name = c + left_suffix if c in overlap else c
                        merged[name] = left_row.get(c)
                    for c in right_non_key:
                        name = c + right_suffix if c in overlap else c
                        merged[name] = right_row.get(c)
                    result.append(merged)
            elif how == 'left':
                merged = {k: left_row.get(k) for k in on}
                for c in left_non_key:
                    name = c + left_suffix if c in overlap else c
                    merged[name] = left_row.get(c)
                for c in right_non_key:
                    name = c + right_suffix if c in overlap else c
                    merged[name] = None
                result.append(merged)
        return DataFrame(result, result_columns)

    def dropna(self,
               subset: Any = None,
               inplace: bool = False,
               how: str = 'any') -> Optional['DataFrame']:
        if subset is None:
            subset = self._columns
        if isinstance(subset, str):
            subset = [subset]
        if how == 'any':
            filtered = [
                row for row in self._data
                if not any(isna(row.get(c)) for c in subset)
            ]
        else:  # how == 'all'
            filtered = [
                row for row in self._data
                if not all(isna(row.get(c)) for c in subset)
            ]
        if inplace:
            self._data = filtered
            return None
        return DataFrame(filtered, self._columns)

    def drop_duplicates(self,
                        subset: Any = None,
                        keep: str = 'first') -> 'DataFrame':
        if subset is None:
            subset = list(self._columns)
        if isinstance(subset, str):
            subset = [subset]
        seen: set = set()
        result: list = []
        for row in self._data:
            key = tuple(row.get(c) for c in subset)
            if key not in seen:
                seen.add(key)
                result.append(row)
        return DataFrame(result, self._columns)

    def drop(self, columns: Any = None) -> 'DataFrame':
        if columns is None:
            return self.copy()
        if isinstance(columns, str):
            columns = [columns]
        drop_set = set(columns)
        new_cols = [c for c in self._columns if c not in drop_set]
        new_data = [{k: v
                     for k, v in row.items()
                     if k not in drop_set}
                    for row in self._data]
        return DataFrame(new_data, new_cols)

    def rename(self,
               columns: Optional[Dict[str, str]] = None,
               inplace: bool = False) -> Optional['DataFrame']:
        if columns is None:
            return None if inplace else self.copy()
        new_cols = [columns.get(c, c) for c in self._columns]
        new_data = [
            {columns.get(k, k): v for k, v in row.items()} for row in self._data
        ]
        if inplace:
            self._columns = new_cols
            self._data = new_data
            return None
        return DataFrame(new_data, new_cols)

    def reset_index(self, drop: bool = False) -> 'DataFrame':
        return DataFrame(list(self._data), list(self._columns))

    def apply(self, func: Callable, axis: Any = 0) -> 'Series':
        if axis == 1 or axis == 'columns':
            return Series([func(Row(row)) for row in self._data])
        raise NotImplementedError('Only axis=1 is supported')

    def iterrows(self) -> Iterator[Tuple[int, Row]]:
        for i, row in enumerate(self._data):
            yield i, Row(row)

    def assign(self, **kwargs) -> 'DataFrame':
        result = self.copy()
        for key, value in kwargs.items():
            if isinstance(value, Series):
                result[key] = value
            elif callable(value):
                result[key] = value(result)
            else:
                result[key] = value
        return result

    def to_csv(self, path: str, index: bool = False) -> None:
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self._columns)
            writer.writeheader()
            for row in self._data:
                csv_row = {}
                for col in self._columns:
                    val = row.get(col)
                    csv_row[col] = '' if val is None else val
                writer.writerow(csv_row)

    def to_records(self, index: bool = False) -> List[tuple]:
        return [
            tuple(row.get(col) for col in self._columns) for row in self._data
        ]

    def fillna(self, value: Any) -> 'DataFrame':
        new_data = []
        for row in self._data:
            new_data.append({
                col: (value if isna(row.get(col)) else row.get(col))
                for col in self._columns
            })
        return DataFrame(new_data, self._columns)

    def head(self, n: int = 5) -> 'DataFrame':
        return DataFrame(self._data[:n], self._columns)

    def astype(self, dtype_map: Any) -> 'DataFrame':
        if isinstance(dtype_map, dict):
            result = self.copy()
            for col, dtype in dtype_map.items():
                result[col] = result[col].astype(dtype)
            return result
        raise NotImplementedError('Only dict dtype_map is supported')


class GroupBy:
    """Groupby result supporting iteration, column selection, and agg."""

    def __init__(self, df: DataFrame, by: str):
        self._df = df
        self._by = by
        self._groups: Dict[Any, List[int]] = {}
        for i, row in enumerate(df._data):
            key = row.get(by)
            self._groups.setdefault(key, []).append(i)

    def __iter__(self) -> Iterator[Tuple[Any, DataFrame]]:
        for key, indices in self._groups.items():
            sub_df = DataFrame([self._df._data[i] for i in indices],
                               self._df._columns)
            yield key, sub_df

    def __getitem__(self, col: str) -> '_GroupByColumn':
        return _GroupByColumn(self, col)

    def agg(self, **kwargs) -> DataFrame:
        """Named aggregation: .agg(out_name=('col', 'func'), ...)"""
        agg_funcs = {'min': min, 'max': max, 'sum': sum}
        result: list = []
        for key, indices in self._groups.items():
            row: dict = {self._by: key}
            for out_name, (col, func_name) in kwargs.items():
                vals = [
                    self._df._data[i].get(col)
                    for i in indices
                    if not isna(self._df._data[i].get(col))
                ]
                if func_name == 'mean':
                    row[out_name] = (sum(vals) / len(vals)) if vals else None
                elif func_name in agg_funcs:
                    row[out_name] = agg_funcs[func_name](vals) if vals else None
                elif func_name == 'count':
                    row[out_name] = len(vals)
            result.append(row)
        columns = [self._by] + list(kwargs.keys())
        return DataFrame(result, columns)


class _GroupByColumn:
    """Result of GroupBy['column'] — supports .apply()."""
    __slots__ = ('_gb', '_col')

    def __init__(self, gb: GroupBy, col: str):
        self._gb = gb
        self._col = col

    def apply(self, func: Callable) -> dict:
        result: dict = {}
        for key, indices in self._gb._groups.items():
            vals = [self._gb._df._data[i].get(self._col) for i in indices]
            result[key] = func(vals)
        return result
