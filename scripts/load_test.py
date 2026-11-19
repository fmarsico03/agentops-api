"""Prueba de carga: N peticiones concurrentes contra la API (default 5).

1. Lanza N `POST /tasks` en paralelo y mide cuánto tarda la API en devolver el job_id (debe ser ~ms).
2. Hace polling de cada job. Si queda en AWAITING_APPROVAL, lo aprueba (o rechaza) vía `POST /approve`,
   simulando al revisor humano (--approve / --reject / --manual).
3. Imprime una tabla por job y las latencias p50/p95 end-to-end y de procesamiento (sin la espera humana).

El costo por ejecución y la latencia p95 "oficiales" se leen en el dashboard de LangSmith (ver README);
este script sirve para disparar la corrida y para contrastar los números.

    python scripts/load_test.py                 # 5 jobs, auto-aprueba la búsqueda paga
    python scripts/load_test.py -n 5 --reject   # rechaza la búsqueda paga (solo corpus local)
    python scripts/load_test.py --manual        # no aprueba: los deja esperando para hacerlo a mano
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

QUERIES = [
    "¿Cuál es la tasa de política monetaria actual del Banco Central de Chile? Calculá cuánto rinde "
    "1.000.000 CLP a esa tasa en un año con capitalización mensual.",
    "Buscá la inflación mensual de Argentina (INDEC) de los últimos 3 meses publicados, calculá el promedio y "
    "la inflación acumulada del período.",
    "¿Cuál es la población actual estimada de Uruguay y de Paraguay? Calculá la diferencia y el ratio entre ambas.",
    "Buscá el precio actual del cobre por libra y analizá el sentimiento de los titulares recientes sobre el cobre.",
    "¿Cuál fue el crecimiento del PIB de Brasil en el último trimestre publicado? Analizá el sentimiento de las "
    "noticias recientes sobre la economía brasileña.",
]
TERMINAL = {"DONE", "FAILED"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def pct(values: list[float], p: float) -> float:
    """Percentil con interpolación lineal (mismo criterio que numpy.percentile por defecto)."""
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


async def run_job(client: httpx.AsyncClient, idx: int, query: str, args) -> dict:
    t0 = time.perf_counter()
    r = await client.post("/tasks", json={"query": query, "max_steps": args.max_steps})
    r.raise_for_status()
    enqueue_ms = (time.perf_counter() - t0) * 1000
    job_id = r.json()["job_id"]
    print(f"[{idx}] encolado {job_id} en {enqueue_ms:.0f} ms")

    approved_at = None
    while True:
        job = (await client.get(f"/tasks/{job_id}")).json()
        status = job["status"]
        if status in TERMINAL:
            break
        if status == "AWAITING_APPROVAL" and approved_at is None and not args.manual:
            req = job.get("approval_request", {})
            decision = {"approved": not args.reject, "reviewer": "load-test",
                        "comment": f"auto ({req.get('estimated_credits')} créditos estimados)"}
            await client.post(f"/tasks/{job_id}/approve", json=decision)
            approved_at = time.perf_counter()
            print(f"[{idx}] {'aprobado' if decision['approved'] else 'rechazado'} (HITL)")
        if status == "AWAITING_APPROVAL" and args.manual:
            print(f"[{idx}] esperando aprobación manual: POST /tasks/{job_id}/approve")
            await asyncio.sleep(10)
        await asyncio.sleep(args.poll)

    total = time.perf_counter() - t0
    print(f"[{idx}] {status} en {total:.1f}s")
    return {
        "idx": idx,
        "job_id": job_id,
        "status": status,
        "enqueue_ms": enqueue_ms,
        "total_s": total,
        "processing_s": job.get("processing_seconds"),
        "approval": job.get("search_approval"),
        "steps": len(job.get("decisions") or []),
        "error": job.get("error"),
        "run_ids": (job.get("trace") or {}).get("run_ids", []),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("-n", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--poll", type=float, default=2.0)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reject", action="store_true", help="Rechazar la búsqueda paga en el HITL")
    group.add_argument("--manual", action="store_true", help="No responder el HITL (aprobación a mano)")
    parser.add_argument("--out", default="screenshots/load_test_results.json")
    args = parser.parse_args()

    queries = [QUERIES[i % len(QUERIES)] for i in range(args.n)]
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        (await client.get("/health")).raise_for_status()
        t0 = time.perf_counter()
        results = await asyncio.gather(*(run_job(client, i + 1, q, args) for i, q in enumerate(queries)))
        wall = time.perf_counter() - t0

    print("\n" + "=" * 100)
    print(f"{'#':<3}{'job_id':<34}{'estado':<8}{'encolar':>9}{'total':>9}{'proceso':>9}  {'HITL':<10}{'pasos':>5}")
    for r in results:
        proc = f"{r['processing_s']:.1f}s" if r["processing_s"] else "-"
        print(f"{r['idx']:<3}{r['job_id']:<34}{r['status']:<8}{r['enqueue_ms']:>7.0f}ms{r['total_s']:>8.1f}s"
              f"{proc:>9}  {str(r['approval']):<10}{r['steps']:>5}")
        if r["error"]:
            print(f"   error: {r['error']}")

    ok = [r for r in results if r["status"] == "DONE"]
    totals = [r["total_s"] for r in ok]
    procs = [r["processing_s"] for r in ok if r["processing_s"]]
    enq = [r["enqueue_ms"] for r in results]
    summary = {
        "jobs": len(results),
        "done": len(ok),
        "failed": len(results) - len(ok),
        "wall_clock_s": round(wall, 2),
        "enqueue_ms_p95": round(pct(enq, 95), 1),
        "e2e_s_p50": round(pct(totals, 50), 2),
        "e2e_s_p95": round(pct(totals, 95), 2),
        "processing_s_p50": round(pct(procs, 50), 2),
        "processing_s_p95": round(pct(procs, 95), 2),
    }
    print("-" * 100)
    for k, v in summary.items():
        print(f"{k:>18}: {v}")
    print("\nLa suma de tiempos individuales vs. el wall-clock muestra que los jobs corrieron en paralelo:")
    print(f"   suma={sum(totals):.1f}s  wall-clock={wall:.1f}s")

    out = Path(args.out)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False), "utf-8")
    print(f"\nResultados guardados en {out}")


if __name__ == "__main__":
    asyncio.run(main())
