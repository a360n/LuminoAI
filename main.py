import os
import io
import uuid
import json
import time
import zipfile
import shutil
import re
import cv2
import numpy as np
import base64
import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, BackgroundTasks
from fastapi.responses import Response, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from cropper_engine import SolarPanelCropperEngine
from batch_cropper import process_batch_directory, find_panel_folders, parse_panel_info
from el_reader_engine import ElReaderEngine
from audit_engine import AuditEngine

app = FastAPI(title="EL Solar Panel Cell Cropper API", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
CATEGORIES = ["Cracks", "Ribbons", "Misalignment", "Impurity", "Missing", "other"]

ALL_POSITIONS = []
for c in ['A', 'B', 'C', 'D', 'E', 'F']:
    for r in range(1, 25):
        ALL_POSITIONS.append(f"{c}{r}")

def normalize_pos_id(cell_id: str) -> str:
    cell_id = cell_id.strip()
    match = re.match(r'^([A-F])(0?(\d+))$', cell_id, re.IGNORECASE)
    if match:
        col = match.group(1).upper()
        num = int(match.group(3))
        return f"{col}{num}"
    return cell_id.upper()

try:
    EXPORT_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exported_cells")
    os.makedirs(EXPORT_BASE_DIR, exist_ok=True)
except PermissionError:
    EXPORT_BASE_DIR = os.path.join(os.path.expanduser("~"), "el_cropper_exports")
    os.makedirs(EXPORT_BASE_DIR, exist_ok=True)

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_efficientnet_model.pth")
ENC_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_efficientnet_model.enc")
_AI_MODEL = None
_AI_DEVICE = None

def get_ai_model():
    global _AI_MODEL, _AI_DEVICE
    if _AI_MODEL is not None:
        return _AI_MODEL, _AI_DEVICE

    from security_core import verify_license_integrity, decrypt_model_in_memory
    valid, msg = verify_license_integrity()
    if not valid:
        raise PermissionError(f"[LuminoAI Security] {msg}")

    if os.path.exists(ENC_MODEL_PATH):
        # Protected Production Release Mode: In-Memory Decryption (Zero Disk Footprint)
        decrypted_bytes = decrypt_model_in_memory(ENC_MODEL_PATH)
        load_source = io.BytesIO(decrypted_bytes)
    elif os.path.exists(MODEL_PATH):
        # Developer Source Mode
        load_source = MODEL_PATH
    else:
        raise FileNotFoundError(f"Neither protected model ({ENC_MODEL_PATH}) nor source checkpoint ({MODEL_PATH}) found.")

    if torch.cuda.is_available():
        _AI_DEVICE = torch.device("cuda")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        _AI_DEVICE = torch.device("mps")
    else:
        _AI_DEVICE = torch.device("cpu")

    model = models.efficientnet_b0(weights=None)
    num_ftrs = model.classifier[1].in_features
    model.classifier[1] = nn.Sequential(
        nn.Dropout(p=0.3),
        nn.Linear(num_ftrs, 2)
    )

    state_dict = torch.load(load_source, map_location=_AI_DEVICE)
    model.load_state_dict(state_dict)
    model.to(_AI_DEVICE)
    if _AI_DEVICE.type in ('mps', 'cuda'):
        model = model.half()
    model.eval()

    # Pre-warm contiguous batch size 144 so Metal/MPS/CUDA shaders compile on startup
    try:
        dtype = torch.float16 if _AI_DEVICE.type in ('mps', 'cuda') else torch.float32
        dummy144 = torch.zeros((144, 3, 224, 224), device=_AI_DEVICE, dtype=dtype).contiguous()
        with torch.no_grad():
            _out = model(dummy144)
            _ = torch.softmax(_out.float(), dim=1).cpu()
        if _AI_DEVICE.type == "mps":
            torch.mps.synchronize()
        elif _AI_DEVICE.type == "cuda":
            torch.cuda.synchronize()
    except Exception as e:
        print(f"⚠️ Model warmup warning: {e}")

    _AI_MODEL = model
    return _AI_MODEL, _AI_DEVICE

_NORM_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
_NORM_STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

AI_TRANSFORMS = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

@app.on_event("startup")
async def startup_warmup():
    """Preloads and warms up the AI model to eliminate cold-start latency on the first request."""
    try:
        _, device = get_ai_model()
        precision = "FP16 (Half-Precision)" if device.type in ('mps', 'cuda') else "FP32"
        print(f"🚀 LuminoAI EfficientNet-B0 Model loaded and warmed up on {device} ({precision}, batch_size=144)!")
    except Exception as e:
        print(f"⚠️ Model startup load warning: {e}")

# ----------------- AI INSPECTION PIPELINE -----------------

def run_ai_inspection_pipeline(contents: bytes, filename: str, backend_received_time: float = None) -> Dict[str, Any]:
    from security_core import verify_license_integrity
    valid, msg = verify_license_integrity()
    if not valid:
        raise HTTPException(status_code=403, detail=f"[LuminoAI Security] {msg}")

    if backend_received_time is None:
        backend_received_time = time.time()
    t_start = time.perf_counter()
    image_bgr = SolarPanelCropperEngine.load_image(contents)
    crop_res = SolarPanelCropperEngine.process_panel(image_bgr)

    model, device = get_ai_model()

    cells_dict = crop_res["cells"]
    cells_list = list(cells_dict.values())

    # Fast vectorized numpy -> tensor conversion in ONE operation (<0.01s)
    all_patches = crop_res.get("all_patches")
    if all_patches is not None and all_patches.ndim == 3:  # (144, 224, 224) 1-channel grayscale
        batch_tensor = torch.from_numpy(all_patches).unsqueeze(1).expand(-1, 3, -1, -1).contiguous().float().div_(255.0)
    elif all_patches is not None:
        np_rgb = np.ascontiguousarray(all_patches[:, :, :, ::-1])
        batch_tensor = torch.from_numpy(np_rgb).permute(0, 3, 1, 2).contiguous().float().div_(255.0)
    else:
        patches = [c["patch_bgr"] for c in cells_list]
        np_rgb = np.ascontiguousarray(np.stack(patches)[:, :, :, ::-1])
        batch_tensor = torch.from_numpy(np_rgb).permute(0, 3, 1, 2).contiguous().float().div_(255.0)

    batch_tensor.sub_(_NORM_MEAN).div_(_NORM_STD)
    if device.type in ('mps', 'cuda'):
        batch_tensor = batch_tensor.to(device=device, dtype=torch.float16).contiguous()
    else:
        batch_tensor = batch_tensor.to(device).contiguous()

    # Ultra-fast parallel inference (144-cell single pass in FP16 on GPU/MPS in ~0.3s)
    with torch.inference_mode():
        output = model(batch_tensor)
        all_probs = torch.softmax(output.float(), dim=1).cpu()

    if device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "cuda":
        torch.cuda.synchronize()

    pred_classes = torch.argmax(all_probs, dim=1).tolist()
    confidences = [float(all_probs[idx][cls].item() * 100.0) for idx, cls in enumerate(pred_classes)]

    del batch_tensor, output
    if device.type == "mps":
        torch.mps.empty_cache()
    elif device.type == "cuda":
        torch.cuda.empty_cache()

    # Pre-identify first defective and first healthy cell for stepper initial views
    first_def_idx = next((i for i in range(len(cells_list)) if pred_classes[i] == 0), None)
    first_hea_idx = next((i for i in range(len(cells_list)) if pred_classes[i] == 1), None)
    needed_thumbnails = {0}
    if first_def_idx is not None:
        needed_thumbnails.add(first_def_idx)
    if first_hea_idx is not None:
        needed_thumbnails.add(first_hea_idx)

    all_cells = []
    defective_cells = []
    healthy_cells = []

    for idx, cell_data in enumerate(cells_list):
        cell_id = cell_data["id"]
        pred_class = pred_classes[idx]
        confidence = confidences[idx]

        is_defective = (pred_class == 0)
        label = "Defective" if is_defective else "Healthy"
        label_en = "Defective ❌" if is_defective else "Healthy ✅"
        label_ar = "Defective ❌" if is_defective else "Healthy ✅"

        # Fixed ultra-lean payload (constant 180KB regardless of defect count):
        # Only encode initial thumbnails for the 3 steppers (A1, first defective, first healthy)
        # All other thumbnails are extracted client-side via offscreen canvas in 0.1ms
        if idx in needed_thumbnails:
            p = cell_data["patch_bgr"]
            _, buf = cv2.imencode('.jpg', p, [cv2.IMWRITE_JPEG_QUALITY, 75])
            b64_img = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode('ascii')
        else:
            b64_img = ""

        cell_obj = {
            "id": cell_id,
            "col": cell_data["col"],
            "row": cell_data["row"],
            "col_idx": cell_data["col_idx"],
            "row_idx": cell_data["row_idx"],
            "label": label,
            "label_en": label_en,
            "label_ar": label_ar,
            "is_defective": is_defective,
            "confidence": round(confidence, 2),
            "bbox": cell_data["bbox_padded"],
            "center": cell_data["center"],
            "b64_image": b64_img
        }

        all_cells.append(cell_obj)
        if is_defective:
            defective_cells.append(cell_obj)
        else:
            healthy_cells.append(cell_obj)

    panel_b64 = "data:image/jpeg;base64," + base64.b64encode(crop_res["full_panel_png"]).decode('utf-8')
    model_panel_b64 = ""

    total_cells = len(all_cells)
    defective_count = len(defective_cells)
    healthy_count = len(healthy_cells)
    health_rate = round((healthy_count / float(total_cells)) * 100.0, 2) if total_cells > 0 else 0.0

    meta = crop_res["metadata"]

    t_backend_ms = int(round((time.perf_counter() - t_start) * 1000))

    return {
        "status": "success",
        "filename": filename,
        "panel_image_b64": panel_b64,
        "model_panel_image_b64": model_panel_b64,
        "grid_overlay": crop_res["grid_overlay"],
        "metadata": meta,
        "backend_received_at": backend_received_time,
        "summary": {
            "total_cells": total_cells,
            "defective_count": defective_count,
            "healthy_count": healthy_count,
            "health_rate": health_rate,
            "time_seconds": round(t_backend_ms / 1000.0, 2),
            "time_ms": t_backend_ms,
            "backend_received_at": backend_received_time,
            "status_text": f"{defective_count} Defects" if defective_count > 0 else "Pass"
        },
        "all_cells": all_cells,
        "defective_cells": defective_cells,
        "healthy_cells": healthy_cells
    }

@app.post("/api/ai-inspect")
async def ai_inspect_panel(
    file: UploadFile = File(...),
    el_file: Optional[UploadFile] = File(None)
):
    t_recv = time.time()
    tif_upload = file
    el_upload = el_file

    # Smart detection in case files were swapped
    if (tif_upload.filename and tif_upload.filename.lower().endswith('.el') and 
        el_upload and el_upload.filename and el_upload.filename.lower().endswith(('.tif', '.tiff', '.png', '.jpg', '.jpeg'))):
        tif_upload, el_upload = el_upload, tif_upload

    if not tif_upload.filename:
        raise HTTPException(status_code=400, detail="No image file selected.")

    try:
        contents = await tif_upload.read()
        # 100% INDEPENDENT AI INSPECTION PIPELINE (ZERO DATA LEAKAGE)
        res_data = run_ai_inspection_pipeline(contents, tif_upload.filename, backend_received_time=t_recv)

        # Parse optional .el file independently if provided
        el_data = {"has_el_file": False, "defective_cell_ids": [], "defects": [], "defect_count": 0}
        if el_upload is not None and el_upload.filename:
            try:
                el_bytes = await el_upload.read()
                if len(el_bytes) > 0:
                    el_data = ElReaderEngine.parse_file_bytes(el_bytes, filename=el_upload.filename)
            except Exception as e:
                el_data = {"has_el_file": False, "error": str(e), "defective_cell_ids": [], "defects": [], "defect_count": 0}

        res_data["el_analysis"] = el_data

        if el_data.get("has_el_file"):
            ai_defect_ids = [c["id"] for c in res_data.get("defective_cells", [])]
            el_defect_ids = el_data.get("defective_cell_ids", [])
            comparison = ElReaderEngine.compare_ai_and_el(ai_defect_ids, el_defect_ids)
            res_data["comparison"] = comparison

            el_def_set = set(comparison["matched_defects"] + comparison["human_el_only_defects"])
            for cell in res_data.get("all_cells", []):
                cid = cell["id"]
                cell["is_el_defective"] = cid in el_def_set
                cell["is_both_defective"] = (cid in el_def_set and cell.get("is_defective", False))
        else:
            res_data["comparison"] = None

        return JSONResponse(res_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during AI panel inspection: {str(e)}")

# ----------------- LIVE AI FOLDER WATCHER API (/aipath) -----------------

AIPATH_SESSIONS: Dict[str, Dict[str, Any]] = {}

@app.post("/api/aipath/start")
async def start_aipath_watcher(folder_path: str = Form(...)):
    folder_path = folder_path.strip('"\'').strip()
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")

    session_id = str(uuid.uuid4())
    
    # Capture initial baseline snapshot of existing files (ignore them completely)
    existing_files = set()
    for root, _, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(('.tif', '.tiff', '.el')):
                existing_files.add(os.path.abspath(os.path.join(root, f)))

    AIPATH_SESSIONS[session_id] = {
        "folder_path": folder_path,
        "seen_files": existing_files,
        "history": [],
        "created_at": time.time()
    }

    return JSONResponse({
        "status": "success",
        "session_id": session_id,
        "folder_path": folder_path,
        "baseline_ignored_count": len(existing_files),
        "message": f"Live monitoring started successfully. Ignored {len(existing_files)} existing files."
    })

@app.get("/api/aipath/poll")
async def poll_aipath_watcher(session_id: str):
    if session_id not in AIPATH_SESSIONS:
        raise HTTPException(status_code=404, detail="Monitoring session not found or expired.")

    session = AIPATH_SESSIONS[session_id]
    folder_path = session["folder_path"]
    seen_files = session["seen_files"]

    # Scan for new .tif / .tiff files added after initial start
    new_file_path = None
    for root, _, files in os.walk(folder_path):
        for f in sorted(files):
            if f.lower().endswith(('.tif', '.tiff')):
                full_p = os.path.abspath(os.path.join(root, f))
                if full_p not in seen_files:
                    try:
                        if os.path.getsize(full_p) > 0:
                            new_file_path = full_p
                            break
                    except Exception:
                        continue
        if new_file_path:
            break

    if not new_file_path:
        return JSONResponse({
            "new_event": False,
            "status": "waiting",
            "message": "Waiting for new TIF panel (with paired .el file)...",
            "history_count": len(session["history"])
        })

    # Mark new file as seen
    t_recv = time.time()
    seen_files.add(new_file_path)
    filename = os.path.basename(new_file_path)

    # Read image bytes in-memory (ZERO modification to source file/folder)
    try:
        with open(new_file_path, "rb") as f:
            file_bytes = f.read()

        # 100% INDEPENDENT AI INSPECTION PIPELINE (ZERO DATA LEAKAGE)
        inspect_result = run_ai_inspection_pipeline(file_bytes, filename, backend_received_time=t_recv)
        inspect_result["filename"] = filename
        inspect_result["full_path"] = new_file_path

        # Locate corresponding .el file in folder
        matching_el_path = ElReaderEngine.find_matching_el_file(folder_path, filename)
        el_data = {"has_el_file": False, "defective_cell_ids": [], "defects": [], "defect_count": 0}
        if matching_el_path and os.path.exists(matching_el_path):
            seen_files.add(matching_el_path)
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

        session["history"].append(inspect_result)

        return JSONResponse({
            "new_event": True,
            "status": "analyzed",
            "filename": filename,
            "panel_data": inspect_result,
            "history_count": len(session["history"])
        })
    except Exception as e:
        return JSONResponse({
            "new_event": False,
            "status": "error",
            "message": f"Error processing new panel: {str(e)}"
        })

# ----------------- TIF GOOD/BAD MODEL INSPECTOR API -----------------

@app.post("/api/model-inspector/init")
async def init_model_inspector(folder_path: str = Form(...)):
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")

    bad_model_dir = os.path.join(folder_path, "bad model")
    os.makedirs(bad_model_dir, exist_ok=True)

    existing_bad_files = set(os.listdir(bad_model_dir)) if os.path.exists(bad_model_dir) else set()

    tif_files = []
    for root, dirs, files in os.walk(folder_path):
        if "bad model" in dirs:
            dirs.remove("bad model")

        for file in sorted(files):
            if file.lower().endswith(('.tif', '.tiff')):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, folder_path)
                tif_files.append({
                    "filename": file,
                    "rel_path": rel_path,
                    "full_path": full_path,
                    "is_in_bad_model": file in existing_bad_files
                })

    return JSONResponse({
        "status": "success",
        "folder_path": folder_path,
        "bad_model_dir": bad_model_dir,
        "total_tif_count": len(tif_files),
        "already_bad_count": sum(1 for f in tif_files if f["is_in_bad_model"]),
        "tif_files": tif_files
    })

@app.post("/api/model-inspector/action")
async def model_inspector_action(
    folder_path: str = Form(...),
    file_path: str = Form(...),
    action: str = Form(...)
):
    folder_path = folder_path.strip('"\'')
    file_path = file_path.strip('"\'')
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Image file not found: {file_path}")

    bad_model_dir = os.path.join(folder_path, "bad model")
    filename = os.path.basename(file_path)

    if action == "bad":
        os.makedirs(bad_model_dir, exist_ok=True)
        target_path = os.path.join(bad_model_dir, filename)
        shutil.copy2(file_path, target_path)
        return JSONResponse({
            "status": "success",
            "action": "bad",
            "message": f"Image copied to {target_path}",
            "target_path": target_path
        })
    else:
        target_path = os.path.join(bad_model_dir, filename)
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass
        return JSONResponse({
            "status": "success",
            "action": "good",
            "message": "Panel approved as Good Model and kept in its original location."
        })

@app.get("/api/model-inspector/image-preview")
async def get_model_inspector_image(path: str):
    file_path = path.strip('"\'')
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        import numpy as np
        img = cv2.imread(file_path, cv2.IMREAD_COLOR)
        if img is None:
            pil_img = Image.open(file_path)
            img = np.array(pil_img)
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        res, buf = cv2.imencode(".png", img)
        if not res:
            raise Exception("Failed to encode image")

        return Response(content=buf.tobytes(), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read panel image: {str(e)}")

# ----------------- BALANCED GOOD CELLS SORTER (3168 & not good) API -----------------

@app.post("/api/good-sorter/init")
async def init_good_cells_sorter(folder_path: str = Form(...)):
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")

    dir_3168 = os.path.join(folder_path, "3168")
    dir_not_good = os.path.join(folder_path, "not good")
    os.makedirs(dir_3168, exist_ok=True)
    os.makedirs(dir_not_good, exist_ok=True)

    pos_accepted_counts = {p: 0 for p in ALL_POSITIONS}
    pos_rejected_counts = {p: 0 for p in ALL_POSITIONS}

    for f in os.listdir(dir_3168):
        if f.lower().endswith(('.png', '.jpg', '.jpeg')):
            parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
            raw_id = parts[-1] if len(parts) > 1 else ""
            cell_id = normalize_pos_id(raw_id)
            if cell_id in pos_accepted_counts:
                pos_accepted_counts[cell_id] += 1

    for f in os.listdir(dir_not_good):
        if f.lower().endswith(('.png', '.jpg', '.jpeg')):
            parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
            raw_id = parts[-1] if len(parts) > 1 else ""
            cell_id = normalize_pos_id(raw_id)
            if cell_id in pos_rejected_counts:
                pos_rejected_counts[cell_id] += 1

    all_cells_list = []

    for f in sorted(os.listdir(folder_path)):
        full_p = os.path.join(folder_path, f)
        if os.path.isfile(full_p) and f.lower().endswith(('.png', '.jpg', '.jpeg')):
            parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
            raw_id = parts[-1] if len(parts) > 1 else "A1"
            cell_id = normalize_pos_id(raw_id)
            panel_name = "-".join(parts[:-1]) if len(parts) > 1 else "Unknown"

            all_cells_list.append({
                "filename": f,
                "full_path": full_p,
                "panel_name": panel_name,
                "cell_id": cell_id,
                "status": "unclassified",
                "rel_folder": "all good cells/"
            })

    for f in sorted(os.listdir(dir_3168)):
        full_p = os.path.join(dir_3168, f)
        if os.path.isfile(full_p) and f.lower().endswith(('.png', '.jpg', '.jpeg')):
            parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
            raw_id = parts[-1] if len(parts) > 1 else "A1"
            cell_id = normalize_pos_id(raw_id)
            panel_name = "-".join(parts[:-1]) if len(parts) > 1 else "Unknown"

            all_cells_list.append({
                "filename": f,
                "full_path": full_p,
                "panel_name": panel_name,
                "cell_id": cell_id,
                "status": "accepted",
                "rel_folder": "all good cells/3168/"
            })

    for f in sorted(os.listdir(dir_not_good)):
        full_p = os.path.join(dir_not_good, f)
        if os.path.isfile(full_p) and f.lower().endswith(('.png', '.jpg', '.jpeg')):
            parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
            raw_id = parts[-1] if len(parts) > 1 else "A1"
            cell_id = normalize_pos_id(raw_id)
            panel_name = "-".join(parts[:-1]) if len(parts) > 1 else "Unknown"

            all_cells_list.append({
                "filename": f,
                "full_path": full_p,
                "panel_name": panel_name,
                "cell_id": cell_id,
                "status": "rejected",
                "rel_folder": "all good cells/not good/"
            })

    total_accepted = sum(pos_accepted_counts.values())
    total_rejected = sum(pos_rejected_counts.values())

    active_position = ALL_POSITIONS[0]
    for pos in ALL_POSITIONS:
        if pos_accepted_counts[pos] < 22:
            active_position = pos
            break

    return JSONResponse({
        "folder_path": folder_path,
        "total_cells": len(all_cells_list),
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "target_total": 3168,
        "target_per_position": 22,
        "active_position": active_position,
        "pos_accepted_counts": pos_accepted_counts,
        "pos_rejected_counts": pos_rejected_counts,
        "cells": all_cells_list,
        "all_positions": ALL_POSITIONS
    })

@app.post("/api/good-sorter/action")
async def good_cells_sorter_action(
    folder_path: str = Form(...),
    file_path: str = Form(...),
    action: str = Form(...)
):
    folder_path = folder_path.strip('"\'')
    file_path = file_path.strip('"\'')
    action = action.strip().lower()

    if action not in ['accepted', 'rejected']:
        raise HTTPException(status_code=400, detail="Action must be 'accepted' or 'rejected'")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    filename = os.path.basename(file_path)
    target_sub = "3168" if action == "accepted" else "not good"
    target_dir = os.path.join(folder_path, target_sub)
    os.makedirs(target_dir, exist_ok=True)

    new_full_path = os.path.join(target_dir, filename)
    if file_path != new_full_path:
        shutil.move(file_path, new_full_path)

    dir_3168 = os.path.join(folder_path, "3168")
    dir_not_good = os.path.join(folder_path, "not good")

    pos_accepted_counts = {p: 0 for p in ALL_POSITIONS}
    pos_rejected_counts = {p: 0 for p in ALL_POSITIONS}

    if os.path.exists(dir_3168):
        for f in os.listdir(dir_3168):
            if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
                raw_id = parts[-1] if len(parts) > 1 else ""
                cell_id = normalize_pos_id(raw_id)
                if cell_id in pos_accepted_counts:
                    pos_accepted_counts[cell_id] += 1

    if os.path.exists(dir_not_good):
        for f in os.listdir(dir_not_good):
            if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                parts = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').split('-')
                raw_id = parts[-1] if len(parts) > 1 else ""
                cell_id = normalize_pos_id(raw_id)
                if cell_id in pos_rejected_counts:
                    pos_rejected_counts[cell_id] += 1

    total_accepted = sum(pos_accepted_counts.values())
    total_rejected = sum(pos_rejected_counts.values())

    active_position = ALL_POSITIONS[0]
    for pos in ALL_POSITIONS:
        if pos_accepted_counts[pos] < 22:
            active_position = pos
            break

    return JSONResponse({
        "status": "success",
        "old_path": file_path,
        "new_path": new_full_path,
        "action": action,
        "target_subfolder": target_sub,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "active_position": active_position,
        "pos_accepted_counts": pos_accepted_counts,
        "pos_rejected_counts": pos_rejected_counts
    })

# ----------------- BAD CELLS SORTER API -----------------

@app.post("/api/sorter/init")
async def init_bad_cells_sorter(folder_path: str = Form(...)):
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")

    category_counts = {}
    for cat in CATEGORIES:
        cat_dir = os.path.join(folder_path, cat)
        os.makedirs(cat_dir, exist_ok=True)
        category_counts[cat] = 0

    cell_files_list = []
    
    for f in sorted(os.listdir(folder_path)):
        full_p = os.path.join(folder_path, f)
        if os.path.isfile(full_p) and f.lower().endswith(('.png', '.jpg', '.jpeg')):
            filename_clean = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '')
            parts = filename_clean.split('-')
            cell_id = parts[-1] if len(parts) > 1 else filename_clean
            panel_name = "-".join(parts[:-1]) if len(parts) > 1 else "Unknown"

            cell_files_list.append({
                "filename": f,
                "full_path": full_p,
                "panel_name": panel_name,
                "cell_id": cell_id,
                "category": "unclassified",
                "rel_folder": "all bad cells/"
            })

    for cat in CATEGORIES:
        cat_dir = os.path.join(folder_path, cat)
        if os.path.exists(cat_dir):
            cat_files = [f for f in sorted(os.listdir(cat_dir)) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
            category_counts[cat] = len(cat_files)
            for f in cat_files:
                full_p = os.path.join(cat_dir, f)
                filename_clean = f.replace('.png', '').replace('.jpg', '').replace('.jpeg', '')
                parts = filename_clean.split('-')
                cell_id = parts[-1] if len(parts) > 1 else filename_clean
                panel_name = "-".join(parts[:-1]) if len(parts) > 1 else "Unknown"

                cell_files_list.append({
                    "filename": f,
                    "full_path": full_p,
                    "panel_name": panel_name,
                    "cell_id": cell_id,
                    "category": cat,
                    "rel_folder": f"all bad cells/{cat}/"
                })

    total_cells = len(cell_files_list)
    sorted_cells = sum(category_counts.values())
    remaining_cells = total_cells - sorted_cells

    return JSONResponse({
        "folder_path": folder_path,
        "total_cells": total_cells,
        "sorted_cells": sorted_cells,
        "remaining_cells": remaining_cells,
        "category_counts": category_counts,
        "cells": cell_files_list
    })

@app.post("/api/sorter/move")
async def move_cell_to_category(
    folder_path: str = Form(...),
    file_path: str = Form(...),
    target_category: str = Form(...)
):
    folder_path = folder_path.strip('"\'')
    file_path = file_path.strip('"\'')
    target_category = target_category.strip()

    if target_category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {target_category}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    filename = os.path.basename(file_path)
    target_dir = os.path.join(folder_path, target_category)
    os.makedirs(target_dir, exist_ok=True)
    new_full_path = os.path.join(target_dir, filename)

    if file_path != new_full_path:
        shutil.move(file_path, new_full_path)

    category_counts = {}
    total_sorted = 0
    for cat in CATEGORIES:
        cat_dir = os.path.join(folder_path, cat)
        if os.path.exists(cat_dir):
            count = len([f for f in os.listdir(cat_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
            category_counts[cat] = count
            total_sorted += count
        else:
            category_counts[cat] = 0

    return JSONResponse({
        "status": "success",
        "old_path": file_path,
        "new_path": new_full_path,
        "category": target_category,
        "category_counts": category_counts,
        "total_sorted": total_sorted
    })

# ----------------- BASE API -----------------

@app.post("/api/upload")
async def upload_panel_image(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected.")
    try:
        contents = await file.read()
        image_bgr = SolarPanelCropperEngine.load_image(contents)
        result = SolarPanelCropperEngine.process_panel(image_bgr)

        session_id = str(uuid.uuid4())
        SESSION_CACHE[session_id] = {
            "filename": file.filename,
            "result": result
        }

        cell_summary = []
        for cell_id, cell_data in result["cells"].items():
            cell_summary.append({
                "id": cell_id,
                "col": cell_data["col"],
                "row": cell_data["row"],
                "bbox": cell_data["bbox_padded"],
                "center": cell_data["center"]
            })

        return JSONResponse({
            "status": "success",
            "session_id": session_id,
            "filename": file.filename,
            "metadata": result["metadata"],
            "grid_overlay": result["grid_overlay"],
            "cells": cell_summary
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image processing error: {str(e)}")

@app.post("/api/batch-process")
async def batch_process_folder(folder_path: str = Form(...)):
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")
    try:
        results = process_batch_directory(folder_path)
        return JSONResponse({
            "status": "success",
            "message": f"Successfully processed {results['success_count']} panels out of {results['total_panels']}.",
            "results": results
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during batch processing: {str(e)}")

@app.get("/api/scan-folder")
async def scan_folder_info(folder_path: str):
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")
    panels = find_panel_folders(folder_path)
    return JSONResponse({
        "folder_path": folder_path,
        "total_panels": len(panels),
        "panels": panels
    })

@app.get("/api/bad-panels-list")
async def get_bad_panels_list(folder_path: str):
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder_path}")

    all_panels = find_panel_folders(folder_path)
    bad_panels = []

    for panel in all_panels:
        category = panel["category"]
        panel_dir = panel["panel_dir"]
        info = parse_panel_info(panel_dir)

        if category == "bad_models" or info["is_defective"] or len(info["defective_cell_ids"]) > 0:
            bad_cell_dir = os.path.join(panel_dir, "bad cells")
            bad_cell_files = []
            if os.path.exists(bad_cell_dir):
                bad_cell_files = [
                    {"filename": f, "path": os.path.join(bad_cell_dir, f)}
                    for f in sorted(os.listdir(bad_cell_dir)) if f.endswith(".png")
                ]

            raw_json = {}
            json_path = os.path.join(panel_dir, "info.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
                        raw_json = json.load(f)
                except Exception:
                    pass

            bad_panels.append({
                "panel_name": panel["panel_name"],
                "panel_dir": panel_dir,
                "tif_path": panel["tif_path"],
                "category": category,
                "info": {
                    "is_defective": info["is_defective"],
                    "defects": info["defects"],
                    "defective_cell_ids": sorted(list(info["defective_cell_ids"]))
                },
                "raw_json": raw_json,
                "bad_cell_files": bad_cell_files
            })

    return JSONResponse({
        "folder_path": folder_path,
        "total_bad_panels": len(bad_panels),
        "bad_panels": bad_panels
    })

@app.get("/api/panel-file-preview")
async def preview_panel_file(path: str):
    path = path.strip('"\'')
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        with open(path, "rb") as f:
            file_bytes = f.read()
        image_bgr = SolarPanelCropperEngine.load_image(file_bytes)
        _, png_buf = cv2.imencode('.png', image_bgr)
        return Response(content=png_buf.tobytes(), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cell-file-preview")
async def preview_cell_file(path: str):
    path = path.strip('"\'')
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/png")

@app.get("/api/panel-image/{session_id}")
async def get_panel_image(session_id: str):
    if session_id not in SESSION_CACHE:
        raise HTTPException(status_code=404, detail="Session not found.")
    png_bytes = SESSION_CACHE[session_id]["result"]["full_panel_png"]
    return Response(content=png_bytes, media_type="image/png")

@app.get("/api/cell-image/{session_id}/{cell_id}")
async def get_cell_image(session_id: str, cell_id: str):
    cell_id = cell_id.upper()
    if session_id not in SESSION_CACHE:
        raise HTTPException(status_code=404, detail="Session not found.")
    cells = SESSION_CACHE[session_id]["result"]["cells"]
    if cell_id not in cells:
        raise HTTPException(status_code=404, detail=f"Cell {cell_id} not found.")
    return Response(content=cells[cell_id]["png_bytes"], media_type="image/png")

@app.get("/api/export/zip/{session_id}")
async def export_zip(session_id: str):
    if session_id not in SESSION_CACHE:
        raise HTTPException(status_code=404, detail="Session not found.")
    session_data = SESSION_CACHE[session_id]
    orig_filename = os.path.splitext(session_data["filename"])[0]
    cells = session_data["result"]["cells"]

    zip_io = io.BytesIO()
    with zipfile.ZipFile(zip_io, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for cell_id, cell_data in cells.items():
            file_name_in_zip = f"{orig_filename}-{cell_id}.png"
            zf.writestr(file_name_in_zip, cell_data["png_bytes"])

    zip_io.seek(0)
    zip_filename = f"{orig_filename}_cells_A1-F24.zip"
    return StreamingResponse(
        zip_io,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'}
    )

@app.post("/api/export/folder/{session_id}")
async def export_to_folder(session_id: str, custom_path: str = Form(None)):
    if session_id not in SESSION_CACHE:
        raise HTTPException(status_code=404, detail="Session not found.")
    session_data = SESSION_CACHE[session_id]
    orig_filename = os.path.splitext(session_data["filename"])[0]
    cells = session_data["result"]["cells"]

    if custom_path and custom_path.strip():
        target_dir = os.path.abspath(custom_path.strip())
    else:
        target_dir = os.path.join(EXPORT_BASE_DIR, orig_filename)

    os.makedirs(target_dir, exist_ok=True)
    saved_count = 0
    for cell_id, cell_data in cells.items():
        file_path = os.path.join(target_dir, f"{orig_filename}-{cell_id}.png")
        with open(file_path, "wb") as f:
            f.write(cell_data["png_bytes"])
        saved_count += 1

    return JSONResponse({
        "status": "success",
        "message": f"Successfully saved {saved_count} cells.",
        "target_directory": target_dir,
        "saved_count": saved_count
    })

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def read_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return Response(content="<h1>EL Cell Cropper App</h1>", media_type="text/html")

@app.get("/ai")
async def read_ai_page():
    ai_file = os.path.join(STATIC_DIR, "ai.html")
    if os.path.exists(ai_file):
        return FileResponse(ai_file)
    return Response(content="<h1>EL AI Solar Inspection Page</h1>", media_type="text/html")

@app.get("/inspector")
async def read_inspector_page():
    inspector_file = os.path.join(STATIC_DIR, "inspector.html")
    if os.path.exists(inspector_file):
        return FileResponse(inspector_file)
    return Response(content="<h1>EL TIF Model Inspector Page</h1>", media_type="text/html")

@app.get("/aipath")
async def read_aipath_page():
    aipath_file = os.path.join(STATIC_DIR, "aipath.html")
    if os.path.exists(aipath_file):
        return FileResponse(aipath_file)
    return Response(content="<h1>EL AI Path Live Watcher Page</h1>", media_type="text/html")

@app.get("/audit")
async def read_audit_page():
    audit_file = os.path.join(STATIC_DIR, "audit.html")
    if os.path.exists(audit_file):
        return FileResponse(audit_file)
    return Response(content="<h1>EL Audited Panels Page</h1>", media_type="text/html")

@app.get("/settings")
async def read_settings_page():
    settings_file = os.path.join(STATIC_DIR, "settings.html")
    if os.path.exists(settings_file):
        return FileResponse(settings_file)
    return Response(content="<h1>Settings Page</h1>", media_type="text/html")

# ----------------- AUDIT & APPROVAL APIS -----------------

@app.post("/api/audit/save")
async def save_audit_endpoint(payload: Dict[str, Any]):
    try:
        res = AuditEngine.save_audit_record(payload)
        return JSONResponse(res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save audit decision: {str(e)}")

@app.get("/api/audit/records")
async def get_audit_records_endpoint(filter_type: str = "all", search: str = ""):
    try:
        records = AuditEngine.get_all_audited_records(filter_type=filter_type, search=search)
        return JSONResponse({"status": "success", "records": records, "count": len(records)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch audit records: {str(e)}")

@app.get("/api/audit/record/{record_id}")
async def get_audit_record_detail(record_id: int):
    record = AuditEngine.get_record_detail(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Audit record not found.")
    return JSONResponse({"status": "success", "record": record})

@app.get("/api/audit/stats")
async def get_audit_stats_endpoint():
    try:
        stats = AuditEngine.get_global_audit_statistics()
        return JSONResponse({"status": "success", "stats": stats})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch audit statistics: {str(e)}")

# ----------------- OPERATOR MANAGEMENT APIS -----------------

@app.get("/api/operators")
async def get_operators_endpoint():
    try:
        operators = AuditEngine.get_all_operators()
        active = AuditEngine.get_active_operator()
        return JSONResponse({"status": "success", "operators": operators, "active_operator": active})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch operators: {str(e)}")

@app.post("/api/operators")
async def add_operator_endpoint(payload: Dict[str, Any]):
    name = payload.get("name", "")
    role = payload.get("role", "Line Inspector")
    try:
        res = AuditEngine.add_operator(name, role)
        return JSONResponse(res)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add operator: {str(e)}")

@app.delete("/api/operators/{name_or_id}")
async def delete_operator_endpoint(name_or_id: str):
    try:
        res = AuditEngine.delete_operator(name_or_id)
        return JSONResponse(res)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete operator: {str(e)}")

@app.get("/api/operators/active")
async def get_active_operator_endpoint():
    try:
        active = AuditEngine.get_active_operator()
        return JSONResponse({"status": "success", "active_operator": active})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch active operator: {str(e)}")

@app.post("/api/operators/active")
async def set_active_operator_endpoint(payload: Dict[str, Any]):
    name = payload.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="Operator name is required.")
    try:
        res = AuditEngine.set_active_operator(name)
        return JSONResponse(res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set active operator: {str(e)}")

# ----------------- BRANDING & FACTORY THEME APIS -----------------

@app.get("/api/settings/branding")
async def get_branding_endpoint():
    try:
        settings = AuditEngine.get_branding_settings()
        return JSONResponse({"status": "success", "branding": settings})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch branding settings: {str(e)}")

@app.post("/api/settings/branding")
async def update_branding_endpoint(payload: Dict[str, Any]):
    try:
        updated = AuditEngine.update_branding_settings(payload)
        return JSONResponse({"status": "success", "branding": updated})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update branding settings: {str(e)}")

@app.post("/api/settings/branding/reset")
async def reset_branding_endpoint():
    try:
        reset = AuditEngine.reset_branding_settings()
        return JSONResponse({"status": "success", "branding": reset})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset branding settings: {str(e)}")

