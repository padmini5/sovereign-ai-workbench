"""Step 27 spreadsheet intelligence — read-only inspection of CSV/XLSX bytes.

Safety: openpyxl runs read_only + data_only (cached values only — formulas
and macros NEVER execute on the server); csv uses the stdlib parser. All
inputs are untrusted bytes; every loop is capped; failures raise
SheetError (caller maps to FAILED/422, never a traceback). No filesystem
paths appear in outputs.
"""
from __future__ import annotations

import csv
import io

MAX_ROWS = 5000
MAX_COLS = 100
MAX_CELLS = 50000


class SheetError(Exception):
    pass


def _col_type(values: list) -> str:
    kinds = set()
    for v in values:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, (int, float)):
            kinds.add("number")
        else:
            kinds.add("text")
    if not kinds:
        return "empty"
    return kinds.pop() if len(kinds) == 1 else "mixed"


def _stats(nums: list[float]) -> dict:
    if not nums:
        return {"count": 0}
    s = sorted(nums)
    n = len(s)
    return {"count": n, "sum": round(sum(s), 4), "min": s[0], "max": s[-1],
            "avg": round(sum(s) / n, 4),
            "median": round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 4)}


def _finish_table(headers: list[str], rows: list[list], sheet: str) -> dict:
    headers = [str(h or f"col_{i + 1}")[:80] for i, h in enumerate(headers[:MAX_COLS])]
    cols = []
    for i, h in enumerate(headers):
        vals = [r[i] if i < len(r) else None for r in rows]
        missing = sum(1 for v in vals if v is None or v == "")
        nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        col = {"name": h, "type": _col_type(vals), "missing": missing}
        if nums:
            col["stats"] = _stats(nums)
        cols.append(col)
    return {"sheet": sheet, "rows": len(rows), "columns": cols}


def inspect_csv(data: bytes) -> dict:
    try:
        text = data.decode("utf-8-sig")
    except Exception:
        try:
            text = data.decode("cp1252")
        except Exception as e:
            raise SheetError(f"undecodable csv: {e}")
    try:
        parsed = list(csv.reader(io.StringIO(text)))
    except Exception as e:
        raise SheetError(f"csv parse failed: {e}")
    if not parsed:
        raise SheetError("empty spreadsheet")
    headers, rows = parsed[0], [r[:MAX_COLS] for r in parsed[1:MAX_ROWS + 1]]
    return {"sheets": [_finish_table(headers, rows, "sheet1")],
            "truncated": len(parsed) - 1 > MAX_ROWS}


def inspect_xlsx(data: bytes) -> dict:
    try:
        import openpyxl
        # read_only + data_only: cached values only; macros/formulas never run.
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise SheetError(f"xlsx open failed: {e}")
    out, cells = [], 0
    try:
        for ws in wb.worksheets:
            headers, rows = None, []
            for row in ws.iter_rows(values_only=True):
                cells += len(row or [])
                if cells > MAX_CELLS:
                    break
                vals = [(None if isinstance(v, bool) and False else v) for v in list(row or [])[:MAX_COLS]]
                if headers is None:
                    if not any(v is not None and v != "" for v in vals):
                        continue
                    headers = [str(v) if v is not None else "" for v in vals]
                    continue
                if len(rows) >= MAX_ROWS:
                    continue
                rows.append(vals)
            if headers is None:
                continue
            out.append(_finish_table(headers, rows, ws.title[:80]))
            if cells > MAX_CELLS:
                break
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if not out:
        raise SheetError("empty spreadsheet")
    return {"sheets": out, "truncated": cells > MAX_CELLS}


def inspect(kind: str, data: bytes) -> dict:
    """kind: csv|xlsx. Returns {sheets[{sheet,rows,columns[]}], truncated}."""
    if kind == "csv":
        return inspect_csv(data)
    if kind == "xlsx":
        return inspect_xlsx(data)
    raise SheetError(f"not a spreadsheet: {kind}")


def raw_rows(kind: str, data: bytes) -> tuple[list[str], list[list]]:
    """Headers + raw rows for server-side aggregation (numbers only downstream)."""
    headers: list[str] = []
    rows: list[list] = []
    if kind == "csv":
        try:
            text = data.decode("utf-8-sig")
        except Exception:
            text = data.decode("cp1252")
        parsed = list(csv.reader(io.StringIO(text)))
        if not parsed:
            raise SheetError("empty spreadsheet")
        headers = [str(h or f"col_{i + 1}")[:80] for i, h in enumerate(parsed[0][:MAX_COLS])]
        rows = [list(r[:MAX_COLS]) for r in parsed[1:MAX_ROWS + 1]]
    elif kind == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            for row in ws.iter_rows(values_only=True):
                vals = list(row or [])[:MAX_COLS]
                if not headers:
                    if not any(v is not None and v != "" for v in vals):
                        continue
                    headers = [str(v) if v is not None else "" for v in vals]
                    continue
                if len(rows) >= MAX_ROWS:
                    break
                rows.append(vals)
        finally:
            try:
                wb.close()
            except Exception:
                pass
        if not headers:
            raise SheetError("empty spreadsheet")
    else:
        raise SheetError(f"not a spreadsheet: {kind}")
    return headers, rows





def _num(v):
    """Numeric cell value or None. Numeric strings ('1000', '1,000.5') parse;
    genuine text is never coerced (returns None -> counted as skipped)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def aggregate_rows(headers: list[str], rows: list[list], revenue_cols: list[str],
                   expense_cols: list[str], group_col: str = "") -> dict:
    """Row-level aggregation over raw rows (numbers only; text skipped)."""
    totals = {"revenue": 0.0, "expenses": 0.0, "skipped_cells": 0, "rows_seen": len(rows)}
    groups: dict[str, dict] = {}
    missing = [c for c in revenue_cols + expense_cols if c not in headers]
    idx = {h: i for i, h in enumerate(headers)}
    gidx = idx.get(group_col) if group_col else None
    for r in rows:
        key = str(r[gidx])[:80] if gidx is not None and gidx < len(r) and r[gidx] not in (None, "") else "all"
        g = groups.setdefault(key, {"revenue": 0.0, "expenses": 0.0, "rows": 0})
        g["rows"] += 1
        for col in revenue_cols:
            v = r[idx[col]] if col in idx and idx[col] < len(r) else None
            n = _num(v)
            if n is None:
                if v not in (None, ""):
                    totals["skipped_cells"] += 1
                continue
            totals["revenue"] += n
            g["revenue"] += n
        for col in expense_cols:
            v = r[idx[col]] if col in idx and idx[col] < len(r) else None
            n = _num(v)
            if n is None:
                if v not in (None, ""):
                    totals["skipped_cells"] += 1
                continue
            totals["expenses"] += n
            g["expenses"] += n
    totals["revenue"] = round(totals["revenue"], 2)
    totals["expenses"] = round(totals["expenses"], 2)
    totals["profit"] = round(totals["revenue"] - totals["expenses"], 2)
    for g in groups.values():
        g["revenue"] = round(g["revenue"], 2)
        g["expenses"] = round(g["expenses"], 2)
        g["profit"] = round(g["revenue"] - g["expenses"], 2)
    return {"totals": totals, "groups": groups, "missing_columns": missing}
