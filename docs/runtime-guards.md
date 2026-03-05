# Runtime Guards: `max_depth` y `global_timeout_seconds`

Mecanismos de seguridad del runtime que garantizan que toda ejecución alcanza un estado terminal (SPEC §1.3).

---

## `max_depth`

Contador de pasos ejecutados. Se incrementa en 1 cada vez que el runtime avanza a un nodo nuevo.

**Propósito:** prevenir loops infinitos. Ejemplo: un evaluator loop donde el score nunca alcanza el umbral requerido volvería a ejecutar indefinidamente sin este guard.

**Comportamiento:** cuando `execution_state.depth >= definition.max_depth`, la ejecución termina en `FAILED` con el mensaje `"max_depth (N) exceeded"`.

**Implementación:** `agentflow/core/runtime.py:108`

```python
if execution_state.depth >= definition.max_depth:
    reason = f"max_depth ({definition.max_depth}) exceeded"
    # → ExecutionStatus.FAILED
```

**Valor por defecto:** 50 (definido en `GraphDefinition`).

**Ejemplo en YAML:**
```yaml
max_depth: 10
```

---

## `global_timeout_seconds`

Tiempo de reloj real desde el inicio de la ejecución. Se mide con `time.monotonic()`.

**Propósito:** proteger contra nodos lentos — LLM que no responde, tool externa colgada, red lenta. El timeout es de la ejecución completa, no por nodo.

**Comportamiento:** cuando el tiempo transcurrido supera el límite, la ejecución termina en `TIMED_OUT`.

**Implementación:** `agentflow/core/runtime.py:98`

```python
elapsed = time.monotonic() - start_time
if elapsed >= definition.global_timeout_seconds:
    # → ExecutionStatus.TIMED_OUT
```

**Valor por defecto:** 300 segundos (definido en `GraphDefinition`).

**Ejemplo en YAML:**
```yaml
global_timeout_seconds: 90
```

---

## `timeout_seconds` (por nodo)

Timeout individual por nodo, independiente del timeout global. Usa `asyncio.wait_for` para interrumpir mid-ejecución.

**Propósito:** proteger contra un nodo específico lento sin afectar el límite global de toda la ejecución. Útil cuando un tool externo o un LLM puede colgarse en un paso particular.

**Comportamiento:** si el executor del nodo no termina dentro de `timeout_seconds`, se lanza `NodeTimeoutError`. Este fallo es **no reintentable** — se trata como un fallo definitivo del nodo. Si el nodo define `on_error`, se redirige ahí; si no, la ejecución termina en `FAILED`.

**Implementación:** `agentflow/core/runtime.py` — `asyncio.wait_for(executor.execute(...), timeout=node_timeout)`

**Diferencia con `global_timeout_seconds`:** el timeout global se verifica al inicio de cada iteración del loop (no interrumpe mid-nodo). El timeout por nodo usa `asyncio.wait_for` y **sí interrumpe** la corrutina del executor en mid-ejecución.

**Ejemplo en YAML:**
```yaml
nodes:
  fetch_data:
    type: tool
    on_error: fallback_node   # campo de NodeDefinition, no de config
    config:
      tool_name: external_api_call
      timeout_seconds: 5.0   # falla si la API no responde en 5 segundos
```

Soportado en: `agent`, `tool`, `parallel`.

---

## Limitacion conocida: `global_timeout_seconds` no interrumpe mid-nodo

El timeout global se verifica al inicio de cada iteracion del loop principal, antes de ejecutar el siguiente nodo. Si un nodo tarda mas tiempo que el timeout configurado, el runtime no lo interrumpe — detecta la violacion recien cuando el nodo termina y el loop va a la siguiente iteracion.

**Ejemplo:**
- `global_timeout_seconds: 30`
- Un nodo LLM tarda 5 minutos en responder
- El timeout global no se activa hasta que ese nodo termina

**Solución:** usar `timeout_seconds` por nodo en los nodos que puedan colgarse.

---

## Tests de referencia

| Guard | Test | Archivo |
|-------|------|---------|
| `max_depth` | `test_max_depth_stops_loop` | `agentflow/tests/test_runtime.py:188` |
| `global_timeout_seconds` | `test_global_timeout` | `agentflow/tests/test_runtime.py:267` |
| `timeout_seconds` (nodo) | `test_node_timeout_fails_node` | `agentflow/tests/test_new_node_types.py` |
| `timeout_seconds` + `on_error` | `test_node_timeout_routes_to_on_error` | `agentflow/tests/test_new_node_types.py` |

Todos los tests usan mocks — no requieren LLM real.
