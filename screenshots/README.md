# Capturas del dashboard (LangSmith)

Evidencia de observabilidad de la corrida de 5 peticiones concurrentes (`python scripts/load_test.py`, 2026-11-16).

| Archivo | Qué muestra |
|---|---|
| `01-traces-list.png` | Las 10 trazas `agentops-job` del proyecto `agentops-api` (arranque + reanudación de los 5 jobs) con su latencia |
| `02-trace-tree.png` | Árbol de una traza `action:resume`: span `llm_fallback` del supervisor con los modelos candidatos, llamadas a Gemini con tokens, y el analyst |
| `03-cost-per-run.png` | Costo por ejecución: vista Threads (1 thread = 1 job), columna *Cost* |
| `04-latency-p95.png` | Chart custom con la latencia p95 de las trazas de la corrida (100,5 s) |
| `load_test_results.json` | Salida de `load_test.py` (run_ids, latencias, p50/p95 por job) |
| `langsmith_report.txt` | Salida de `langsmith_report.py`: costo por job, p95, tokens y latencia por nodo |
