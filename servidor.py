import io
import json
import os
import re
import secrets
import string
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import boto3
import cv2
from botocore.exceptions import ClientError
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image
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

CLEANUP_AFTER_ZIP_SECONDS = int(os.getenv("CLEANUP_AFTER_ZIP_SECONDS", "86400"))
INACTIVITY_CLEANUP_SECONDS = int(os.getenv("INACTIVITY_CLEANUP_SECONDS", "0"))
CLEANUP_POLL_SECONDS = int(os.getenv("CLEANUP_POLL_SECONDS", "30"))
RENDER_IDLE_SECONDS = int(os.getenv("RENDER_IDLE_SECONDS", "900"))

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.getenv("R2_BUCKET", "").strip()
R2_PREFIX = os.getenv("R2_PREFIX", "scanner-usos-imagen").strip().strip("/")

SESSION_CODE_PATTERN = re.compile(r"^[A-Z0-9_-]{4,32}$")

_state_lock = threading.Lock()
_sessions = {}
_r2_client = None
_r2_enabled = False


def _safe_key_part(value):
    return secure_filename(str(value or "")).strip() or "sin_nombre"


def _build_r2_key(*parts):
    clean_parts = [str(p).strip("/") for p in parts if str(p).strip("/")]
    return "/".join(clean_parts)


def _r2_state_key(session_code):
    return _build_r2_key(R2_PREFIX, "sessions", session_code, "state.json")


def _r2_source_key(session_code, filename):
    return _build_r2_key(
        R2_PREFIX,
        "sessions",
        session_code,
        "source",
        f"{int(time.time())}_{_safe_key_part(filename)}",
    )


def _r2_pdf_key(session_code, id_value):
    return _build_r2_key(R2_PREFIX, "sessions", session_code, "pdfs", f"{_safe_key_part(id_value)}.pdf")


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


def _create_context():
    return {
        "manager": ScanDataManager(),
        "meta": {
            "last_activity": time.time(),
            "zip_downloaded_at": None,
        },
        "restored": False,
    }


def _normalize_session_code(value):
    code = str(value or "").strip().upper()
    if not SESSION_CODE_PATTERN.match(code):
        return ""
    return code


def _generate_session_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _get_requested_session_code():
    code = request.headers.get("X-Session-Code", "")
    if not code:
        code = request.args.get("session", "")
    if not code and request.method in {"POST", "PUT", "PATCH"}:
        payload = request.get_json(silent=True) or {}
        code = payload.get("session_code", "")
    return _normalize_session_code(code)


def _status_payload(ctx):
    return ctx["manager"].status()


def _error(msg, status_code=400, ctx=None):
    payload = {"ok": False, "msg": msg}
    if ctx is not None:
        payload.update(_status_payload(ctx))
    return jsonify(payload), status_code


def _persist_state_locked(session_code, ctx):
    if not _r2_enabled:
        return

    payload = {
        "manager": ctx["manager"].export_state(),
        "meta": ctx["meta"],
    }
    _r2_put_bytes(
        _r2_state_key(session_code),
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )


def _restore_state_locked(session_code, ctx):
    if not _r2_enabled:
        return False

    raw = _r2_get_bytes(_r2_state_key(session_code))
    if not raw:
        return False

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return False

    ctx["manager"].import_state(payload.get("manager", {}))
    ctx["meta"] = payload.get("meta", {}) or {"last_activity": time.time(), "zip_downloaded_at": None}
    if "last_activity" not in ctx["meta"]:
        ctx["meta"]["last_activity"] = time.time()
    if "zip_downloaded_at" not in ctx["meta"]:
        ctx["meta"]["zip_downloaded_at"] = None
    ctx["restored"] = True
    return True


def _get_context_locked(session_code, create_if_missing=True):
    ctx = _sessions.get(session_code)
    if ctx is None and create_if_missing:
        ctx = _create_context()
        _sessions[session_code] = ctx
        _restore_state_locked(session_code, ctx)
    return ctx


