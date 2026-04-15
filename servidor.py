import io
import json
import os
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import cv2
import boto3
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image
from botocore.exceptions import ClientError
from werkzeug.utils import secure_filename

from data_manager import ScanDataManager
from scanner_core import (
    ajustar_a_carta,
    aplicar_filtro,
    decode_data_url,
    detectar_documento,
    ordenar_puntos,
    transformar,
)

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "fuentes"
OUTPUT_DIR = BASE_DIR / "usos_separados"

for folder in [DATA_DIR, OUTPUT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = {".xlsx", ".xls"}

manager = ScanDataManager()
startup_message = ""

CLEANUP_AFTER_ZIP_SECONDS = int(os.getenv("CLEANUP_AFTER_ZIP_SECONDS", "900"))
INACTIVITY_CLEANUP_SECONDS = int(os.getenv("INACTIVITY_CLEANUP_SECONDS", "0"))
CLEANUP_POLL_SECONDS = int(os.getenv("CLEANUP_POLL_SECONDS", "30"))
RENDER_IDLE_SECONDS = int(os.getenv("RENDER_IDLE_SECONDS", "900"))

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.getenv("R2_BUCKET", "").strip()
R2_PREFIX = os.getenv("R2_PREFIX", "scanner-usos-imagen").strip().strip("/")
R2_STATE_KEY = f"{R2_PREFIX}/session_state.json" if R2_PREFIX else "session_state.json"

_cleanup_lock = threading.Lock()
_session_meta = {
    "last_activity": time.time(),
    "zip_downloaded_at": None,
}

_r2_client = None
_r2_enabled = False


def _status_payload():
    return manager.status()


def _error(msg, status_code=400):
    return jsonify({"ok": False, "msg": msg, **_status_payload()}), status_code


def _load_default_source():
    return None


def _save_outputs(image_bgr, id_value):
    pdf_path = OUTPUT_DIR / f"{id_value}.pdf"

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(image_rgb)
    pdf_buffer = io.BytesIO()
    pil_img.save(pdf_buffer, "PDF")
    pdf_bytes = pdf_buffer.getvalue()
    pdf_path.write_bytes(pdf_bytes)

    return pdf_path, pdf_bytes


def _build_zip_from_paths(pdf_paths):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for path in pdf_paths:
            p = Path(path)
            if p.exists() and p.suffix.lower() == ".pdf":
                zip_file.write(p, arcname=p.name)

    zip_buffer.seek(0)
    return zip_buffer


def _safe_key_part(value):
    return secure_filename(str(value or "")).strip() or "sin_nombre"


def _build_r2_key(*parts):
    clean_parts = [str(p).strip("/") for p in parts if str(p).strip("/")]
    return "/".join(clean_parts)


def _r2_can_use():
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET)


def _r2_init():
    global _r2_client
    global _r2_enabled

    if _r2_client is not None:
        return _r2_client

    if not _r2_can_use():
        _r2_enabled = False
        return None

    endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    _r2_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    _r2_enabled = True
    return _r2_client


def _r2_put_bytes(key, data, content_type="application/octet-stream"):
    if not _r2_enabled:
        return False

    try:
        client = _r2_init()
        if client is None:
            return False
        client.put_object(Bucket=R2_BUCKET, Key=key, Body=data, ContentType=content_type)
        return True
    except Exception:
        return False


def _r2_get_bytes(key):
    if not _r2_enabled:
        return None

    try:
        client = _r2_init()
        if client is None:
            return None
        response = client.get_object(Bucket=R2_BUCKET, Key=key)
        return response["Body"].read()
    except ClientError:
        return None
    except Exception:
        return None


def _r2_delete_key(key):
    if not _r2_enabled or not key:
        return

    try:
        client = _r2_init()
        if client is None:
            return
        client.delete_object(Bucket=R2_BUCKET, Key=key)
    except Exception:
        return


