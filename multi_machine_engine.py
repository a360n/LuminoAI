#!/usr/bin/env python3
"""
LuminoAI Multi-Machine Integration Engine
------------------------------------------
Connects the Central Pre-Lam Server to 3 Post-Lamination machines:
1. EcoSUN: Solar Simulator & Flasher (remote SQL Server polling)
2. EcoHIPOT: Electrical Safety & Insulation Tester (remote SQL Server polling)
3. Post-Lam EcoLAB: Post-Lamination EL Inspection Station (zero-disk streaming over SMB + AI inference)

STRICT POLICIES:
- ZERO DUPLICATE DISK STORAGE: Post-Lam images are read directly into RAM buffers from SMB/UNC.
- 100% ENGLISH ONLY in all logs, comments, status messages, and diagnostics.
- ZERO AGENTS / ZERO INSTALL on remote machine PCs.
"""

import os
import re
import time
import glob
import json
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable

from audit_engine import AuditEngine
from el_reader_engine import ElReaderEngine

logger = logging.getLogger("MultiMachineEngine")
logger.setLevel(logging.INFO)

# Callback placeholder for AI inspection pipeline (injected from main.py)
_AI_INSPECTION_PIPELINE: Optional[Callable[[bytes, str], Dict[str, Any]]] = None

def set_ai_inspection_pipeline(pipeline_func: Callable[[bytes, str], Dict[str, Any]]) -> None:
    """Injects the AI inspection pipeline function from main.py."""
    global _AI_INSPECTION_PIPELINE
    _AI_INSPECTION_PIPELINE = pipeline_func


def get_sql_connection(server: str, database: str, username: str, password: str, timeout: int = 5):
    """
    Attempts connection to SQL Server using pymssql first, then pyodbc.
    """
    server = server.strip()
    database = database.strip()
    username = username.strip()
    password = password.strip()

    mssql_server = server
    if server.startswith(".\\") or server.startswith("./"):
        mssql_server = "localhost\\" + server[2:]

    # 1. Try pymssql
    try:
        import pymssql
        conn = pymssql.connect(
            server=mssql_server,
            user=username,
            password=password,
            database=database,
            login_timeout=timeout,
            timeout=timeout,
            as_dict=True
        )
        return conn, "pymssql"
    except ImportError:
        pass
    except Exception as e:
        last_pymssql_err = str(e)

    # 2. Try pyodbc
    try:
        import pyodbc
        installed_drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
        driver_name = "ODBC Driver 17 for SQL Server"
        if driver_name not in installed_drivers and installed_drivers:
            driver_name = installed_drivers[0]

        conn_str = (
            f"DRIVER={{{driver_name}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            "TrustServerCertificate=yes;"
            f"Connection Timeout={timeout};"
        )
        conn = pyodbc.connect(conn_str, timeout=timeout)
        return conn, "pyodbc"
    except ImportError:
        pass
    except Exception as e:
        raise ConnectionError(f"SQL Server connection failed: {e}")

    raise ModuleNotFoundError(
        "Neither 'pymssql' nor 'pyodbc' is installed in your Python environment."
    )


def _row_to_dict(row) -> Dict[str, Any]:
    """Normalizes a DB row from pymssql (dict) or pyodbc (Row) into a lowercase key dict."""
    if isinstance(row, dict):
        return {k.lower(): v for k, v in row.items()}
    if hasattr(row, "cursor_description"):
        cols = [col[0].lower() for col in row.cursor_description]
        return dict(zip(cols, list(row)))
    return {}


# =====================================================================
# 1. EcoSUN Solar Simulator / Flasher Listener
# =====================================================================

ECOSUN_STATE: Dict[str, Any] = {
    "is_running": False,
    "is_connected": False,
    "last_seen_id": 0,
    "last_poll_time": None,
    "last_error": None,
    "total_synced": 0,
    "latest_record": None,
    "recent_events": []
}

_ECOSUN_THREAD: Optional[threading.Thread] = None
_ECOSUN_STOP_EVENT = threading.Event()
_ECOSUN_LOCK = threading.Lock()