def _touch_activity_locked(ctx, persist=False):
    ctx["meta"]["last_activity"] = time.time()
    if persist:
        for code, value in _sessions.items():
            if value is ctx:
                _persist_state_locked(code, ctx)
                break


def _delete_entry_files(entry):
    pdf_path = Path(entry.get("pdf", "")) if entry.get("pdf") else None
    if pdf_path and pdf_path.exists() and pdf_path.is_file():
        try:
            pdf_path.unlink()
        except Exception:
            pass
    _r2_delete_key(entry.get("pdf_r2_key", ""))


def _cleanup_context_locked(session_code, ctx, reason=""):
    manager = ctx["manager"]

    if manager.source_r2_key:
        _r2_delete_key(manager.source_r2_key)

    for entry in list(manager.history):
        _delete_entry_files(entry)

    source_path = Path(manager.source_disk_path) if manager.source_disk_path else None
    if source_path and source_path.exists() and source_path.is_file():
        try:
            source_path.unlink()
        except Exception:
            pass

    manager.clear_all()
    ctx["meta"]["zip_downloaded_at"] = None
    _touch_activity_locked(ctx)
    _persist_state_locked(session_code, ctx)


def _build_zip_from_history(history_items):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for entry in history_items:
            pdf_name = f"{entry.get('id', 'archivo')}.pdf"
            pdf_path = Path(entry.get("pdf", "")) if entry.get("pdf") else None
            pdf_r2_key = entry.get("pdf_r2_key", "")

            content = None
            if pdf_path and pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
                try:
                    content = pdf_path.read_bytes()
                except Exception:
                    content = None
            elif pdf_r2_key:
                content = _r2_get_bytes(pdf_r2_key)

            if content:
                zip_file.writestr(pdf_name, content)

    zip_buffer.seek(0)
    return zip_buffer


def _save_outputs(image_bgr, id_value):
    pdf_path = OUTPUT_DIR / f"{id_value}.pdf"

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(image_rgb)
    pdf_buffer = io.BytesIO()
    pil_img.save(pdf_buffer, "PDF")
    pdf_bytes = pdf_buffer.getvalue()
    pdf_path.write_bytes(pdf_bytes)

    return pdf_path, pdf_bytes


def _cleanup_worker():
    interval = max(10, CLEANUP_POLL_SECONDS)
    while True:
        time.sleep(interval)
        try:
            with _state_lock:
                now = time.time()
                for code, ctx in list(_sessions.items()):
                    manager = ctx["manager"]
                    has_session_data = bool(manager.records or manager.history or manager.source_disk_path)
                    if not has_session_data:
                        continue

                    zip_downloaded_at = ctx["meta"].get("zip_downloaded_at")
                    last_activity = float(ctx["meta"].get("last_activity", now))

                    if zip_downloaded_at and now - float(zip_downloaded_at) >= CLEANUP_AFTER_ZIP_SECONDS:
                        _cleanup_context_locked(code, ctx, "after-zip")
                        continue

                    if INACTIVITY_CLEANUP_SECONDS > 0 and now - last_activity >= INACTIVITY_CLEANUP_SECONDS:
                        _cleanup_context_locked(code, ctx, "inactivity")
        except Exception:
            pass


threading.Thread(target=_cleanup_worker, daemon=True, name="session-cleanup-worker").start()
_r2_init()


@app.route("/")
def index():
    startup_message = "Crea o ingresa un codigo de jornada para comenzar."
    return render_template(
        "index.html",
        startup_message=startup_message,
        render_idle_seconds=max(0, RENDER_IDLE_SECONDS),
    )


@app.before_request
def before_request_touch():
    if request.endpoint == "static":
        return

    code = _get_requested_session_code()
    if not code:
        return

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=True)
        _touch_activity_locked(ctx)


@app.route("/session/new", methods=["POST"])
def session_new():
    with _state_lock:
        code = _generate_session_code()
        while code in _sessions:
            code = _generate_session_code()
        ctx = _create_context()
        _sessions[code] = ctx
        _persist_state_locked(code, ctx)

    return jsonify(
        {
            "ok": True,
            "session_code": code,
            "msg": f"Jornada {code} creada.",
            **_status_payload(ctx),
        }
    )


