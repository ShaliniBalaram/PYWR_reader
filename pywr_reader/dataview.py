"""Look inside a model's data file, from inside the pywr environment.

    python dataview.py <data file> <output.json> [key]

The reader app itself runs on Flask alone — pandas and PyTables live in the
pywr environment — so h5 and xlsx can only be read out here, the same
arrangement runner.py uses for the model run.

Reads only the head of a dataset, never the whole thing: an 80-year daily
timeseries would otherwise be pulled into memory just to show a dozen rows.

Output JSON:
    {"ok": true, "kind": "h5"|"csv"|"excel", "keys": [{"key", "rows"}],
     "preview": {"columns": [...], "index": [...], "rows": [[...]],
                 "n_rows": N, "truncated": bool}}
"""

import json
import math
import sys

MAX_ROWS = 200


def _cell(value):
    """A JSON-safe cell: NaN/inf become null, numpy scalars become plain
    numbers, anything else its string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(num) or math.isinf(num):
        return None
    return int(num) if float(num).is_integer() and abs(num) < 1e15 else round(num, 6)


def _frame_preview(frame, total=None):
    head = frame.head(MAX_ROWS)
    n_rows = len(frame) if total is None else total
    n_rows = None if n_rows is None else int(n_rows)   # numpy int -> int, or
    return {                                           # json can't encode it
        "columns": [str(c) for c in head.columns],
        "index": [str(i) for i in head.index],
        "rows": [[_cell(v) for v in row] for row in head.itertuples(index=False)],
        "n_rows": n_rows,
        "n_cols": int(len(head.columns)),
        # bool(): comparing a numpy int yields numpy.bool_, which json rejects
        "truncated": bool(n_rows is not None and n_rows > MAX_ROWS),
    }


def _storer_rows(storer):
    """How many rows a key holds, without reading it.

    Table-format stores carry .nrows. Fixed-format ones (what pandas writes by
    default, and what real pywr timeseries files turn out to be) have an
    .nrows attribute that is None, and keep the length in .shape — [rows,
    cols] for a frame, a bare int for a series. Miss that and the viewer
    reports its own page size as the file's length."""
    rows = getattr(storer, "nrows", None)
    if rows is None:
        shape = getattr(storer, "shape", None)
        if isinstance(shape, int):
            rows = shape                          # SeriesFixed
        elif isinstance(shape, (list, tuple)) and shape:
            rows = shape[0]                       # FrameFixed: [rows, cols]
    try:
        return int(rows) if rows is not None else None
    except (TypeError, ValueError):
        return None


def _series_to_frame(obj):
    """h5 keys often hold a Series; show it as a one-column table."""
    import pandas as pd
    if isinstance(obj, pd.Series):
        return obj.to_frame(name=obj.name or "value")
    return obj


def read_h5(path, key=None):
    import pandas as pd
    out = {"kind": "h5", "keys": []}
    store_keys = []
    try:
        with pd.HDFStore(path, mode="r") as store:
            store_keys = [str(k) for k in store.keys()]
            for k in store_keys:
                try:
                    rows = _storer_rows(store.get_storer(k))
                except Exception:      # noqa: BLE001 — a key we can't stat
                    rows = None
                out["keys"].append({"key": k, "rows": rows})
            if key or len(store_keys) == 1:
                target = key or store_keys[0]
                out["key"] = target
                try:                    # only the head, not the whole dataset
                    frame = store.select(target, start=0, stop=MAX_ROWS)
                    total = _storer_rows(store.get_storer(target))
                except (TypeError, NotImplementedError, ValueError):
                    frame = store.get(target)   # fixed-format: no windowing
                    total = len(frame)
                out["preview"] = _frame_preview(_series_to_frame(frame), total)
    except Exception as exc:           # noqa: BLE001 — not an HDFStore?
        if not store_keys:
            return _read_h5_raw(path, key, exc)
        raise
    if not store_keys:
        return _read_h5_raw(path, key, None)
    return out


def _read_h5_raw(path, key=None, err=None):
    """A plain HDF5 that pandas didn't write — which real pywr timeseries
    files often are. List the datasets via PyTables and read the head of one,
    so the values are visible and not just the names."""
    import pandas as pd
    import tables
    out = {"kind": "h5", "keys": [], "raw": True}
    if err:
        out["note"] = (f"Not a pandas HDF store ({type(err).__name__}) — "
                       "reading it as plain HDF5.")
    with tables.open_file(path, mode="r") as fh:
        for node in fh.walk_nodes("/"):
            if isinstance(node, tables.Leaf):
                out["keys"].append({
                    "key": node._v_pathname,
                    "rows": int(node.shape[0]) if node.shape else None,
                    "shape": [int(d) for d in node.shape],
                    "dtype": str(node.dtype),
                })
        if key:
            leaf = fh.get_node(key)
            total = int(leaf.shape[0]) if leaf.shape else 0
            frame = pd.DataFrame(leaf.read(0, MAX_ROWS))
            if list(frame.columns) == [0]:
                frame.columns = ["value"]      # a bare 1-D array
            out["key"] = key
            out["preview"] = _frame_preview(frame, total)
    return out


