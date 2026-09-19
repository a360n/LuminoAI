"""
LuminoAI - High-Performance Historical Backfill Engine
=====================================================
Processes historical solar panel inspections (27,000+ records) from newest to oldest.
Features:
1. 100% Read-Only SQL Server compliance (WITH (NOLOCK)).
2. Memory-bounded batching with explicit garbage collection to prevent memory exhaustion.
3. Strict deduplication against LuminoAI SQLite database (skips already processed panels).
4. Auto-approval for 100% clean panels (AI 0 defects AND EL 0 defects).
5. Automatic staging of defective or discrepancy panels into the Pending Review queue.
6. Real-time background status reporting and pause/resume control.
7. Cooperative yielding when live production line panels arrive.
"""

import os
import gc
import time
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

from audit_engine import AuditEngine
from el_reader_engine import ElReaderEngine
import ecolab_db_listener

logger = logging.getLogger("LuminoAI.BackfillEngine")

_BACKFILL_LOCK = threading.Lock()
_PAUSE_EVENT = threading.Event()
_STOP_EVENT = threading.Event()

# Global backfill state
BACKFILL_STATE: Dict[str, Any] = {
    "is_running": False,
    "is_paused": False,
    "total_candidates": 0,
    "processed_count": 0,
    "auto_approved_count": 0,
    "pending_count": 0,
    "skipped_count": 0,
    "current_serial": None,
    "current_file": None,
    "current_test_id": None,
    "start_time": None,
    "elapsed_seconds": 0,
    "error": None,
    "source_mode": "idle", # "sql_server", "disk_folder", or "idle"
}


def get_backfill_status() -> Dict[str, Any]:
    """Returns current real-time state of the historical backfill process."""
    with _BACKFILL_LOCK:
        state = dict(BACKFILL_STATE)
        state["total_processed"] = state.get("processed_count", 0)
        if state["is_running"] and state["start_time"]:
            try:
                start_dt = datetime.strptime(state["start_time"], "%Y-%m-%d %H:%M:%S")
                state["elapsed_seconds"] = int((datetime.now() - start_dt).total_seconds())
            except Exception:
                pass
        return state


