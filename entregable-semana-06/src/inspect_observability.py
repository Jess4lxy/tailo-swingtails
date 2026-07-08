"""Inspector de la bitacora de observabilidad (entregable semana 05).

Muestra las ultimas interacciones registradas con sus metricas de rendimiento
(TTFT, latencia total, tokens/segundo), el estado del guardrail y las
herramientas ejecutadas. Sirve para la "Bitacora de Observabilidad en Accion"
del documento tecnico (capturas de registros reales).

Uso:
    python src/inspect_observability.py            # ultimas 20 interacciones
    python src/inspect_observability.py --n 50     # ultimas 50
    python src/inspect_observability.py --stats    # agregados (promedios, %bloqueo)
    python src/inspect_observability.py --session <conversation_id>
"""
from __future__ import annotations

import argparse
import json

import observability


def _fmt(v, suffix: str = "") -> str:
    return f"{v}{suffix}" if v is not None else "—"


def _print_row(row: dict) -> None:
    blocked = "[BLOQUEADO]" if row["was_blocked"] else "[OK]"
    print(f"\n#{row['id']}  [{row['timestamp']}]  {blocked}")
    print(f"  session : {row['session_id']}")
    print(f"  usuario : {row['user_prompt'][:100]}")
    print(f"  agente  : {row['system_response'][:100]}")
    print(
        f"  metricas: TTFT={_fmt(row['ttft_ms'],' ms')} | "
        f"latencia={_fmt(row['total_latency_ms'],' ms')} | "
        f"tps={_fmt(row['tokens_per_second'])}"
    )
    try:
        tools = json.loads(row["tools_executed"])
    except (json.JSONDecodeError, TypeError):
        tools = []
    if tools:
        resumen = ", ".join(f"{t.get('name')}[{t.get('status')}]" for t in tools)
        print(f"  tools   : {resumen}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20, help="Cuantas filas mostrar.")
    parser.add_argument("--session", default=None, help="Filtra por conversation_id.")
    parser.add_argument("--stats", action="store_true", help="Muestra agregados.")
    args = parser.parse_args()

    observability.init_db()

    if args.stats:
        s = observability.stats()
        total = s.get("total") or 0
        blocked = s.get("blocked") or 0
        print("=== Estadisticas de observabilidad ===")
        print(f"  interacciones registradas : {total}")
        print(f"  bloqueadas por guardrail   : {blocked}"
              f" ({(100*blocked/total):.1f}%)" if total else "  bloqueadas: 0")
        print(f"  TTFT promedio              : {_fmt(round(s['avg_ttft_ms'],2) if s.get('avg_ttft_ms') else None,' ms')}")
        print(f"  latencia total promedio    : {_fmt(round(s['avg_latency_ms'],2) if s.get('avg_latency_ms') else None,' ms')}")
        print(f"  tokens/segundo promedio    : {_fmt(round(s['avg_tps'],2) if s.get('avg_tps') else None)}")
        return

    rows = observability.recent_logs(limit=args.n, session_id=args.session)
    if not rows:
        print("[no hay registros de observabilidad todavia]")
        return
    print(f"=== Ultimas {len(rows)} interacciones (mas reciente primero) ===")
    for row in rows:
        _print_row(row)


if __name__ == "__main__":
    main()