def test_ecosun_connection(server: str, database: str, username: str, password: str, table_name: str = "dbo.TestData") -> Dict[str, Any]:
    """Tests connectivity to EcoSUN remote SQL Server and inspects latest flasher tests."""
    t0 = time.time()
    try:
        conn, driver = get_sql_connection(server, database, username, password, timeout=5)
        cursor = conn.cursor()

        # Find best table candidate
        candidates = [table_name, "dbo.TestData", "dbo.Tests", "dbo.ModuleTest", "dbo.IVData", "dbo.Results"]
        valid_table = None
        for tbl in candidates:
            try:
                cursor.execute(f"SELECT TOP 1 * FROM {tbl} WITH (NOLOCK)")
                valid_table = tbl
                break
            except Exception:
                continue

        if not valid_table:
            conn.close()
            return {
                "success": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": "No recognizable flasher test table found. Please check table name.",
                "message": f"Connected to {database} but failed to locate flasher tables."
            }

        # Check total tests
        cursor.execute(f"SELECT COUNT(*) as total_cnt, MAX(ID) as max_id FROM {valid_table} WITH (NOLOCK)")
        row = cursor.fetchone()
        row_dict = _row_to_dict(row)
        total_cnt = row_dict.get("total_cnt") or 0
        max_id = row_dict.get("max_id") or 0

        # Fetch latest sample
        latest_sample = None
        if max_id:
            cursor.execute(f"SELECT TOP 1 * FROM {valid_table} WITH (NOLOCK) ORDER BY ID DESC")
            rec = cursor.fetchone()
            if rec:
                latest_sample = _row_to_dict(rec)

        conn.close()
        elapsed = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "latency_ms": elapsed,
            "driver": driver,
            "database": database,
            "table_name": valid_table,
            "total_tests": total_cnt,
            "max_id": max_id,
            "latest_record": latest_sample,
            "message": f"Successfully connected to EcoSUN database [{database}] via ({driver})! Total records: {total_cnt:,}"
        }
    except Exception as e:
        elapsed = round((time.time() - t0) * 1000, 1)
        return {
            "success": False,
            "latency_ms": elapsed,
            "error": str(e),
            "message": f"EcoSUN connection failed: {str(e)}"
        }


