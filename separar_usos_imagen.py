"""
separar_usos_imagen.py
──────────────────────
Divide un PDF escaneado de usos de imagen (1 página por formulario)
en archivos individuales renombrados con el número de identificación
de cada estudiante, tomados de un CSV en el mismo orden.

USO:
   python  separar_usos_imagen.py --pdf usos_imagen.pdf --csv lista.csv

REQUISITOS:
    pip install pypdf

FORMATO DEL CSV:
    Una sola columna, sin encabezado, un ID por fila.
    Ejemplo de lista.csv:
        1234567890
        9876543210
        1122334455
        ...
"""

import argparse
import sys
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("❌ Falta la librería pypdf. Instálala con:  pip install pypdf")
    sys.exit(1)


def leer_ids_csv(ruta_csv: str) -> list[str]:
    """Lee los IDs del CSV, una columna sin encabezado."""
    ids = []
    with open(ruta_csv, newline="", encoding="utf-8-sig") as f:
        for linea in f:
            id_estudiante = linea.strip()
            if id_estudiante:  # ignorar líneas vacías
                ids.append(id_estudiante)
    return ids


def separar_pdf(ruta_pdf: str, ruta_csv: str, carpeta_salida: str):
    ruta_pdf = Path(ruta_pdf)
    ruta_csv = Path(ruta_csv)
    carpeta = Path(carpeta_salida)

    # Validaciones de entrada
    if not ruta_pdf.exists():
        print(f"❌ No se encontró el PDF: {ruta_pdf}")
        sys.exit(1)
    if not ruta_csv.exists():
        print(f"❌ No se encontró el CSV: {ruta_csv}")
        sys.exit(1)

    # Leer IDs
    ids = leer_ids_csv(str(ruta_csv))
    print(f"✅ CSV leído: {len(ids)} IDs encontrados.")

    # Leer PDF
    reader = PdfReader(str(ruta_pdf))
    total_paginas = len(reader.pages)
    print(f"✅ PDF leído: {total_paginas} páginas.")

    # Verificar que coincidan
    if total_paginas != len(ids):
        print(f"\n⚠️  ADVERTENCIA: el PDF tiene {total_paginas} páginas "
              f"pero el CSV tiene {len(ids)} IDs.")
        print("   Procesaré hasta el mínimo de los dos.")
        total = min(total_paginas, len(ids))
    else:
        total = total_paginas

    # Crear carpeta de salida
    carpeta.mkdir(parents=True, exist_ok=True)
    print(f"📁 Archivos de salida en: {carpeta.resolve()}\n")

    # Separar y guardar
    exitosos = 0
    errores = []

    for i in range(total):
        id_estudiante = ids[i]
        nombre_archivo = carpeta / f"{id_estudiante}.pdf"

        # Si ya existe un archivo con ese nombre, agregar sufijo para no sobreescribir
        if nombre_archivo.exists():
            nombre_archivo = carpeta / f"{id_estudiante}_pag{i+1}.pdf"

        try:
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            with open(nombre_archivo, "wb") as f_out:
                writer.write(f_out)
            exitosos += 1
            print(f"  [{i+1:>3}/{total}] ✓ {nombre_archivo.name}")
        except Exception as e:
            errores.append((i + 1, id_estudiante, str(e)))
            print(f"  [{i+1:>3}/{total}] ✗ Error en ID {id_estudiante}: {e}")

    # Resumen final
    print(f"\n{'─'*50}")
    print(f"✅ Completado: {exitosos}/{total} archivos generados.")
    if errores:
        print(f"❌ Errores ({len(errores)}):")
        for pagina, id_est, msg in errores:
            print(f"   Página {pagina} / ID {id_est}: {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="Divide un PDF escaneado en archivos individuales renombrados por ID."
    )
    parser.add_argument(
        "--pdf", required=True,
        help="Ruta al PDF escaneado con todos los formularios (ej: usos_imagen.pdf)"
    )
    parser.add_argument(
        "--csv", required=True,
        help="Ruta al CSV con la lista de IDs en orden (ej: lista.csv)"
    )
    parser.add_argument(
        "--salida", default="usos_separados",
        help="Carpeta donde se guardarán los PDFs individuales (default: usos_separados/)"
    )
    args = parser.parse_args()

    separar_pdf(args.pdf, args.csv, args.salida)


if __name__ == "__main__":
    main()
