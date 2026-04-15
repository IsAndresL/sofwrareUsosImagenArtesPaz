from pathlib import Path
import unicodedata
import copy

import pandas as pd


class ScanDataManager:
    def __init__(self):
        self.source_path = ""
        self.source_disk_path = ""
        self.source_r2_key = ""
        self.records = []
        self.selected_indices = []
        self.selection_locked = False
        self.counter = 0
        self.target = 0
        self.history = []
        self.scanned_ids = []

    def _normalize_column(self, value):
        text = str(value).strip().upper()
        text = text.replace("_", " ")
        text = text.replace("-", " ")
        text = text.replace(".", " ")
        text = text.replace(",", " ")
        text = text.replace(";", " ")
        text = "".join(
            ch for ch in unicodedata.normalize("NFD", text)
            if unicodedata.category(ch) != "Mn"
        )
        text = " ".join(text.split())
        return text

    def _safe_series(self, df, lookup, key):
        real_col = lookup.get(key)
        if real_col is None:
            return pd.Series([""] * len(df))
        return df[real_col].fillna("").astype(str).str.strip()

    def _from_excel(self, path):
        try:
            df = pd.read_excel(path, dtype=str).fillna("")
        except ImportError as exc:
            raise ValueError(
                "Falta openpyxl para leer Excel. Instala con: pip install openpyxl"
            ) from exc

        lookup = {self._normalize_column(col): col for col in df.columns}

        if "NUMERO DE IDENTIFICACION" not in lookup:
            raise ValueError(
                "El Excel debe incluir la columna 'NUMERO DE IDENTIFICACION'."
            )

        ids = self._safe_series(df, lookup, "NUMERO DE IDENTIFICACION")
        p_apellido = self._safe_series(df, lookup, "PRIMER APELLIDO")
        s_apellido = self._safe_series(df, lookup, "SEGUNDO APELLIDO")
        p_nombre = self._safe_series(df, lookup, "PRIMER NOMBRE")
        s_nombre = self._safe_series(df, lookup, "SEGUNDO NOMBRE")

        records = []
        for idx in range(len(df)):
            id_value = ids.iloc[idx].strip()
            if not id_value:
                continue

            first_name = p_nombre.iloc[idx].strip()
            second_name = s_nombre.iloc[idx].strip()
            first_last_name = p_apellido.iloc[idx].strip()
            second_last_name = s_apellido.iloc[idx].strip()
            full_name = " ".join(
                part for part in [first_name, second_name, first_last_name, second_last_name]
                if part
            ).strip()

            records.append(
                {
                    "id": id_value,
                    "first_name": first_name,
                    "second_name": second_name,
                    "first_last_name": first_last_name,
                    "second_last_name": second_last_name,
                    "full_name": full_name,
                }
            )

        return records

    def load_source(self, source_file):
        path = Path(source_file)
        if not path.exists():
            raise FileNotFoundError(f"No se encontro el archivo: {path.name}")

        suffix = path.suffix.lower()
        if suffix in [".xlsx", ".xls"]:
            records = self._from_excel(path)
        else:
            raise ValueError("Formato no soportado. Usa xlsx o xls.")

        if not records:
            raise ValueError("El archivo no contiene filas validas con identificacion.")

        self.records = records
        self.source_path = path.name
        self.source_disk_path = str(path)
        self.source_r2_key = ""
        self.selected_indices = list(range(len(records)))
        self.selection_locked = False
        self.counter = 0
        self.history = []
        self.scanned_ids = []
        self.target = len(records)

    def set_selection(self, selected_indices):
        if not self.records:
            raise ValueError("Primero carga un archivo con datos.")
        if self.selection_locked:
            raise ValueError("La seleccion ya fue aplicada. Carga otra caracterizacion para cambiarla.")

        cleaned = []
        seen = set()
        for value in selected_indices:
            try:
                index = int(value)
            except Exception:
                continue
            if 0 <= index < len(self.records) and index not in seen:
                cleaned.append(index)
                seen.add(index)

        if not cleaned:
            raise ValueError("Debes seleccionar al menos una persona.")

        self.selected_indices = cleaned
        self.counter = 0
        self.history = []
        self.scanned_ids = []
        self.target = len(cleaned)
        self.selection_locked = True

    def total(self):
        if not self.records or not self.selected_indices:
            return 0
        return min(self.target, len(self.selected_indices))

    def selected_records(self):
        return [self.records[index] for index in self.selected_indices]

    def current_record(self):
        if self.counter >= self.total():
            return None
        return self.selected_records()[self.counter]

    def status(self):
        record = self.current_record() or {}
        return {
            "current": self.counter,
            "total": self.total(),
            "source_total": len(self.records),
            "target": self.target,
            "selected_total": len(self.selected_indices),
            "selected_indices": self.selected_indices,
            "selection_locked": self.selection_locked,
            "scanned_ids": self.scanned_ids,
            "next_id": record.get("id", ""),
            "next_name": record.get("full_name", ""),
            "next_first_name": record.get("first_name", ""),
            "next_second_name": record.get("second_name", ""),
            "next_first_last_name": record.get("first_last_name", ""),
            "next_second_last_name": record.get("second_last_name", ""),
            "source_file": self.source_path,
            "has_records": len(self.records) > 0,
        }

    def set_target(self, target):
        if not self.records:
            raise ValueError("Primero carga un archivo con datos.")
        if target < 1:
            raise ValueError("La meta debe ser mayor que 0.")
        if target > len(self.selected_indices):
            raise ValueError(
                f"La meta no puede superar las personas seleccionadas ({len(self.selected_indices)})."
            )
        if target < self.counter:
            raise ValueError(
                f"Ya llevas {self.counter} escaneos, no puedes bajar la meta por debajo de eso."
            )
        self.target = target

    def register_scan(self, id_value, pdf_path, pdf_r2_key=""):
        self.history.append(
            {
                "id": id_value,
                "pdf": str(pdf_path),
                "pdf_r2_key": str(pdf_r2_key or ""),
            }
        )
        if id_value not in self.scanned_ids:
            self.scanned_ids.append(id_value)
        self.counter += 1

    def undo_last(self):
        if self.counter == 0 or not self.history:
            return None

        entry = self.history.pop()
        if entry["id"] in self.scanned_ids:
            self.scanned_ids.remove(entry["id"])
        self.counter = max(0, self.counter - 1)
        return entry

    def reset_session(self):
        self.counter = 0
        self.history = []
        self.scanned_ids = []

    def clear_all(self):
        self.source_path = ""
        self.source_disk_path = ""
        self.source_r2_key = ""
        self.records = []
        self.selected_indices = []
        self.selection_locked = False
        self.counter = 0
        self.target = 0
        self.history = []
        self.scanned_ids = []

    def export_state(self):
        return {
            "source_path": self.source_path,
            "source_disk_path": self.source_disk_path,
            "source_r2_key": self.source_r2_key,
            "records": copy.deepcopy(self.records),
            "selected_indices": list(self.selected_indices),
            "selection_locked": bool(self.selection_locked),
            "counter": int(self.counter),
            "target": int(self.target),
            "history": copy.deepcopy(self.history),
            "scanned_ids": list(self.scanned_ids),
        }

    def import_state(self, state):
        payload = state or {}
        self.source_path = str(payload.get("source_path", "") or "")
        self.source_disk_path = str(payload.get("source_disk_path", "") or "")
        self.source_r2_key = str(payload.get("source_r2_key", "") or "")
        self.records = list(payload.get("records", []) or [])
        self.selected_indices = [int(i) for i in payload.get("selected_indices", []) if isinstance(i, (int, str)) and str(i).isdigit()]
        self.selection_locked = bool(payload.get("selection_locked", False))
        self.counter = int(payload.get("counter", 0) or 0)
        self.target = int(payload.get("target", 0) or 0)
        self.history = list(payload.get("history", []) or [])
        self.scanned_ids = list(payload.get("scanned_ids", []) or [])
