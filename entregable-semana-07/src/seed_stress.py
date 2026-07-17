"""Seeder de datos de ESTRES a escala de produccion (semana 07 - Fase A punto 3).

Puebla la BD relacional local (config.STRESS_DB, tabla `citas`) con >=50,000
citas ficticias pero coherentes, usando BULK INSERTS dentro de transacciones
(no un INSERT por fila): asi los miles de registros se confirman en disco en
pocas operaciones de escritura en lugar de miles de sincronizaciones (disk
sync), bajando el sembrado de minutos a segundos.

Ademas DEMUESTRA, con numeros, los conceptos de la rubrica (Fase teorica D):
  - contraste bulk vs INSERT-por-fila (disk sync),
  - impacto de un indice B-Tree: latencia de una consulta por fecha ANTES
    (Full Table Scan, O(N)) y DESPUES de crear el indice (O(log N)),
  - EXPLAIN QUERY PLAN como evidencia de uso del indice,
  - SELECT COUNT(*) FROM citas como evidencia del conteo total.

Uso:
    python src/seed_stress.py                 # 50,000 (o TAILO_STRESS_SEED_TARGET)
    python src/seed_stress.py --target 10000  # nivel Competente
    python src/seed_stress.py --reset         # borra y vuelve a sembrar
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import time
from datetime import date, timedelta

import stress_db
from config import EVAL_USER_ID, STRESS_DB, STRESS_SEED_TARGET

# Semilla fija -> datos reproducibles entre corridas (para la evidencia).
random.seed(2026)

# --- Catalogos ficticios pero realistas -------------------------------------
PET_NAMES = [
    "Firulais", "Luna", "Max", "Bella", "Rocky", "Nala", "Toby", "Kira", "Simba",
    "Coco", "Bruno", "Maya", "Zeus", "Lola", "Thor", "Canela", "Duke", "Chispa",
    "Manchas", "Pelusa", "Rex", "Frida", "Odin", "Sasha", "Milo", "Nube", "Bobby",
    "Micha", "Tommy", "Estrella", "Chocolate", "Pancho", "Michi", "Draco", "Copito",
]
SPECIES = (["Perro"] * 6) + (["Gato"] * 4) + ["Conejo", "Huron", "Iguana", "Ave", "Tortuga"]
CLINICS = [
    "Toy Inc Veterinaria", "PetSalud Centro", "VidaAnimal Norte", "Huellitas 24h",
    "Clinica San Roque", "VetExpress Sur", "AnimalCare Plaza", "Patitas Felices",
    "Hospital Veterinario Merida", "MascotaSana", "Zoonosis Centro", "Colmillos y Bigotes",
]
SERVICES = [
    "Consulta General", "Vacunacion", "Desparasitacion", "Estetica y Baño",
    "Cirugia Menor", "Analisis de Laboratorio", "Radiografia", "Control de Peso",
]
STATUSES = (["Pendiente"] * 5) + (["Confirmada"] * 3) + (["Completada"] * 3) + (["Cancelada"] * 1)
HOURS = [f"{h:02d}:{m:02d}:00" for h in range(8, 19) for m in (0, 30)]
NOTES = [
    None, None, None, "Traer cartilla de vacunacion", "Ayuno de 8 horas",
    "Revisar oido derecho", "Seguimiento post operatorio", "Primera visita",
]

_DATE_START = date(2026, 1, 1)
_DATE_SPAN = 365  # citas repartidas en todo 2026


def _rand_date() -> str:
    return (_DATE_START + timedelta(days=random.randint(0, _DATE_SPAN))).isoformat()


def _row(idx: int, user_id: int) -> tuple:
    """Genera una fila de cita coherente. `idx` alimenta el folio secuencial."""
    return (
        f"SW-{idx:07d}",
        user_id,
        random.choice(PET_NAMES),
        random.choice(SPECIES),
        random.choice(CLINICS),
        random.choice(SERVICES),
        _rand_date(),
        random.choice(HOURS),
        random.choice(STATUSES),
        random.choice(NOTES),
    )


# Filas "ancla" DETERMINISTAS para el usuario de evaluacion: garantizan que la
# bateria (que consulta como EVAL_USER_ID) obtenga resultados conocidos.
_ANCHOR_DATE = "2026-08-15"


def _anchor_rows(start_idx: int) -> list[tuple]:
    rows = []
    for i in range(30):  # 30 citas del usuario de prueba en una fecha conocida
        rows.append((
            f"SW-{start_idx + i:07d}", EVAL_USER_ID,
            PET_NAMES[i % len(PET_NAMES)], "Perro",
            CLINICS[i % len(CLINICS)], SERVICES[i % len(SERVICES)],
            _ANCHOR_DATE, HOURS[i % len(HOURS)],
            "Pendiente" if i % 2 == 0 else "Confirmada",
            "Cita de control",
        ))
    return rows


_INSERT_SQL = (
    "INSERT INTO citas (folio, user_id, pet_name, specie, clinic_name, "
    "service_name, appointment_date, hour, status, notes, created_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))"
)


def _bulk_seed(conn: sqlite3.Connection, target: int, batch: int = 5000) -> float:
    """Inserta `target` citas con executemany por lotes en una transaccion.

    Devuelve los segundos que tomo. Los folios van 1..target; las primeras 30
    filas son las "ancla" del usuario de evaluacion, el resto aleatorias con
    user_id repartido en 1..500."""
    t0 = time.perf_counter()
    anchors = _anchor_rows(1)
    conn.executemany(_INSERT_SQL, anchors)

    remaining = target - len(anchors)
    idx = len(anchors) + 1
    pending: list[tuple] = []
    for _ in range(max(remaining, 0)):
        pending.append(_row(idx, random.randint(1, 500)))
        idx += 1
        if len(pending) >= batch:
            conn.executemany(_INSERT_SQL, pending)
            pending.clear()
    if pending:
        conn.executemany(_INSERT_SQL, pending)
    conn.commit()
    return time.perf_counter() - t0


def _naive_benchmark(conn: sqlite3.Connection, n: int = 500) -> dict:
    """Micro-benchmark del ANTIPATRON: INSERT por fila con commit individual.

    Mide el costo del disk sync por fila (miles de escrituras fisicas). Usa una
    tabla temporal para no contaminar `citas`, y extrapola al target."""
    conn.execute("CREATE TABLE IF NOT EXISTS _naive AS SELECT * FROM citas WHERE 0")
    ins = (
        "INSERT INTO _naive (folio, user_id, pet_name, specie, clinic_name, "
        "service_name, appointment_date, hour, status, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))"
    )
    # El antipatron real usa escritura DURABLE: cada commit fuerza un fsync a
    # disco. Activamos synchronous=FULL solo para este micro-benchmark, para
    # medir honestamente el costo del disk sync por fila (con synchronous=OFF
    # el commit no sincroniza y el contraste seria enganoso).
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA journal_mode = DELETE")
    t0 = time.perf_counter()
    for i in range(n):
        conn.execute(ins, _row(i + 1, random.randint(1, 500)))
        conn.commit()  # <- fuerza un disk sync por fila (el antipatron)
    elapsed = time.perf_counter() - t0
    # Restaura el modo rapido para el resto del sembrado/consultas.
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("DROP TABLE _naive")
    conn.commit()
    return {"rows": n, "seconds": round(elapsed, 3), "per_row_ms": round(elapsed * 1000 / n, 3)}


def _index_impact(conn: sqlite3.Connection, fecha: str) -> dict:
    """Latencia de una consulta por fecha ANTES y DESPUES del indice B-Tree."""
    sql = "SELECT COUNT(*) FROM citas WHERE appointment_date = ?"

    def _time_query() -> float:
        t0 = time.perf_counter()
        conn.execute(sql, (fecha,)).fetchone()
        return (time.perf_counter() - t0) * 1000

    before_ms = min(_time_query() for _ in range(3))  # sin indice: full scan
    plan_before = [r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, (fecha,))]

    stress_db.create_indexes(conn)
    conn.commit()

    after_ms = min(_time_query() for _ in range(3))    # con indice: seek
    plan_after = [r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, (fecha,))]

    return {
        "before_ms": round(before_ms, 3), "after_ms": round(after_ms, 3),
        "speedup": round(before_ms / after_ms, 1) if after_ms else None,
        "plan_before": plan_before, "plan_after": plan_after,
    }


def seed(target: int = STRESS_SEED_TARGET, reset: bool = False, benchmark: bool = True) -> dict:
    if reset and STRESS_DB.exists():
        STRESS_DB.unlink()

    with stress_db.connect() as conn:
        # Optimizaciones de escritura: reducen los disk sync durante el bulk.
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA temp_store = MEMORY")
        stress_db.init_schema(conn)

        existing = conn.execute("SELECT COUNT(*) AS n FROM citas").fetchone()["n"]
        if existing and not reset:
            print(f"[seed] La BD ya tiene {existing:,} citas. Usa --reset para regenerar.")
            report = _final_report(conn, bulk_seconds=None)
            _print_report(report)
            return report

        print(f"[seed] Sembrando {target:,} citas con bulk inserts / transacciones…")
        bulk_seconds = _bulk_seed(conn, target)

        naive = _naive_benchmark(conn) if benchmark else None
        impact = _index_impact(conn, _ANCHOR_DATE)

        report = _final_report(conn, bulk_seconds=bulk_seconds, naive=naive, impact=impact)

    _print_report(report)
    _write_evidence(report)
    return report


def _final_report(conn, bulk_seconds, naive=None, impact=None) -> dict:
    total = conn.execute("SELECT COUNT(*) AS n FROM citas").fetchone()["n"]
    eval_user = conn.execute(
        "SELECT COUNT(*) AS n FROM citas WHERE user_id = ?", (EVAL_USER_ID,)
    ).fetchone()["n"]
    por_estado = {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM citas GROUP BY status")
    }
    return {
        "db_path": str(STRESS_DB),
        "total_citas": total,
        "citas_usuario_eval": eval_user,
        "eval_user_id": EVAL_USER_ID,
        "por_estado": por_estado,
        "bulk_seconds": round(bulk_seconds, 3) if bulk_seconds else None,
        "bulk_rows_per_sec": round(total / bulk_seconds) if bulk_seconds else None,
        "naive_benchmark": naive,
        "index_impact": impact,
        "nivel": "Excelente (>=50k)" if total >= 50000 else
                 ("Competente (>=10k)" if total >= 10000 else "Insuficiente (<10k)"),
    }


def _print_report(r: dict) -> None:
    print("\n" + "=" * 68)
    print(" EVIDENCIA DE SEMBRADO DE DATOS (semana 07)")
    print("=" * 68)
    print(f" BD:                 {r['db_path']}")
    print(f" SELECT COUNT(*):    {r['total_citas']:,} citas   -> nivel {r['nivel']}")
    print(f" Citas del user {r['eval_user_id']:<4} {r['citas_usuario_eval']:,} (para la bateria evaluadora)")
    print(f" Por estado:         {r['por_estado']}")
    if r.get("bulk_seconds") is not None:
        print(f" Bulk insert:        {r['total_citas']:,} filas en {r['bulk_seconds']}s "
              f"(~{r['bulk_rows_per_sec']:,} filas/s)")
    if r.get("naive_benchmark"):
        nb = r["naive_benchmark"]
        extrapol = nb["per_row_ms"] * r["total_citas"] / 1000
        print(f" INSERT por fila:    {nb['per_row_ms']} ms/fila (commit individual) "
              f"-> ~{extrapol:,.0f}s extrapolado a {r['total_citas']:,} filas")
    if r.get("index_impact"):
        ii = r["index_impact"]
        print(f" Indice B-Tree:      consulta por fecha {ii['before_ms']}ms (scan) -> "
              f"{ii['after_ms']}ms (indice)  ~{ii['speedup']}x mas rapido")
        print(f"   EXPLAIN antes:    {ii['plan_before']}")
        print(f"   EXPLAIN despues:  {ii['plan_after']}")
    print("=" * 68 + "\n")


def _write_evidence(r: dict) -> None:
    """Escribe la evidencia en texto plano para adjuntar al PDF del entregable."""
    import json

    out = STRESS_DB.parent / "seed_report.txt"
    out.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[seed] Evidencia escrita en {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seeder de estres para Tailo (semana 07).")
    parser.add_argument("--target", type=int, default=STRESS_SEED_TARGET)
    parser.add_argument("--reset", action="store_true", help="Borra y vuelve a sembrar.")
    parser.add_argument("--no-benchmark", action="store_true", help="Omite el micro-benchmark naive.")
    args = parser.parse_args()
    seed(target=args.target, reset=args.reset, benchmark=not args.no_benchmark)