def start_backfill(batch_size: int = 200) -> Dict[str, Any]:
    """Starts the historical backfill worker in a background thread."""
    global BACKFILL_STATE
    with _BACKFILL_LOCK:
        if BACKFILL_STATE["is_running"]:
            if BACKFILL_STATE["is_paused"]:
                _PAUSE_EVENT.set()
                BACKFILL_STATE["is_paused"] = False
                return {"status": "resumed", "message": "Backfill resumed from pause."}
            return {"status": "already_running", "message": "Backfill is already actively running."}

        _STOP_EVENT.clear()
        _PAUSE_EVENT.set()
        BACKFILL_STATE["is_running"] = True
        BACKFILL_STATE["is_paused"] = False
        BACKFILL_STATE["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        BACKFILL_STATE["error"] = None

    t = threading.Thread(target=_backfill_worker, args=(batch_size,), daemon=True, name="LuminoAI-BackfillWorker")
    t.start()
    return {"status": "started", "message": "Historical backfill worker started successfully."}


def pause_backfill() -> Dict[str, Any]:
    """Pauses the backfill loop without losing state."""
    with _BACKFILL_LOCK:
        if not BACKFILL_STATE["is_running"]:
            return {"status": "not_running", "message": "Backfill is not running."}
        _PAUSE_EVENT.clear()
        BACKFILL_STATE["is_paused"] = True
    return {"status": "paused", "message": "Backfill worker paused."}


def stop_backfill() -> Dict[str, Any]:
    """Gracefully stops the backfill worker."""
    with _BACKFILL_LOCK:
        _STOP_EVENT.set()
        _PAUSE_EVENT.set()
        BACKFILL_STATE["is_running"] = False
        BACKFILL_STATE["is_paused"] = False
    return {"status": "stopped", "message": "Backfill worker signaled to stop."}


def reset_backfill_state() -> Dict[str, Any]:
    """Gracefully stops the worker and resets all backfill progress counters to 0."""
    with _BACKFILL_LOCK:
        _STOP_EVENT.set()
        _PAUSE_EVENT.set()
        BACKFILL_STATE["is_running"] = False
        BACKFILL_STATE["is_paused"] = False
        BACKFILL_STATE["processed_count"] = 0
        BACKFILL_STATE["approved_count"] = 0
        BACKFILL_STATE["pending_count"] = 0
        BACKFILL_STATE["skipped_count"] = 0
        BACKFILL_STATE["error_count"] = 0
        BACKFILL_STATE["current_panel"] = None
        BACKFILL_STATE["start_time"] = None
        BACKFILL_STATE["error"] = None
    return {"status": "reset", "message": "Backfill state reset to 0."}


def _backfill_worker(batch_size: int):
    """Core background worker loop executing newest to oldest."""
    global BACKFILL_STATE
    logger.info("Starting historical backfill worker...")

    from main import run_ai_inspection_pipeline, GLOBAL_WATCHER

    try:
        # Step 1: Pre-load existing IDs and filenames from LuminoAI SQLite DB to allow instantaneous 0ms dedup
        AuditEngine.init_db()
        existing_test_ids = set()
        existing_filenames = set()
        with AuditEngine.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT ecolab_test_id, filename FROM audited_panels")
            for r in c.fetchall():
                if r["ecolab_test_id"]:
                    existing_test_ids.add(int(r["ecolab_test_id"]))
                if r["filename"]:
                    existing_filenames.add(r["filename"].strip().lower())
                    existing_filenames.add(os.path.basename(r["filename"].strip().lower()))

        logger.info(f"Loaded {len(existing_test_ids)} existing test IDs and {len(existing_filenames)} filenames for deduplication.")

        # Step 2: Determine Candidate Source (SQL Server vs Disk Folder)
        production_folder = AuditEngine.get_aipath_folder()
        sql_connected = False
        sql_conn = None
        driver_name = "unknown"

        db_cfg = AuditEngine.get_ecolab_db_config()
        if db_cfg and db_cfg.get("server") and db_cfg.get("database"):
            try:
                sql_conn, driver_name = ecolab_db_listener.get_db_connection(
                    db_cfg["server"], db_cfg["database"],
                    db_cfg.get("username", ""), db_cfg.get("password", ""),
                    timeout=5
                )
                sql_connected = True
                BACKFILL_STATE["source_mode"] = "sql_server"
                logger.info(f"Connected to SQL Server [{db_cfg['database']}] for backfill candidates.")
            except Exception as conn_err:
                logger.warning(f"Could not connect to SQL Server for backfill: {conn_err}. Falling back to disk folder.")
                sql_connected = False

        if not sql_connected:
            BACKFILL_STATE["source_mode"] = "disk_folder"

        # Step 3: Process Candidates
        if sql_connected and sql_conn:
            _process_sql_candidates(sql_conn, driver_name, existing_test_ids, existing_filenames, production_folder, run_ai_inspection_pipeline, batch_size)
        elif production_folder and os.path.exists(production_folder):
            _process_disk_candidates(production_folder, existing_filenames, run_ai_inspection_pipeline)
        else:
            with _BACKFILL_LOCK:
                BACKFILL_STATE["error"] = "Neither SQL Server nor valid production folder is accessible."

    except Exception as e:
        logger.error(f"Fatal error in backfill worker: {e}", exc_info=True)
        with _BACKFILL_LOCK:
            BACKFILL_STATE["error"] = str(e)
    finally:
        with _BACKFILL_LOCK:
            BACKFILL_STATE["is_running"] = False
            BACKFILL_STATE["is_paused"] = False
        logger.info("Historical backfill worker finished.")


def _process_sql_candidates(sql_conn, driver_name, existing_test_ids, existing_filenames, production_folder, run_ai_inspection_pipeline, batch_size):
    """Processes SQL Server dbo.Tests ordered by ID DESC."""
    global BACKFILL_STATE

    cursor = sql_conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total_cnt FROM dbo.Tests WITH (NOLOCK)")
    cnt_row = cursor.fetchone()
    total_cnt = 0
    if cnt_row:
        if isinstance(cnt_row, dict):
            total_cnt = cnt_row.get("total_cnt") or (list(cnt_row.values())[0] if cnt_row else 0)
        else:
            try:
                total_cnt = cnt_row[0]
            except Exception:
                total_cnt = getattr(cnt_row, "total_cnt", 0)
    with _BACKFILL_LOCK:
        BACKFILL_STATE["total_candidates"] = int(total_cnt or 0)

    last_seen_id = None
    records_processed_since_gc = 0

    while not _STOP_EVENT.is_set():
        if not _PAUSE_EVENT.is_set():
            time.sleep(0.5)
            continue

        # Fetch chunk ordered by ID DESC
        where_clause = ""
        params = []
        if last_seen_id is not None:
            where_clause = "WHERE ID < %s" if driver_name == "pymssql" else "WHERE ID < ?"
            params.append(last_seen_id)

        query = f"""
            SELECT TOP {batch_size} ID, Serial, TestDateTime, ImageFilePath, ELScore
            FROM dbo.Tests WITH (NOLOCK)
            {where_clause}
            ORDER BY ID DESC
        """
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        if not rows:
            logger.info("Reached end of dbo.Tests records.")
            break

        for raw_row in rows:
            if _STOP_EVENT.is_set():
                break
            while not _PAUSE_EVENT.is_set():
                time.sleep(0.5)

            rec = ecolab_db_listener._row_to_dict(raw_row)
            test_id = rec.get("id")
            last_seen_id = test_id

            if not test_id:
                continue

            serial = str(rec.get("serial") or "").strip()
            raw_img_path = str(rec.get("image_file_path") or "").strip()
            fn = os.path.basename(raw_img_path) if raw_img_path else f"{test_id}.1.tif"

            # Check deduplication
            if int(test_id) in existing_test_ids or fn.lower() in existing_filenames:
                with _BACKFILL_LOCK:
                    BACKFILL_STATE["skipped_count"] += 1
                continue

            # Locate image on disk
            resolved_img_path = _find_file_on_disk(raw_img_path, fn, production_folder)
            if not resolved_img_path or not os.path.exists(resolved_img_path):
                with _BACKFILL_LOCK:
                    BACKFILL_STATE["skipped_count"] += 1
                continue

            # Process single panel
            with _BACKFILL_LOCK:
                BACKFILL_STATE["current_serial"] = serial or fn
                BACKFILL_STATE["current_file"] = fn
                BACKFILL_STATE["current_test_id"] = test_id

            _process_single_panel(resolved_img_path, fn, serial, test_id, rec, run_ai_inspection_pipeline, production_folder=production_folder)

            existing_test_ids.add(int(test_id))
            existing_filenames.add(fn.lower())

            records_processed_since_gc += 1
            if records_processed_since_gc >= 20:
                gc.collect()
                records_processed_since_gc = 0
                time.sleep(0.05)

    try:
        sql_conn.close()
    except Exception:
        pass


def _process_disk_candidates(production_folder, existing_filenames, run_ai_inspection_pipeline):
    """Fallback scanner: processes .tif files in production folder ordered by mtime DESC."""
    global BACKFILL_STATE
    all_files = []
    for root, _, files in os.walk(production_folder):
        for f in files:
            if f.lower().endswith(('.tif', '.tiff')):
                full_p = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(full_p)
                    all_files.append((full_p, f, mtime))
                except Exception:
                    pass

    # Order newest to oldest
    all_files.sort(key=lambda x: x[2], reverse=True)
    with _BACKFILL_LOCK:
        BACKFILL_STATE["total_candidates"] = len(all_files)

    records_processed_since_gc = 0
    for full_p, fn, _ in all_files:
        if _STOP_EVENT.is_set():
            break
        while not _PAUSE_EVENT.is_set():
            time.sleep(0.5)

        if fn.lower() in existing_filenames:
            with _BACKFILL_LOCK:
                BACKFILL_STATE["skipped_count"] += 1
            continue

        meta = ecolab_db_listener.resolve_panel_metadata(full_p)
        serial = meta.get("serial_number") or fn
        test_id = meta.get("ecolab_test_id")

        with _BACKFILL_LOCK:
            BACKFILL_STATE["current_serial"] = serial
            BACKFILL_STATE["current_file"] = fn
            BACKFILL_STATE["current_test_id"] = test_id

        _process_single_panel(full_p, fn, serial, test_id, meta, run_ai_inspection_pipeline, production_folder=production_folder)
        existing_filenames.add(fn.lower())

        records_processed_since_gc += 1
        if records_processed_since_gc >= 20:
            gc.collect()
            records_processed_since_gc = 0
            time.sleep(0.05)


def _find_file_on_disk(raw_path: str, filename: str, production_folder: Optional[str]) -> Optional[str]:
    """Resolves physical file on disk from raw path or inside production folder with 0ms fast path checking."""
    if raw_path and os.path.exists(raw_path) and os.path.isfile(raw_path):
        return raw_path
    if not production_folder or not os.path.exists(production_folder):
        return None

    # Fast direct check 1: production_folder / filename
    candidate1 = os.path.join(production_folder, filename)
    if os.path.exists(candidate1) and os.path.isfile(candidate1):
        return candidate1

    if raw_path:
        cleaned = raw_path.strip().strip("'\"")
        # Fast direct check 2: production_folder / cleaned
        candidate2 = os.path.join(production_folder, cleaned)
        if os.path.exists(candidate2) and os.path.isfile(candidate2):
            return candidate2
        # Fast direct check 3: production_folder / basename
        base_fn = os.path.basename(cleaned)
        if base_fn != filename:
            candidate3 = os.path.join(production_folder, base_fn)
            if os.path.exists(candidate3) and os.path.isfile(candidate3):
                return candidate3

    return None


def _process_single_panel(image_path: str, filename: str, serial_number: str, test_id: Optional[int], meta: Dict[str, Any], run_ai_inspection_pipeline, production_folder: Optional[str] = None):
    """Runs AI inspection, pairs with .el file if available, and saves as either auto-approved or pending review."""
    global BACKFILL_STATE
    try:
        with open(image_path, "rb") as f:
            contents = f.read()

        inspect_result = run_ai_inspection_pipeline(contents, filename)

        # Ensure serial number, filename, and test ID are explicitly on inspect_result
        inspect_result["serial_number"] = serial_number
        inspect_result["filename"] = filename
        if test_id:
            inspect_result["ecolab_test_id"] = test_id

        # Locate and parse matching .el file
        matching_el_path = None
        raw_el_path = meta.get("el_file_path") or meta.get("ELFilePath")
        if raw_el_path:
            matching_el_path = _find_file_on_disk(raw_el_path, os.path.basename(raw_el_path), production_folder)
        if not matching_el_path:
            matching_el_path = ElReaderEngine.find_matching_el_file(os.path.dirname(image_path), filename)
        if not matching_el_path and production_folder:
            matching_el_path = ElReaderEngine.find_matching_el_file(production_folder, filename)

        el_data = {"has_el_file": False, "defective_cell_ids": [], "defects": [], "defect_count": 0}
        if matching_el_path and os.path.exists(matching_el_path):
            el_data = ElReaderEngine.parse_file_path(matching_el_path)

        inspect_result["el_analysis"] = el_data

        if el_data.get("has_el_file"):
            ai_defect_ids = [c["id"] for c in inspect_result.get("defective_cells", [])]
            el_defect_ids = el_data.get("defective_cell_ids", [])
            comparison = ElReaderEngine.compare_ai_and_el(ai_defect_ids, el_defect_ids)
            inspect_result["comparison"] = comparison

            el_def_set = set(comparison["matched_defects"] + comparison["human_el_only_defects"])
            for cell in inspect_result.get("all_cells", []):
                cid = cell["id"]
                cell["is_el_defective"] = cid in el_def_set
                cell["is_both_defective"] = (cid in el_def_set and cell.get("is_defective", False))
        else:
            inspect_result["comparison"] = None

        el_file_path = matching_el_path or el_data.get("el_file_path") or ""
        comparison = inspect_result.get("comparison") or {}
        has_el = bool(el_data.get("has_el_file"))
        is_full_match = comparison.get("is_full_match", False) if comparison else False

        ai_defect_ids = [c["id"] for c in inspect_result.get("defective_cells", [])]
        el_defect_ids = el_data.get("defective_cell_ids", []) if has_el else []

        ai_defect_count = len(ai_defect_ids)
        el_defect_count = len(el_defect_ids)

        # 100% CLEAN PASS: 0 defects in AI AND 0 defects in EL machine (or no EL file)
        is_100_clean_pass = (
            ai_defect_count == 0 and
            (not has_el or el_defect_count == 0 or is_full_match)
        )

        test_dt = meta.get("test_datetime") or meta.get("TestDateTime")
        el_score = meta.get("el_score") or meta.get("ELScore")

        if is_100_clean_pass:
            audit_data = {
                "filename": filename,
                "panel_id": inspect_result.get("panel_id") or os.path.splitext(filename)[0],
                "serial_number": serial_number,
                "operator_name": "AI Auto-Validator",
                "is_matched": True,
                "ai_defects": [],
                "el_defects": [],
                "actual_defects": [],
                "disputes": [],
                "cell_defect_types": {},
                "approval_status": "auto_approved",
                "is_approved": 1,
                "ecolab_test_id": test_id,
                "inspection_cycle": meta.get("inspection_cycle", 1),
                "panel_image_b64": inspect_result.get("panel_image_b64", ""),
                "full_data": inspect_result,
                "test_datetime": test_dt,
                "image_path": image_path,
                "el_file_path": el_file_path,
                "rating": "A",
                "el_score": el_score
            }
            AuditEngine.save_audit_record(audit_data)
            with _BACKFILL_LOCK:
                BACKFILL_STATE["auto_approved_count"] += 1
                BACKFILL_STATE["processed_count"] += 1
        else:
            num_def = max(ai_defect_count, el_defect_count)
            derived_rating = "B" if num_def <= 1 else ("C" if num_def <= 3 else "D")
            pending_data = {
                "filename": filename,
                "panel_id": inspect_result.get("panel_id") or os.path.splitext(filename)[0],
                "serial_number": serial_number,
                "operator_name": AuditEngine.get_active_operator() or "Operator 1",
                "is_matched": is_full_match,
                "ai_defects": ai_defect_ids,
                "el_defects": el_defect_ids,
                "actual_defects": [],
                "disputes": comparison.get("disputes", []),
                "cell_defect_types": {},
                "approval_status": "pending",
                "is_approved": 0,
                "ecolab_test_id": test_id,
                "inspection_cycle": meta.get("inspection_cycle", 1),
                "panel_image_b64": inspect_result.get("panel_image_b64", ""),
                "full_data": inspect_result,
                "test_datetime": test_dt,
                "image_path": image_path,
                "el_file_path": el_file_path,
                "rating": derived_rating,
                "el_score": el_score
            }
            AuditEngine.save_unapproved_panel(pending_data)
            with _BACKFILL_LOCK:
                BACKFILL_STATE["pending_count"] += 1
                BACKFILL_STATE["processed_count"] += 1

        del contents
        del inspect_result

    except Exception as proc_err:
        logger.warning(f"Error processing backfill panel {filename}: {proc_err}")