def _build_zip_from_history(history_items):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for item in history_items:
            pdf_name = f"{item.get('id', 'archivo')}.pdf"
            pdf_path = Path(item.get("pdf", "")) if item.get("pdf") else None
            pdf_r2_key = item.get("pdf_r2_key", "")

            content = None
            if pdf_path and pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
                content = pdf_path.read_bytes()
            elif pdf_r2_key:
                content = _r2_get_bytes(pdf_r2_key)

            if content:
                zip_file.writestr(pdf_name, content)

    zip_buffer.seek(0)
    return zip_buffer


def _persist_state_locked():
    if not _r2_enabled:
        return

    payload = {
        "manager": manager.export_state(),
        "meta": {
            "last_activity": _session_meta["last_activity"],
            "zip_downloaded_at": _session_meta["zip_downloaded_at"],
        },
    }
    _r2_put_bytes(
        R2_STATE_KEY,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )


def _restore_state_from_r2():
    if not _r2_enabled:
        return False

    raw = _r2_get_bytes(R2_STATE_KEY)
    if not raw:
        return False

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return False

    manager.import_state(payload.get("manager", {}))
    meta = payload.get("meta", {}) or {}
    _session_meta["last_activity"] = float(meta.get("last_activity", time.time()))
    _session_meta["zip_downloaded_at"] = meta.get("zip_downloaded_at")
    return True


def _touch_activity_locked(now=None):
    _session_meta["last_activity"] = now if now is not None else time.time()


def _touch_activity():
    with _cleanup_lock:
        _touch_activity_locked()


def _mark_zip_downloaded():
    with _cleanup_lock:
        now = time.time()
        _session_meta["zip_downloaded_at"] = now
        _touch_activity_locked(now)
        _persist_state_locked()


def _collect_session_paths():
    files = set()

    for item in manager.history:
        pdf = item.get("pdf", "")
        if pdf:
            files.add(Path(pdf))

    if manager.source_disk_path:
        files.add(Path(manager.source_disk_path))

    return files


def _cleanup_session_storage_locked(reason=""):
    for file_path in _collect_session_paths():
        try:
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
        except Exception:
            continue

    if manager.source_r2_key:
        _r2_delete_key(manager.source_r2_key)

    for item in manager.history:
        _r2_delete_key(item.get("pdf_r2_key", ""))

    manager.clear_all()
    _session_meta["zip_downloaded_at"] = None
    _touch_activity_locked()
    _persist_state_locked()


def _maybe_cleanup_session_files():
    with _cleanup_lock:
        has_session_data = bool(manager.records or manager.history or manager.source_disk_path)
        if not has_session_data:
            return

        now = time.time()
        zip_downloaded_at = _session_meta["zip_downloaded_at"]
        last_activity = _session_meta["last_activity"]

        if zip_downloaded_at and now - zip_downloaded_at >= CLEANUP_AFTER_ZIP_SECONDS:
            _cleanup_session_storage_locked("after-zip")
            return

        if INACTIVITY_CLEANUP_SECONDS > 0 and now - last_activity >= INACTIVITY_CLEANUP_SECONDS:
            _cleanup_session_storage_locked("inactivity")


def _cleanup_worker():
    interval = max(10, CLEANUP_POLL_SECONDS)
    while True:
        time.sleep(interval)
        try:
            _maybe_cleanup_session_files()
        except Exception:
            pass


threading.Thread(target=_cleanup_worker, daemon=True, name="session-cleanup-worker").start()


_r2_init()
if _restore_state_from_r2() and manager.records:
    startup_message = "Sesion recuperada desde R2. Puedes continuar donde ibas."
else:
    startup_message = "Carga el archivo de caracterizacion para comenzar."


@app.route("/")
def index():
    return render_template(
        "index.html",
        startup_message=startup_message,
        render_idle_seconds=max(0, RENDER_IDLE_SECONDS),
    )


@app.before_request
def before_request_touch():
    if request.endpoint != "static":
        _touch_activity()


@app.route("/status")
def status():
    return jsonify(_status_payload())