def read_csv(path):
    import pandas as pd
    head = pd.read_csv(path, nrows=MAX_ROWS)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        total = max(sum(1 for _ in fh) - 1, 0)      # minus the header
    return {"kind": "csv", "keys": [], "preview": _frame_preview(head, total)}


def read_excel(path, key=None):
    import pandas as pd
    book = pd.ExcelFile(path)
    sheets = [str(s) for s in book.sheet_names]
    out = {"kind": "excel", "keys": [{"key": s, "rows": None} for s in sheets]}
    target = key if key in sheets else (sheets[0] if sheets else None)
    if target is not None:
        out["key"] = target
        head = book.parse(target, nrows=MAX_ROWS)
        out["preview"] = _frame_preview(head, None)
    return out


def inspect(path, key=None):
    lower = path.lower()
    if lower.endswith((".h5", ".hdf5", ".hdf")):
        return read_h5(path, key)
    if lower.endswith((".xlsx", ".xls", ".xlsm")):
        return read_excel(path, key)
    if lower.endswith((".csv", ".txt")):
        return read_csv(path)
    raise ValueError(f"don't know how to read {path.rsplit('.', 1)[-1]} files")


# --- plotting: the head is not enough, the whole column has to be read ------
PLOT_POINTS = 3000      # a line this long already exceeds the pixels it draws on
MAX_PLOT_COLS = 40


def _read_full(path, key=None):
    """The whole frame for a key — for plotting, where the head would show only
    the first weeks of an 80-year series. Returns (frame, n_rows)."""
    import pandas as pd
    lower = path.lower()
    if lower.endswith((".h5", ".hdf5", ".hdf")):
        try:
            with pd.HDFStore(path, mode="r") as store:
                keys = [str(k) for k in store.keys()]
                if not keys:
                    raise ValueError("empty store")
                target = key or (keys[0] if len(keys) == 1 else None)
                if target is None:
                    raise ValueError("this file has several keys — pick one")
                frame = _series_to_frame(store.get(target))
                return frame, len(frame)
        except Exception:               # noqa: BLE001 — plain HDF5, use tables
            import tables
            with tables.open_file(path, mode="r") as fh:
                leaf = fh.get_node(key) if key else None
                if leaf is None:
                    raise ValueError("this file has several keys — pick one")
                frame = pd.DataFrame(leaf.read())
                if list(frame.columns) == [0]:
                    frame.columns = ["value"]
                return frame, len(frame)
    if lower.endswith((".xlsx", ".xls", ".xlsm")):
        book = pd.ExcelFile(path)
        sheet = key if key in book.sheet_names else book.sheet_names[0]
        frame = book.parse(sheet)
        return _index_by_first_label(frame), len(frame)
    if lower.endswith((".csv", ".txt")):
        frame = pd.read_csv(path)
        return _index_by_first_label(frame), len(frame)
    raise ValueError(f"can't plot a {path.rsplit('.', 1)[-1]} file")


def _index_by_first_label(frame):
    """A csv/xlsx table usually leads with a label or date column — make it the
    x-axis so the plot reads against dates, not row numbers. h5 frames already
    carry a real index, so this only touches the default RangeIndex."""
    import pandas as pd
    if (len(frame.columns)
            and frame.index.equals(pd.RangeIndex(len(frame)))
            and not pd.api.types.is_numeric_dtype(frame[frame.columns[0]])):
        return frame.set_index(frame.columns[0])
    return frame


def read_series(path, key=None):
    frame, n_rows = _read_full(path, key)
    numeric = frame.select_dtypes(include="number")
    all_cols = list(numeric.columns)
    cols = all_cols[:MAX_PLOT_COLS]
    numeric = numeric[cols]

    downsampled = False
    if len(numeric) > PLOT_POINTS:              # thin it, keeping the last point
        step = int(math.ceil(len(numeric) / PLOT_POINTS))
        kept = numeric.iloc[::step]
        if len(numeric) and kept.index[-1] != numeric.index[-1]:
            import pandas as pd
            kept = pd.concat([kept, numeric.iloc[[-1]]])
        numeric = kept
        downsampled = True

    return {
        "kind": "series",
        "key": key,
        "dates": [str(i) for i in numeric.index],
        "series": [{"name": str(c),
                    "values": [_cell(v) for v in numeric[c].tolist()]}
                   for c in cols],
        "n_rows": int(n_rows),
        "downsampled": downsampled,
        "cols_truncated": len(all_cols) > MAX_PLOT_COLS,
        "n_series_available": len(all_cols),
    }


def main():
    args = [a for a in sys.argv[1:] if a != "--series"]
    series_mode = "--series" in sys.argv
    path, out_path = args[0], args[1]
    key = args[2] if len(args) > 2 else None
    try:
        result = (read_series(path, key or None) if series_mode
                  else inspect(path, key or None))
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 — report it to the app
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