def _ecosun_listener_loop(cfg: Dict[str, Any]):
    server = cfg.get("server", "").strip()
    database = cfg.get("database", "").strip()
    username = cfg.get("username", "").strip()
    password = cfg.get("password", "").strip()
    table_name = cfg.get("table_name", "dbo.TestData").strip() or "dbo.TestData"
    poll_interval = float(cfg.get("poll_interval", 2.0))

    logger.info(f"EcoSUN listener started on {server}/{database} [{table_name}]")

    last_id = 0
    # Initialize last_id from local database to avoid duplicate historical imports
    try:
        latest = AuditEngine.get_latest_ecosun_test()
        if latest and latest.get("raw_data_json"):
            raw = json.loads(latest["raw_data_json"])
            last_id = int(raw.get("id") or 0)
    except Exception:
        last_id = 0

    with _ECOSUN_LOCK:
        ECOSUN_STATE["is_running"] = True
        ECOSUN_STATE["last_seen_id"] = last_id

    while not _ECOSUN_STOP_EVENT.is_set():
        try:
            conn, driver = get_sql_connection(server, database, username, password, timeout=5)
            with _ECOSUN_LOCK:
                ECOSUN_STATE["is_connected"] = True
                ECOSUN_STATE["last_error"] = None

            cursor = conn.cursor()

            # Query new records
            if driver == "pymssql":
                q = f"SELECT TOP 50 * FROM {table_name} WITH (NOLOCK) WHERE ID > %s ORDER BY ID ASC"
                cursor.execute(q, (last_id,))
            else:
                q = f"SELECT TOP 50 * FROM {table_name} WITH (NOLOCK) WHERE ID > ? ORDER BY ID ASC"
                cursor.execute(q, (last_id,))

            rows = cursor.fetchall()
            conn.close()

            for r in rows:
                if _ECOSUN_STOP_EVENT.is_set():
                    break
                d = _row_to_dict(r)
                curr_id = int(d.get("id") or 0)
                if curr_id > last_id:
                    last_id = curr_id

                # Resolve standard solar parameters
                serial = d.get("serial") or d.get("serialnumber") or d.get("barcode") or d.get("panel_id") or f"SN-{curr_id}"
                test_dt = d.get("testdatetime") or d.get("datetime") or d.get("date_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if isinstance(test_dt, datetime):
                    test_dt = test_dt.strftime("%Y-%m-%d %H:%M:%S")

                pmax = float(d.get("pmax") or d.get("pm") or d.get("p_max") or 0.0)
                voc = float(d.get("voc") or d.get("v_oc") or 0.0)
                isc = float(d.get("isc") or d.get("i_sc") or 0.0)
                ff = float(d.get("ff") or d.get("fill_factor") or 0.0)
                eff = float(d.get("efficiency") or d.get("eff") or d.get("eta") or 0.0)
                rs = float(d.get("rs") or d.get("r_s") or 0.0)
                rsh = float(d.get("rsh") or d.get("r_sh") or 0.0)
                grade = str(d.get("grade") or d.get("class") or ("A" if pmax > 500 else "B")).strip().upper()
                temp = float(d.get("temp") or d.get("temperature") or 25.0)
                irradiance = float(d.get("irradiance") or d.get("irr") or 1000.0)
                op_name = str(d.get("operator") or d.get("operator_name") or "EcoSUN Operator")

                record_payload = {
                    "serial_number": str(serial).strip(),
                    "test_datetime": str(test_dt),
                    "pmax": pmax,
                    "voc": voc,
                    "isc": isc,
                    "ff": ff,
                    "efficiency": eff,
                    "rs": rs,
                    "rsh": rsh,
                    "grade": grade,
                    "temp": temp,
                    "irradiance": irradiance,
                    "operator_name": op_name,
                    "raw_data_json": json.dumps(d, default=str)
                }

                # Save into local database
                AuditEngine.save_ecosun_test(record_payload)

                with _ECOSUN_LOCK:
                    ECOSUN_STATE["last_seen_id"] = last_id
                    ECOSUN_STATE["total_synced"] += 1
                    ECOSUN_STATE["latest_record"] = record_payload
                    ECOSUN_STATE["recent_events"].insert(0, record_payload)
                    if len(ECOSUN_STATE["recent_events"]) > 50:
                        ECOSUN_STATE["recent_events"].pop()

            with _ECOSUN_LOCK:
                ECOSUN_STATE["last_poll_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            with _ECOSUN_LOCK:
                ECOSUN_STATE["is_connected"] = False
                ECOSUN_STATE["last_error"] = str(e)
            logger.debug(f"EcoSUN listener poll notice: {e}")

        _ECOSUN_STOP_EVENT.wait(poll_interval)

    with _ECOSUN_LOCK:
        ECOSUN_STATE["is_running"] = False
        ECOSUN_STATE["is_connected"] = False
    logger.info("EcoSUN listener loop stopped.")


def start_ecosun_listener(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _ECOSUN_THREAD, _ECOSUN_STOP_EVENT
    if cfg is None:
        configs = AuditEngine.get_machine_configs()
        cfg = configs.get("ecosun_config", {})

    if not cfg.get("enabled", True):
        return {"status": "disabled", "message": "EcoSUN listener is disabled in settings."}

    if _ECOSUN_THREAD and _ECOSUN_THREAD.is_alive():
        return {"status": "already_running", "message": "EcoSUN listener is already active."}

    _ECOSUN_STOP_EVENT.clear()
    _ECOSUN_THREAD = threading.Thread(target=_ecosun_listener_loop, args=(cfg,), daemon=True, name="EcoSUNListener")
    _ECOSUN_THREAD.start()
    return {"status": "started", "message": "EcoSUN listener successfully started."}


def stop_ecosun_listener() -> Dict[str, Any]:
    global _ECOSUN_THREAD, _ECOSUN_STOP_EVENT
    _ECOSUN_STOP_EVENT.set()
    if _ECOSUN_THREAD and _ECOSUN_THREAD.is_alive():
        _ECOSUN_THREAD.join(timeout=3)
    return {"status": "stopped", "message": "EcoSUN listener stopped."}


# =====================================================================
# 2. EcoHIPOT Electrical Safety & Insulation Tester Listener
# =====================================================================

ECOHIPOT_STATE: Dict[str, Any] = {
    "is_running": False,
    "is_connected": False,
    "last_seen_id": 0,
    "last_poll_time": None,
    "last_error": None,
    "total_synced": 0,
    "latest_record": None,
    "recent_events": []
}

_ECOHIPOT_THREAD: Optional[threading.Thread] = None
_ECOHIPOT_STOP_EVENT = threading.Event()
_ECOHIPOT_LOCK = threading.Lock()


def test_ecohipot_connection(server: str, database: str, username: str, password: str, table_name: str = "dbo.HipotTests") -> Dict[str, Any]:
    """Tests connectivity to EcoHIPOT remote SQL Server and inspects latest safety tests."""
    t0 = time.time()
    try:
        conn, driver = get_sql_connection(server, database, username, password, timeout=5)
        cursor = conn.cursor()

        candidates = [table_name, "dbo.HipotTests", "dbo.Tests", "dbo.SafetyTests", "dbo.HipotResults", "dbo.Results"]
        valid_table = None
        for tbl in candidates:
            try:
                cursor.execute(f"SELECT TOP 1 * FROM {tbl} WITH (NOLOCK)")
                valid_table = tbl
                break
            except Exception:
                continue

        if not valid_table:
            conn.close()
            return {
                "success": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": "No recognizable hipot safety test table found. Please check table name.",
                "message": f"Connected to {database} but failed to locate safety test tables."
            }

        cursor.execute(f"SELECT COUNT(*) as total_cnt, MAX(ID) as max_id FROM {valid_table} WITH (NOLOCK)")
        row = cursor.fetchone()
        row_dict = _row_to_dict(row)
        total_cnt = row_dict.get("total_cnt") or 0
        max_id = row_dict.get("max_id") or 0

        latest_sample = None
        if max_id:
            cursor.execute(f"SELECT TOP 1 * FROM {valid_table} WITH (NOLOCK) ORDER BY ID DESC")
            rec = cursor.fetchone()
            if rec:
                latest_sample = _row_to_dict(rec)

        conn.close()
        elapsed = round((time.time() - t0) * 1000, 1)
        return {
            "success": True,
            "latency_ms": elapsed,
            "driver": driver,
            "database": database,
            "table_name": valid_table,
            "total_tests": total_cnt,
            "max_id": max_id,
            "latest_record": latest_sample,
            "message": f"Successfully connected to EcoHIPOT database [{database}] via ({driver})! Total records: {total_cnt:,}"
        }
    except Exception as e:
        elapsed = round((time.time() - t0) * 1000, 1)
        return {
            "success": False,
            "latency_ms": elapsed,
            "error": str(e),
            "message": f"EcoHIPOT connection failed: {str(e)}"
        }


def _ecohipot_listener_loop(cfg: Dict[str, Any]):
    server = cfg.get("server", "").strip()
    database = cfg.get("database", "").strip()
    username = cfg.get("username", "").strip()
    password = cfg.get("password", "").strip()
    table_name = cfg.get("table_name", "dbo.HipotTests").strip() or "dbo.HipotTests"
    poll_interval = float(cfg.get("poll_interval", 2.0))

    logger.info(f"EcoHIPOT listener started on {server}/{database} [{table_name}]")

    last_id = 0
    try:
        latest = AuditEngine.get_latest_ecohipot_test()
        if latest and latest.get("raw_data_json"):
            raw = json.loads(latest["raw_data_json"])
            last_id = int(raw.get("id") or 0)
    except Exception:
        last_id = 0

    with _ECOHIPOT_LOCK:
        ECOHIPOT_STATE["is_running"] = True
        ECOHIPOT_STATE["last_seen_id"] = last_id

    while not _ECOHIPOT_STOP_EVENT.is_set():
        try:
            conn, driver = get_sql_connection(server, database, username, password, timeout=5)
            with _ECOHIPOT_LOCK:
                ECOHIPOT_STATE["is_connected"] = True
                ECOHIPOT_STATE["last_error"] = None

            cursor = conn.cursor()

            if driver == "pymssql":
                q = f"SELECT TOP 50 * FROM {table_name} WITH (NOLOCK) WHERE ID > %s ORDER BY ID ASC"
                cursor.execute(q, (last_id,))
            else:
                q = f"SELECT TOP 50 * FROM {table_name} WITH (NOLOCK) WHERE ID > ? ORDER BY ID ASC"
                cursor.execute(q, (last_id,))

            rows = cursor.fetchall()
            conn.close()

            for r in rows:
                if _ECOHIPOT_STOP_EVENT.is_set():
                    break
                d = _row_to_dict(r)
                curr_id = int(d.get("id") or 0)
                if curr_id > last_id:
                    last_id = curr_id

                serial = d.get("serial") or d.get("serialnumber") or d.get("barcode") or d.get("panel_id") or f"SN-{curr_id}"
                test_dt = d.get("testdatetime") or d.get("datetime") or d.get("date_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if isinstance(test_dt, datetime):
                    test_dt = test_dt.strftime("%Y-%m-%d %H:%M:%S")

                riso = float(d.get("insulationresistance") or d.get("riso") or d.get("r_iso") or 500.0)
                leakage = float(d.get("leakagecurrent") or d.get("leakage") or d.get("current_ma") or 0.12)
                breakdown = float(d.get("breakdownvoltage") or d.get("voltage") or d.get("breakdown_v") or 1500.0)
                ground = float(d.get("groundcontinuity") or d.get("ground_ohm") or d.get("gnd") or 0.05)
                res_str = str(d.get("testresult") or d.get("result") or d.get("status") or "PASS").strip().upper()
                test_result = "PASS" if "PASS" in res_str or "OK" in res_str else "FAIL"
                fail_reason = str(d.get("failreason") or d.get("error_msg") or "")
                op_name = str(d.get("operator") or d.get("operator_name") or "EcoHIPOT Operator")

                record_payload = {
                    "serial_number": str(serial).strip(),
                    "test_datetime": str(test_dt),
                    "insulation_resistance_mohm": riso,
                    "leakage_current_ma": leakage,
                    "breakdown_voltage_v": breakdown,
                    "ground_continuity_ohm": ground,
                    "test_result": test_result,
                    "fail_reason": fail_reason,
                    "operator_name": op_name,
                    "raw_data_json": json.dumps(d, default=str)
                }

                # Save into local database
                AuditEngine.save_ecohipot_test(record_payload)

                with _ECOHIPOT_LOCK:
                    ECOHIPOT_STATE["last_seen_id"] = last_id
                    ECOHIPOT_STATE["total_synced"] += 1
                    ECOHIPOT_STATE["latest_record"] = record_payload
                    ECOHIPOT_STATE["recent_events"].insert(0, record_payload)
                    if len(ECOHIPOT_STATE["recent_events"]) > 50:
                        ECOHIPOT_STATE["recent_events"].pop()

            with _ECOHIPOT_LOCK:
                ECOHIPOT_STATE["last_poll_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            with _ECOHIPOT_LOCK:
                ECOHIPOT_STATE["is_connected"] = False
                ECOHIPOT_STATE["last_error"] = str(e)
            logger.debug(f"EcoHIPOT listener poll notice: {e}")

        _ECOHIPOT_STOP_EVENT.wait(poll_interval)

    with _ECOHIPOT_LOCK:
        ECOHIPOT_STATE["is_running"] = False
        ECOHIPOT_STATE["is_connected"] = False
    logger.info("EcoHIPOT listener loop stopped.")


def start_ecohipot_listener(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _ECOHIPOT_THREAD, _ECOHIPOT_STOP_EVENT
    if cfg is None:
        configs = AuditEngine.get_machine_configs()
        cfg = configs.get("ecohipot_config", {})

    if not cfg.get("enabled", True):
        return {"status": "disabled", "message": "EcoHIPOT listener is disabled in settings."}

    if _ECOHIPOT_THREAD and _ECOHIPOT_THREAD.is_alive():
        return {"status": "already_running", "message": "EcoHIPOT listener is already active."}

    _ECOHIPOT_STOP_EVENT.clear()
    _ECOHIPOT_THREAD = threading.Thread(target=_ecohipot_listener_loop, args=(cfg,), daemon=True, name="EcoHIPOTListener")
    _ECOHIPOT_THREAD.start()
    return {"status": "started", "message": "EcoHIPOT listener successfully started."}


def stop_ecohipot_listener() -> Dict[str, Any]:
    global _ECOHIPOT_THREAD, _ECOHIPOT_STOP_EVENT
    _ECOHIPOT_STOP_EVENT.set()
    if _ECOHIPOT_THREAD and _ECOHIPOT_THREAD.is_alive():
        _ECOHIPOT_THREAD.join(timeout=3)
    return {"status": "stopped", "message": "EcoHIPOT listener stopped."}


# =====================================================================
# 3. Post-Lam EcoLAB Zero-Disk Stream Poller & Differential Analyzer
# =====================================================================

POST_LAM_STATE: Dict[str, Any] = {
    "is_running": False,
    "is_connected": False,
    "last_poll_time": None,
    "last_processed_file": None,
    "last_error": None,
    "total_inspected": 0,
    "recent_panels": [],
    "processed_files_cache": set()
}

_POST_LAM_THREAD: Optional[threading.Thread] = None
_POST_LAM_STOP_EVENT = threading.Event()
_POST_LAM_LOCK = threading.Lock()


def test_post_lam_connection(server: str = "", database: str = "", username: str = "", password: str = "", shared_folder: str = "") -> Dict[str, Any]:
    """
    Tests Post-Lam EcoLAB setup:
    1. Tests SMB / UNC folder accessibility (MANDATORY for image streaming).
    2. Tests SQL Server connection if configured (optional metadata enhancement).
    """
    t0 = time.time()
    folder_ok = False
    folder_file_count = 0
    folder_msg = ""
    sql_ok = False
    sql_msg = ""
    driver_used = None

    shared_folder = (shared_folder or "").strip()
    if shared_folder:
        if os.path.isdir(shared_folder):
            folder_ok = True
            try:
                tif_files = glob.glob(os.path.join(shared_folder, "*.tif"))
                folder_file_count = len(tif_files)
                folder_msg = f"Shared folder accessible. Found {folder_file_count} .tif files."
            except Exception as fe:
                folder_msg = f"Folder accessible but directory listing error: {fe}"
        else:
            folder_msg = f"Shared folder directory not found or inaccessible: {shared_folder}"
    else:
        folder_msg = "No shared folder path provided."

    server = (server or "").strip()
    if server and database:
        try:
            conn, driver_used = get_sql_connection(server, database, username, password, timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total_cnt FROM dbo.Tests WITH (NOLOCK)")
            r = cursor.fetchone()
            cnt = _row_to_dict(r).get("total_cnt") or 0
            conn.close()
            sql_ok = True
            sql_msg = f"SQL connection successful ({driver_used}). Tests count: {cnt:,}"
        except Exception as se:
            sql_msg = f"SQL connection failed: {se}"

    elapsed = round((time.time() - t0) * 1000, 1)
    overall_success = folder_ok or sql_ok

    return {
        "success": overall_success,
        "latency_ms": elapsed,
        "folder_accessible": folder_ok,
        "folder_file_count": folder_file_count,
        "folder_message": folder_msg,
        "sql_connected": sql_ok,
        "sql_driver": driver_used,
        "sql_message": sql_msg,
        "message": f"Post-Lam Station Test: Folder [{folder_msg}] | SQL [{sql_msg}]"
    }


def _post_lam_listener_loop(cfg: Dict[str, Any]):
    server = cfg.get("server", "").strip()
    database = cfg.get("database", "").strip()
    username = cfg.get("username", "").strip()
    password = cfg.get("password", "").strip()
    shared_folder = cfg.get("shared_folder", "").strip()
    poll_interval = float(cfg.get("poll_interval", 2.0))

    logger.info(f"Post-Lam EcoLAB listener started. Folder: '{shared_folder}', SQL: '{server}/{database}'")

    # Seed processed files cache from database
    processed_files = set()
    try:
        with AuditEngine.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT filename FROM audited_panels WHERE inspection_stage = 'post_lam'")
            for r in cursor.fetchall():
                if r["filename"]:
                    processed_files.add(r["filename"])
    except Exception as e:
        logger.debug(f"Could not seed post-lam processed files: {e}")

    with _POST_LAM_LOCK:
        POST_LAM_STATE["is_running"] = True
        POST_LAM_STATE["processed_files_cache"] = processed_files

    while not _POST_LAM_STOP_EVENT.is_set():
        try:
            if not shared_folder or not os.path.isdir(shared_folder):
                with _POST_LAM_LOCK:
                    POST_LAM_STATE["is_connected"] = False
                    POST_LAM_STATE["last_error"] = f"Shared folder inaccessible: {shared_folder}"
                _POST_LAM_STOP_EVENT.wait(poll_interval)
                continue

            with _POST_LAM_LOCK:
                POST_LAM_STATE["is_connected"] = True
                POST_LAM_STATE["last_error"] = None

            # Find all .tif files in shared folder
            tif_files = glob.glob(os.path.join(shared_folder, "*.tif"))
            # Sort by modification time ascending (FIFO)
            tif_files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)

            for full_path in tif_files:
                if _POST_LAM_STOP_EVENT.is_set():
                    break
                fname = os.path.basename(full_path)
                if fname in processed_files:
                    continue

                # Wait slightly if file is still being written by EcoLAB machine
                try:
                    fsize_1 = os.path.getsize(full_path)
                    time.sleep(0.15)
                    fsize_2 = os.path.getsize(full_path)
                    if fsize_1 != fsize_2 or fsize_1 == 0:
                        continue  # Still writing, skip for next iteration
                except Exception:
                    continue

                # -------------------------------------------------------------
                # ZERO DUPLICATE DISK STORAGE: Read directly into memory buffer!
                # -------------------------------------------------------------
                try:
                    with open(full_path, "rb") as f:
                        img_bytes = f.read()
                except Exception as read_err:
                    logger.warning(f"Failed to read post-lam image {fname} from SMB: {read_err}")
                    continue

                logger.info(f"Processing Post-Lam image in RAM ({len(img_bytes) / 1024 / 1024:.2f} MB): {fname}")

                # 1. Run AI inference in memory
                if _AI_INSPECTION_PIPELINE is None:
                    logger.warning("AI inspection pipeline callback not registered in MultiMachineEngine!")
                    continue

                t_recv = time.time()
                inspect_result = _AI_INSPECTION_PIPELINE(img_bytes, fname, backend_received_time=t_recv)
                inspect_result["filename"] = fname
                inspect_result["full_path"] = full_path

                # 2. Check for companion .el file on the remote shared folder in memory
                el_filename = os.path.splitext(fname)[0] + ".el"
                remote_el_path = os.path.join(shared_folder, el_filename)
                el_data = {"has_el_file": False, "defective_cell_ids": [], "defects": [], "defect_count": 0}

                if os.path.isfile(remote_el_path):
                    try:
                        with open(remote_el_path, "r", encoding="utf-8", errors="ignore") as elf:
                            el_raw_text = elf.read()
                        el_mtime = os.path.getmtime(remote_el_path)
                        el_data = ElReaderEngine.parse_content(el_raw_text, el_filename, file_mtime=el_mtime)
                        el_data["has_el_file"] = True
                    except Exception as el_err:
                        logger.debug(f"Could not parse remote .el file {remote_el_path}: {el_err}")

                inspect_result["el_analysis"] = el_data

                # 3. Resolve Serial Number
                serial_number = el_data.get("serial_number") or ""
                if not serial_number or serial_number == "Unknown" or serial_number.startswith("ID-"):
                    # Check barcode match or SQL metadata
                    m = re.findall(r'\b(ANM[A-Z0-9]{8,15}|[A-Z]{2,4}\d{8,14})\b', fname)
                    if m:
                        serial_number = m[0]
                    else:
                        serial_number = os.path.splitext(fname)[0]

                inspect_result["serial_number"] = serial_number

                # 4. Compare AI vs EL Machine/Operator
                ai_defect_ids = [c["id"] for c in inspect_result.get("defective_cells", [])]
                el_defect_ids = el_data.get("defective_cell_ids", []) if el_data.get("has_el_file") else []
                has_el = bool(el_data.get("has_el_file"))
                comparison = ElReaderEngine.compare_ai_and_el(ai_defect_ids, el_defect_ids) if has_el else None
                inspect_result["comparison"] = comparison

                is_full_match = comparison.get("is_full_match", False) if comparison else (len(ai_defect_ids) == 0)

                # 5. Differential Analysis: Find Pre-Lam Record for this Serial Number!
                pre_lam_rec = None
                try:
                    with AuditEngine.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT id, filename, serial_number, actual_defects_json, cell_defect_types_json
                            FROM audited_panels
                            WHERE serial_number = ? AND (inspection_stage = 'pre_lam' OR inspection_stage IS NULL) AND is_approved = 1
                            ORDER BY id DESC LIMIT 1
                        """, (serial_number,))
                        pre_row = cursor.fetchone()
                        if pre_row:
                            pre_lam_rec = dict(pre_row)
                except Exception as p_err:
                    logger.debug(f"Pre-lam record resolution notice: {p_err}")

                pre_lam_defects_set = set()
                if pre_lam_rec:
                    try:
                        pre_lam_defects_set = set(json.loads(pre_lam_rec.get("actual_defects_json") or "[]"))
                    except Exception:
                        pass

                # Detect newly introduced defects (in Post-Lam but NOT in Pre-Lam)
                post_lam_defects_set = set(ai_defect_ids)
                newly_induced_defects = list(post_lam_defects_set - pre_lam_defects_set)
                lamination_defects_list = [
                    {"cell_id": cid, "defect_type": "Crack", "induced_by_lamination": True}
                    for cid in newly_induced_defects
                ]

                # 6. Auto-Approve if 100% Concordance, or Send to Pending Adjudication Queue
                test_dt_str = el_data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if is_full_match and len(ai_defect_ids) == 0:
                    # 100% Clean Pass -> Auto Approve
                    auto_data = {
                        "filename": fname,
                        "panel_id": os.path.splitext(fname)[0],
                        "serial_number": serial_number,
                        "operator_name": "Auto-Approval AI (Post-Lam)",
                        "is_matched": True,
                        "ai_defects": [],
                        "el_defects": [],
                        "actual_defects": [],
                        "disputes": [],
                        "cell_defect_types": {},
                        "approval_status": "auto_approved",
                        "is_approved": 1,
                        "inspection_stage": "post_lam",
                        "pre_lam_ref_id": pre_lam_rec["id"] if pre_lam_rec else None,
                        "lamination_defects": [],
                        "test_datetime": test_dt_str,
                        "image_path": full_path,
                        "panel_image_b64": inspect_result.get("panel_image_b64", ""),
                        "full_data": inspect_result
                    }
                    saved_rec = AuditEngine.save_audit_record(auto_data)
                    inspect_result["audit_status"] = saved_rec
                else:
                    # Defective or Discrepant -> Operator Adjudication Required at /post-lam
                    pending_disputes = []
                    if comparison:
                        for cid in comparison.get("human_el_only_defects", []):
                            pending_disputes.append({
                                "cell_id": cid,
                                "type": "EL_DEFECT_AI_HEALTHY",
                                "el_verdict": "defective",
                                "ai_verdict": "healthy"
                            })
                        for cid in comparison.get("ai_only_defects", []):
                            pending_disputes.append({
                                "cell_id": cid,
                                "type": "AI_DEFECT_EL_HEALTHY",
                                "el_verdict": "healthy",
                                "ai_verdict": "defective"
                            })
                    else:
                        for cid in ai_defect_ids:
                            pending_disputes.append({
                                "cell_id": cid,
                                "type": "AI_DEFECT_EL_HEALTHY",
                                "el_verdict": "uninspected",
                                "ai_verdict": "defective"
                            })

                    pending_data = {
                        "filename": fname,
                        "panel_id": os.path.splitext(fname)[0],
                        "serial_number": serial_number,
                        "operator_name": "Post-Lam Inspector",
                        "is_matched": is_full_match,
                        "ai_defects": ai_defect_ids,
                        "el_defects": el_defect_ids,
                        "actual_defects": comparison.get("matched_defects", []) if comparison else ai_defect_ids,
                        "disputes": pending_disputes,
                        "cell_defect_types": {cid: "Crack" for cid in ai_defect_ids},
                        "approval_status": "pending",
                        "is_approved": 0,
                        "inspection_stage": "post_lam",
                        "pre_lam_ref_id": pre_lam_rec["id"] if pre_lam_rec else None,
                        "lamination_defects": lamination_defects_list,
                        "test_datetime": test_dt_str,
                        "image_path": full_path,
                        "panel_image_b64": inspect_result.get("panel_image_b64", ""),
                        "full_data": inspect_result
                    }
                    saved_rec = AuditEngine.save_unapproved_panel(pending_data)
                    inspect_result["audit_status"] = saved_rec

                processed_files.add(fname)
                with _POST_LAM_LOCK:
                    POST_LAM_STATE["processed_files_cache"].add(fname)
                    POST_LAM_STATE["last_processed_file"] = fname
                    POST_LAM_STATE["total_inspected"] += 1
                    POST_LAM_STATE["recent_panels"].insert(0, {
                        "filename": fname,
                        "serial_number": serial_number,
                        "test_datetime": test_dt_str,
                        "is_matched": is_full_match,
                        "ai_defect_count": len(ai_defect_ids),
                        "lamination_defects_count": len(lamination_defects_list),
                        "pre_lam_ref_id": pre_lam_rec["id"] if pre_lam_rec else None,
                        "approval_status": "auto_approved" if is_full_match and len(ai_defect_ids) == 0 else "pending"
                    })
                    if len(POST_LAM_STATE["recent_panels"]) > 50:
                        POST_LAM_STATE["recent_panels"].pop()

            with _POST_LAM_LOCK:
                POST_LAM_STATE["last_poll_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            with _POST_LAM_LOCK:
                POST_LAM_STATE["last_error"] = str(e)
            logger.error(f"Post-Lam listener poll error: {e}")

        _POST_LAM_STOP_EVENT.wait(poll_interval)

    with _POST_LAM_LOCK:
        POST_LAM_STATE["is_running"] = False
        POST_LAM_STATE["is_connected"] = False
    logger.info("Post-Lam EcoLAB listener loop stopped.")


def start_post_lam_listener(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _POST_LAM_THREAD, _POST_LAM_STOP_EVENT
    if cfg is None:
        configs = AuditEngine.get_machine_configs()
        cfg = configs.get("post_lam_config", {})

    if not cfg.get("enabled", True):
        return {"status": "disabled", "message": "Post-Lam EcoLAB listener is disabled in settings."}

    if _POST_LAM_THREAD and _POST_LAM_THREAD.is_alive():
        return {"status": "already_running", "message": "Post-Lam EcoLAB listener is already active."}

    _POST_LAM_STOP_EVENT.clear()
    _POST_LAM_THREAD = threading.Thread(target=_post_lam_listener_loop, args=(cfg,), daemon=True, name="PostLamListener")
    _POST_LAM_THREAD.start()
    return {"status": "started", "message": "Post-Lam EcoLAB listener successfully started."}


def stop_post_lam_listener() -> Dict[str, Any]:
    global _POST_LAM_THREAD, _POST_LAM_STOP_EVENT
    _POST_LAM_STOP_EVENT.set()
    if _POST_LAM_THREAD and _POST_LAM_THREAD.is_alive():
        _POST_LAM_THREAD.join(timeout=3)
    return {"status": "stopped", "message": "Post-Lam EcoLAB listener stopped."}


# =====================================================================
# Master Engine Initialization & Control
# =====================================================================

def init_all_machine_listeners():
    """Initializes and starts all enabled remote machine listeners on system boot."""
    configs = AuditEngine.get_machine_configs()
    ecosun_res = start_ecosun_listener(configs.get("ecosun_config", {}))
    ecohipot_res = start_ecohipot_listener(configs.get("ecohipot_config", {}))
    post_lam_res = start_post_lam_listener(configs.get("post_lam_config", {}))
    logger.info(f"Machine Listeners Init: EcoSUN: {ecosun_res['status']} | EcoHIPOT: {ecohipot_res['status']} | Post-Lam: {post_lam_res['status']}")
    return {
        "ecosun": ecosun_res,
        "ecohipot": ecohipot_res,
        "post_lam": post_lam_res
    }


def stop_all_machine_listeners():
    """Stops all active remote machine listeners."""
    r1 = stop_ecosun_listener()
    r2 = stop_ecohipot_listener()
    r3 = stop_post_lam_listener()
    return {"ecosun": r1, "ecohipot": r2, "post_lam": r3}


def get_all_machines_status() -> Dict[str, Any]:
    """Returns real-time status of all 3 remote machines."""
    with _ECOSUN_LOCK:
        s_ecosun = dict(ECOSUN_STATE)
        s_ecosun["recent_events"] = list(ECOSUN_STATE["recent_events"][:10])
    with _ECOHIPOT_LOCK:
        s_ecohipot = dict(ECOHIPOT_STATE)
        s_ecohipot["recent_events"] = list(ECOHIPOT_STATE["recent_events"][:10])
    with _POST_LAM_LOCK:
        s_post_lam = dict(POST_LAM_STATE)
        s_post_lam["recent_panels"] = list(POST_LAM_STATE["recent_panels"][:10])
        s_post_lam["processed_count"] = len(POST_LAM_STATE["processed_files_cache"])
        if "processed_files_cache" in s_post_lam:
            del s_post_lam["processed_files_cache"]

    return {
        "ecosun": s_ecosun,
        "ecohipot": s_ecohipot,
        "post_lam": s_post_lam,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