@app.route("/load-source", methods=["POST"])
def load_source():
    with _cleanup_lock:
        if manager.records or manager.history or manager.source_disk_path:
            _cleanup_session_storage_locked("new-source")

    if "file" not in request.files:
        return _error("Debes seleccionar un archivo para cargar.")

    file = request.files["file"]
    filename = secure_filename(file.filename or "")
    if not filename:
        return _error("Nombre de archivo invalido.")

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return _error("Formato no soportado. Usa xlsx o xls.")

    destination = DATA_DIR / filename
    file.save(destination)

    try:
        manager.load_source(destination)
    except Exception as exc:
        if destination.exists():
            destination.unlink()
        return _error(f"No se pudo leer el archivo: {exc}")

    if _r2_enabled:
        source_key = _build_r2_key(R2_PREFIX, "source", f"{int(time.time())}_{_safe_key_part(filename)}")
        if _r2_put_bytes(source_key, destination.read_bytes(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
            manager.source_r2_key = source_key

    with _cleanup_lock:
        _persist_state_locked()

    return jsonify(
        {
            "ok": True,
            "msg": f"Fuente cargada desde {filename}",
            **_status_payload(),
        }
    )


@app.route("/preview-source", methods=["GET"])
def preview_source():
    if not manager.records:
        return _error("Primero carga un archivo de caracterizacion.", 404)

    return jsonify(
        {
            "ok": True,
            "records": [
                {
                    "index": idx,
                    "id": record.get("id", ""),
                    "full_name": record.get("full_name", ""),
                    "first_name": record.get("first_name", ""),
                    "second_name": record.get("second_name", ""),
                    "first_last_name": record.get("first_last_name", ""),
                    "second_last_name": record.get("second_last_name", ""),
                }
                for idx, record in enumerate(manager.records)
            ],
            **_status_payload(),
        }
    )


@app.route("/set-selection", methods=["POST"])
def set_selection():
    payload = request.json or {}
    selected_indices = payload.get("selected_indices", [])

    try:
        manager.set_selection(selected_indices)
    except ValueError as exc:
        return _error(str(exc))

    with _cleanup_lock:
        _persist_state_locked()

    return jsonify(
        {
            "ok": True,
            "msg": f"Seleccion aplicada: {len(manager.selected_indices)} personas.",
            **_status_payload(),
        }
    )


@app.route("/set-target", methods=["POST"])
def set_target():
    payload = request.json or {}
    try:
        target = int(payload.get("target", 0))
        manager.set_target(target)
    except ValueError as exc:
        return _error(str(exc))
    except Exception:
        return _error("Meta invalida.")

    with _cleanup_lock:
        _persist_state_locked()

    return jsonify(
        {
            "ok": True,
            "msg": f"Meta actualizada a {target}.",
            **_status_payload(),
        }
    )


@app.route("/reset-session", methods=["POST"])
def reset_session():
    manager.reset_session()
    with _cleanup_lock:
        _persist_state_locked()
    return jsonify(
        {
            "ok": True,
            "msg": "Jornada reiniciada. Contador e historial en cero.",
            **_status_payload(),
        }
    )


@app.route("/auto-corners", methods=["POST"])
def auto_corners():
    raw = (request.json or {}).get("img", "")
    if not raw:
        return _error("Sin imagen", 400)

    try:
        image = decode_data_url(raw)
        doc = detectar_documento(image)
        if doc is None:
            return jsonify({"ok": False, "msg": "No detectado", **_status_payload()})

        points = ordenar_puntos(doc.reshape(4, 2)).tolist()
        return jsonify({"ok": True, "corners": points, **_status_payload()})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception:
        return _error("Error detectando esquinas", 500)


@app.route("/upload", methods=["POST"])
def upload():
    if manager.total() == 0:
        return _error("No hay personas seleccionadas. Sube el Excel y aplica la seleccion.")

    if not manager.selection_locked:
        return _error("Primero aplica la seleccion de personas antes de capturar.")

    if manager.counter >= manager.total():
        return _error("Meta alcanzada. No puedes escanear mas.")

    payload = request.json or {}
    raw = payload.get("img", "")
    if not raw:
        return _error("Sin imagen")

    try:
        image = decode_data_url(raw)
    except Exception as exc:
        return _error(str(exc))

    try:
        corners = payload.get("corners")
        if corners and len(corners) == 4:
            image = transformar(image, corners)
        else:
            detected = detectar_documento(image)
            if detected is not None:
                image = transformar(image, detected.reshape(4, 2))

        filtro = payload.get("filter", "normal")
        image = aplicar_filtro(image, filtro)
        image = ajustar_a_carta(image)
    except Exception:
        return _error("Error procesando imagen", 500)

    record = manager.current_record()
    if record is None:
        return _error("No hay mas registros para procesar.")

    id_value = record["id"]

    try:
        pdf_path, pdf_bytes = _save_outputs(image, id_value)
    except Exception:
        return _error("No se pudo guardar el PDF", 500)

    pdf_r2_key = ""
    if _r2_enabled:
        pdf_r2_key = _build_r2_key(R2_PREFIX, "pdfs", f"{_safe_key_part(id_value)}.pdf")
        _r2_put_bytes(pdf_r2_key, pdf_bytes, content_type="application/pdf")

    manager.register_scan(id_value, pdf_path, pdf_r2_key=pdf_r2_key)
    with _cleanup_lock:
        _persist_state_locked()
    status_payload = _status_payload()

    display_name = record.get("full_name", "").strip()
    extra = f" - {display_name}" if display_name else ""
    return jsonify(
        {
            "ok": True,
            "msg": f"Guardado {id_value}.pdf{extra} ({status_payload['current']}/{status_payload['total']})",
            **status_payload,
        }
    )


@app.route("/undo-last", methods=["POST"])
def undo_last():
    last = manager.undo_last()
    if last is None:
        return _error("No hay escaneos para deshacer.")

    pdf_path = Path(last["pdf"])
    if pdf_path.exists():
        pdf_path.unlink()

    _r2_delete_key(last.get("pdf_r2_key", ""))
    with _cleanup_lock:
        _persist_state_locked()

    return jsonify(
        {
            "ok": True,
            "msg": f"Se deshizo {last['id']}.pdf",
            **_status_payload(),
        }
    )


@app.route("/download-zip", methods=["GET"])
def download_zip():
    scope = (request.args.get("scope") or "session").strip().lower()

    if scope == "all":
        pdf_paths = sorted(
            [
                p
                for p in OUTPUT_DIR.glob("*.pdf")
                if p.is_file()
            ],
            key=lambda x: x.name,
        )
        filename_prefix = "todos"
        if not pdf_paths:
            return _error("No hay PDFs para comprimir.", 404)
        zip_buffer = _build_zip_from_paths(pdf_paths)
    else:
        history_items = list(manager.history)
        filename_prefix = "jornada"
        if not history_items:
            return _error("No hay PDFs para comprimir.", 404)
        zip_buffer = _build_zip_from_history(history_items)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_name = f"scans_{filename_prefix}_{timestamp}.zip"

    if scope == "session":
        _mark_zip_downloaded()

    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/zip",
    )


@app.route("/pdf/<string:id_value>", methods=["GET"])
def view_pdf(id_value):
    safe_id = Path(id_value).name.replace(".pdf", "")
    pdf_path = OUTPUT_DIR / f"{safe_id}.pdf"

    if pdf_path.exists():
        return send_file(pdf_path, mimetype="application/pdf")

    entry = next((item for item in manager.history if str(item.get("id", "")) == safe_id), None)
    if entry:
        pdf_r2_key = entry.get("pdf_r2_key", "")
    else:
        pdf_r2_key = _build_r2_key(R2_PREFIX, "pdfs", f"{_safe_key_part(safe_id)}.pdf")

    content = _r2_get_bytes(pdf_r2_key)
    if not content:
        return "PDF no encontrado", 404

    return send_file(io.BytesIO(content), mimetype="application/pdf", download_name=f"{safe_id}.pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