@app.route("/status")
def status():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=True)
        return jsonify({"ok": True, "session_code": code, **_status_payload(ctx)})


@app.route("/load-source", methods=["POST"])
def load_source():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=True)
        manager = ctx["manager"]
        if manager.records or manager.history or manager.source_disk_path:
            _cleanup_context_locked(code, ctx, "new-source")

    if "file" not in request.files:
        return _error("Debes seleccionar un archivo para cargar.", 400, ctx)

    file = request.files["file"]
    filename = secure_filename(file.filename or "")
    if not filename:
        return _error("Nombre de archivo invalido.", 400, ctx)

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return _error("Formato no soportado. Usa xlsx o xls.", 400, ctx)

    destination = DATA_DIR / f"{code}_{filename}"
    file.save(destination)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=True)
        manager = ctx["manager"]
        try:
            manager.load_source(destination)
        except Exception as exc:
            if destination.exists():
                destination.unlink()
            return _error(f"No se pudo leer el archivo: {exc}", 400, ctx)

        if _r2_enabled:
            source_key = _r2_source_key(code, filename)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if _r2_put_bytes(source_key, destination.read_bytes(), content_type=content_type):
                manager.source_r2_key = source_key

        _persist_state_locked(code, ctx)

        return jsonify(
            {
                "ok": True,
                "session_code": code,
                "msg": f"Fuente cargada para jornada {code}: {filename}",
                **_status_payload(ctx),
            }
        )


@app.route("/preview-source", methods=["GET"])
def preview_source():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None or not ctx["manager"].records:
            return _error("Primero carga un archivo de caracterizacion.", 404, ctx)

        manager = ctx["manager"]
        return jsonify(
            {
                "ok": True,
                "session_code": code,
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
                **_status_payload(ctx),
            }
        )


@app.route("/set-selection", methods=["POST"])
def set_selection():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    payload = request.json or {}
    selected_indices = payload.get("selected_indices", [])

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)

        manager = ctx["manager"]
        try:
            manager.set_selection(selected_indices)
        except ValueError as exc:
            return _error(str(exc), 400, ctx)

        _persist_state_locked(code, ctx)
        return jsonify(
            {
                "ok": True,
                "session_code": code,
                "msg": f"Seleccion aplicada: {len(manager.selected_indices)} personas.",
                **_status_payload(ctx),
            }
        )


@app.route("/reset-session", methods=["POST"])
def reset_session():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)

        _cleanup_context_locked(code, ctx, "manual-reset")
        return jsonify(
            {
                "ok": True,
                "session_code": code,
                "msg": "Jornada reiniciada. Se eliminaron Excel y PDFs de la sesion.",
                **_status_payload(ctx),
            }
        )


@app.route("/rescan-person", methods=["POST"])
def rescan_person():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    payload = request.json or {}
    id_value = payload.get("id", "")

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)

        manager = ctx["manager"]
        try:
            result = manager.rescan_from_id(id_value)
        except ValueError as exc:
            return _error(str(exc), 400, ctx)

        for entry in result.get("removed_entries", []):
            _delete_entry_files(entry)

        _persist_state_locked(code, ctx)
        return jsonify(
            {
                "ok": True,
                "session_code": code,
                "msg": f"Reescaneo activado para {id_value}. Continua desde esa persona.",
                **_status_payload(ctx),
            }
        )


@app.route("/auto-corners", methods=["POST"])
def auto_corners():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)

    raw = (request.json or {}).get("img", "")
    if not raw:
        return _error("Sin imagen", 400, ctx)

    try:
        image = decode_data_url(raw)
        doc = detectar_documento(image)
        if doc is None:
            return jsonify({"ok": False, "msg": "No detectado", **_status_payload(ctx)})

        points = ordenar_puntos(doc.reshape(4, 2)).tolist()
        return jsonify({"ok": True, "corners": points, **_status_payload(ctx)})
    except ValueError as exc:
        return _error(str(exc), 400, ctx)
    except Exception:
        return _error("Error detectando esquinas", 500, ctx)


@app.route("/upload", methods=["POST"])
def upload():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)
        manager = ctx["manager"]

    if manager.total() == 0:
        return _error("No hay personas seleccionadas. Sube el Excel y aplica la seleccion.", 400, ctx)

    if not manager.selection_locked:
        return _error("Primero aplica la seleccion de personas antes de capturar.", 400, ctx)

    if manager.counter >= manager.total():
        return _error("Meta alcanzada. No puedes escanear mas.", 400, ctx)

    payload = request.json or {}
    raw = payload.get("img", "")
    if not raw:
        return _error("Sin imagen", 400, ctx)

    try:
        image = decode_data_url(raw)
    except Exception as exc:
        return _error(str(exc), 400, ctx)

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
        return _error("Error procesando imagen", 500, ctx)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        manager = ctx["manager"]
        record = manager.current_record()
        if record is None:
            return _error("No hay mas registros para procesar.", 400, ctx)

        id_value = record["id"]

    try:
        pdf_path, pdf_bytes = _save_outputs(image, id_value)
    except Exception:
        return _error("No se pudo guardar el PDF", 500, ctx)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        manager = ctx["manager"]

        pdf_r2_key = ""
        if _r2_enabled:
            pdf_r2_key = _r2_pdf_key(code, id_value)
            _r2_put_bytes(pdf_r2_key, pdf_bytes, content_type="application/pdf")

        manager.register_scan(id_value, pdf_path, pdf_r2_key=pdf_r2_key)
        _persist_state_locked(code, ctx)
        status_payload = _status_payload(ctx)

    display_name = record.get("full_name", "").strip()
    extra = f" - {display_name}" if display_name else ""
    return jsonify(
        {
            "ok": True,
            "session_code": code,
            "msg": f"Guardado {id_value}.pdf{extra} ({status_payload['current']}/{status_payload['total']})",
            **status_payload,
        }
    )


@app.route("/undo-last", methods=["POST"])
def undo_last():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)

        manager = ctx["manager"]
        last = manager.undo_last()
        if last is None:
            return _error("No hay escaneos para deshacer.", 400, ctx)

        _delete_entry_files(last)
        _persist_state_locked(code, ctx)

        return jsonify(
            {
                "ok": True,
                "session_code": code,
                "msg": f"Se deshizo {last['id']}.pdf",
                **_status_payload(ctx),
            }
        )


@app.route("/download-zip", methods=["GET"])
def download_zip():
    code = _get_requested_session_code()
    if not code:
        return _error("Debes ingresar un codigo de jornada.", 400)

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return _error("Jornada no encontrada.", 404)

        history_items = list(ctx["manager"].history)
        if not history_items:
            return _error("No hay PDFs para comprimir.", 404, ctx)

        zip_buffer = _build_zip_from_history(history_items)
        ctx["meta"]["zip_downloaded_at"] = time.time()
        _persist_state_locked(code, ctx)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_name = f"scans_{code}_{timestamp}.zip"

    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/zip",
    )


@app.route("/pdf/<string:id_value>", methods=["GET"])
def view_pdf(id_value):
    code = _get_requested_session_code()
    if not code:
        return "Debes ingresar codigo de jornada", 400

    with _state_lock:
        ctx = _get_context_locked(code, create_if_missing=False)
        if ctx is None:
            return "Jornada no encontrada", 404

        manager = ctx["manager"]
        safe_id = Path(id_value).name.replace(".pdf", "")
        entry = next((item for item in manager.history if str(item.get("id", "")) == safe_id), None)
        if entry is None:
            return "PDF no encontrado", 404

        pdf_path = Path(entry.get("pdf", "")) if entry.get("pdf") else None
        if pdf_path and pdf_path.exists():
            response = send_file(pdf_path, mimetype="application/pdf")
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

        content = _r2_get_bytes(entry.get("pdf_r2_key", ""))
        if not content:
            return "PDF no encontrado", 404

        response = send_file(io.BytesIO(content), mimetype="application/pdf", download_name=f"{safe_id}.pdf")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
