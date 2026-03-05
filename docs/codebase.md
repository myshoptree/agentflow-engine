# Codebase — Cómo funciona AgentFlow

Guía de referencia para entender qué hace cada archivo, cómo se relacionan entre sí y por qué están organizados así.

---

## Mapa general

```
run.py                          ← punto de entrada CLI (demo)
agentflow/
├── core/
│   ├── models.py               ← contratos de datos (Pydantic)
│   ├── compiler.py             ← validación estática del grafo
│   ├── runtime.py              ← motor de ejecución (loop principal)
│   ├── state_manager.py        ← ciclo de vida del estado de ejecución
│   ├── session_manager.py      ← sesiones y historial de conversación
│   └── observability.py        ← eventos JSON estructurados
├── executors/
│   ├── agent_executor.py       ← ejecuta nodos LLM via Pydantic AI
│   ├── condition_executor.py   ← nodo condición (no-op, runtime lo resuelve)
│   ├── tool_executor.py        ← ejecuta funciones Python registradas
│   ├── parallel_executor.py    ← ejecuta ramas en paralelo
│   └── registry.py             ← mapea NodeType → executor correcto
└── dsl/
    └── condition_parser.py     ← evalúa condiciones sin eval()
examples/
├── *.yaml                      ← grafos de ejemplo
├── schemas.py                  ← modelos Pydantic de output para los ejemplos
└── tools.py                    ← tools simuladas registradas en el registry
```

---

## `run.py` — Punto de entrada CLI

Script de demo que conecta todas las piezas. No es parte del engine — es una capa de presentación para ejecutar grafos desde la terminal.

### Qué hace

```
argv → parsear --yaml / --chat
     → load_graph()        lee YAML y crea GraphDefinition
     → GraphCompiler()     valida y compila el grafo
     → run_single()        un mensaje → una ejecución → imprime resultado
       run_chat()          loop interactivo multi-turno
```

### Funciones clave

| Función | Responsabilidad |
|---------|----------------|
| `load_graph(path)` | Lee el YAML y lo deserializa en `GraphDefinition` via Pydantic |
| `send_message(...)` | Orquesta una ejecución completa: carga sesión → construye estado → ejecuta grafo → guarda historial |
| `_run_with_human_input(...)` | Maneja el ciclo suspend/resume: si el grafo queda `SUSPENDED`, pide input al usuario y resume |
| `_find_human_input_key(...)` | Detecta qué campo espera el nodo `human_input` para inyectar el input del usuario |
| `run_single(message, yaml)` | Modo mensaje único — imprime el trace y el resultado final |
| `run_chat(yaml)` | Modo chat — loop `input()` que reutiliza la misma sesión entre turnos |

### TraceHandler

Un `logging.Handler` que intercepta los eventos JSON del engine (`agentflow.observability`) y los imprime en formato legible:

```
  ▶ [agent] classifier
      input : { user_input='quiero comprar ahora' }
  ✓ [agent] classifier  843ms
      output: { intent='HOT', confidence=0.95 }
  classifier → check_intent
```

Se activa con `_setup_trace()` al inicio de `run_single` y `run_chat`.

---

## `core/models.py` — Contratos de datos

Define **todas** las estructuras de datos del sistema como modelos Pydantic. Es la fuente única de verdad para qué forma tienen los datos en cada capa.

### Grupos de modelos

**Grafo (lo que el usuario define en YAML):**

| Clase | Qué representa |
|-------|---------------|
| `GraphDefinition` | El grafo completo: id, version, entry_node, nodes, edges, state_schema |
| `NodeDefinition` | Un nodo: tipo + config dict + on_error |
| `Edge` | Conexión entre nodos: from → to + condición opcional + priority |
| `EdgeCondition` | Condición simple: `{field, operator, value}` |
| `CompoundCondition` | Condición AND/OR sobre una lista de `EdgeCondition` |
| `ConditionBranch` | Branch de un ConditionNode: condición + target |
| `StateFieldDefinition` | Campo del estado: nombre, tipo, default, required |

**Configs por tipo de nodo:**

| Clase | Nodo |
|-------|------|
| `AgentNodeConfig` | `agent` — model, system_prompt, output_schema, state_output_mapping, retry_policy |
| `ToolNodeConfig` | `tool` — tool_name, input_mapping, output_mapping |
| `ConditionNodeConfig` | `condition` — branches, default |
| `ParallelNodeConfig` | `parallel` — lista de node_ids a ejecutar en paralelo |
| `HumanInputNodeConfig` | `human_input` — prompt, input_mapping |
| `RetryPolicy` | Política de reintentos: max_retries, backoff con fórmula exponencial |

**Grafo compilado (output del compiler):**

| Clase | Qué representa |
|-------|---------------|
| `CompiledGraph` | Grafo validado con acceso O(1) por node_id |
| `CompiledNode` | Nodo con sus edges de salida ya ordenados por priority |
| `CompilationError` | Error o warning de compilación con severity y node_id |

**Estado de ejecución (lo que vive en runtime):**

| Clase | Qué representa |
|-------|---------------|
| `ExecutionState` | Estado completo de una ejecución: id, status, current_node, graph_state, depth |
| `ExecutionStatus` | Enum: PENDING / RUNNING / COMPLETED / FAILED / TIMED_OUT / SUSPENDED / CANCELLED |
| `StateCheckpoint` | Snapshot inmutable del graph_state en un momento dado |
| `NodeExecutionRecord` | Log de ejecución de un nodo: input, output, duración, tokens |

**Sesión y conversación:**

| Clase | Qué representa |
|-------|---------------|
| `Session` | Agrupa ejecuciones bajo un session_id con historial y accumulated_state |
| `ConversationMessage` | Mensaje `{role: user/assistant, content}` |

---

## `core/compiler.py` — Validación estática

Toma un `GraphDefinition` y produce un `CompiledGraph` — o lanza `GraphCompilationError` si hay errores.

### Qué valida

```
GraphDefinition
    │
    ├── _check_edge_references()   → todos los from_node/to_node existen en nodes
    ├── _check_reachability()      → BFS desde entry_node, nodos inalcanzables → warning
    ├── _check_cycles()            → Tarjan SCC, ciclos sin salida condicional → error
    └── _check_conditions()
            ├── edges con condition → field empieza con "state.", existe en state_schema, tipo compatible
            └── ConditionNode branches → targets existen, fields existen en state_schema
```

### Output

`CompiledGraph` con:
- `nodes: dict[str, CompiledNode]` — acceso O(1) por node_id
- Edges de cada nodo **ordenados por priority descendente** (el de mayor priority se evalúa primero)
- `compilation_warnings` — nodos inalcanzables (no bloquean ejecución)

---

## `core/runtime.py` — Motor de ejecución

El loop principal que avanza el grafo nodo a nodo hasta alcanzar un estado terminal.

### Loop principal (`run()`)

```
mientras no terminal:
  1. Verificar timeout global (→ TIMED_OUT)
  2. Verificar max_depth (→ FAILED)
  3. Si nodo es END → checkpoint → COMPLETED
  4. Si nodo es HUMAN_INPUT → resolver siguiente nodo → SUSPENDED
  5. Ejecutar nodo con reintentos (retry_policy)
     - Si falla y tiene on_error → ir a on_error node
     - Si falla sin on_error → FAILED
  6. Aplicar state_updates al graph_state
  7. Checkpoint
  8. Resolver transición:
     - Si es CONDITION con branches → _resolve_condition_node()
     - Si no → _resolve_transition() (edges por priority)
  9. Avanzar current_node, depth += 1
```

### Resolución de transiciones

**`_resolve_transition()`** — edges clásicos:
- Evalúa edges en orden de priority descendente
- Primer edge condicional que matchea → siguiente nodo
- Edge sin condición → fallback
- Ninguno matchea → None → FAILED

**`_resolve_condition_node()`** — ConditionNode con branches:
- Evalúa branches en orden
- Primer branch que matchea → target
- Si ninguno → `config.default` o None → FAILED

---

## `core/state_manager.py` — Ciclo de vida del estado

Gestiona la persistencia y ciclo de vida de las ejecuciones. El protocolo abstracto `StateManagerProtocol` permite swappear la implementación (in-memory → PostgreSQL) sin cambiar el runtime.

### Métodos principales

| Método | Qué hace |
|--------|---------|
| `create_execution(graph, initial_input)` | Inicializa `graph_state` con defaults del schema + initial_input |
| `load_execution(execution_id)` | Recupera el estado actual de una ejecución |
| `save_execution(state)` | Persiste el estado (deep copy para aislamiento) |
| `checkpoint(state, node_id)` | Crea snapshot inmutable del graph_state en ese momento |
| `update_status(id, status)` | Cambia el status sin tocar el estado interno |
| `resume(execution_id, input_data)` | Inyecta input al estado y cambia SUSPENDED → RUNNING |
| `cancel(execution_id)` | Marca RUNNING/SUSPENDED como CANCELLED |
| `record_node_execution(record)` | Guarda el log de ejecución de cada nodo |

### Aislamiento

Cada operación trabaja con **deep copies** — nunca referencias al objeto interno. Esto garantiza que dos ejecuciones concurrentes no puedan compartir estado (SPEC §1.2).

---

## `core/session_manager.py` — Sesiones multi-turno

Agrupa múltiples ejecuciones bajo una sesión con historial de conversación persistente.

### Flujo por mensaje

```
1. append_message(USER, mensaje)
2. get_history_for_llm() → lista [{role, content}] para el LLM
3. [ejecución del grafo]
4. append_message(ASSISTANT, respuesta)
5. update_accumulated_state(graph_state) → el estado se acumula para el próximo turno
```

### Estado acumulado

`accumulated_state` es el `graph_state` de la última ejecución completada. Se usa como base del estado inicial del siguiente mensaje — así el agente "recuerda" intent, nombre del usuario, etc. sin repetir clasificaciones.

---

## `core/observability.py` — Eventos JSON

Emite eventos estructurados via `logging.getLogger("agentflow.observability")`. Nunca suprime un evento silenciosamente (SPEC §7.3).

### Eventos emitidos

| Evento | Cuándo |
|--------|--------|
| `execution.started` | Al inicio del loop |
| `execution.completed` | Al llegar a un nodo END |
| `execution.failed` | En cualquier fallo terminal |
| `execution.timed_out` | Al expirar global_timeout_seconds |
| `execution.suspended` | Al llegar a human_input |
| `node.started` | Antes de ejecutar un nodo — incluye `input_state` |
| `node.completed` | Tras ejecutar exitosamente — incluye `state_updates` y `duration_ms` |
| `node.failed` | Tras un intento fallido |
| `node.retrying` | Antes de un reintento — incluye `backoff_seconds` |
| `transition.resolved` | Cuando se elige el siguiente nodo |
| `transition.no_valid_transition` | Cuando ningún edge aplica |
| `state.checkpoint` | Tras crear un checkpoint |

---

## `dsl/condition_parser.py` — Evaluador de condiciones

Evalúa condiciones sobre el `graph_state` **sin usar `eval()`**. Operadores fijos definidos en SPEC §4.1.

### Funciones

**`evaluate_condition(condition, graph_state)`**
- Resuelve el `field` con dot-notation: `"state.intent"` → `graph_state["intent"]`
- Aplica el operador: `eq`, `neq`, `gt`, `lt`, `gte`, `lte`, `in`, `contains`, `is_null`
- Devuelve `bool`

**`evaluate_compound_condition(condition, graph_state)`**
- Si recibe `EdgeCondition` → delega a `evaluate_condition()`
- Si recibe `CompoundCondition`:
  - `AND` → `all(results)`
  - `OR` → `any(results)`

---

## `executors/` — Ejecutores por tipo de nodo

Cada executor implementa `async execute(node_id, node_def, execution_state) -> dict[str, Any]`.

El dict retornado son los **state_updates** que el runtime aplica al `graph_state`.

### `agent_executor.py`

1. Parsea `AgentNodeConfig` del node_def
2. Importa dinámicamente el `output_schema` (fully-qualified class name)
3. Crea un `pydantic_ai.Agent` con model + system_prompt + output_type
4. Construye el mensaje desde `graph_state["user_input"]`
5. Si hay `conversation_history` en el estado → lo pasa como `message_history` al LLM
6. Mapea el output al estado via `state_output_mapping: {state_field: output_field}`

### `tool_executor.py`

1. Busca la tool por nombre en `_registry` (dict global)
2. Construye kwargs desde `input_mapping: {tool_param: state_field}`
3. Ejecuta `async tool_fn(**kwargs) → dict`
4. Mapea el resultado al estado via `output_mapping: {state_field: output_field}`

Para registrar una tool: `register_tool("nombre", async_fn)`.

### `condition_executor.py`

No-op — devuelve `{}`. La lógica de condición la resuelve el runtime en `_resolve_condition_node()` o `_resolve_transition()`, no el executor.

### `parallel_executor.py`

1. Parsea `ParallelNodeConfig.branches` (lista de node_ids)
2. `asyncio.gather()` de todos los branches simultáneamente
3. Merge de resultados — branches posteriores sobreescriben en conflicto de keys

### `registry.py`

`get_executor(node_type, compiled_graph) → executor`

Mapeo directo NodeType → instancia de executor. `END` y `HUMAN_INPUT` tienen executors sentinel — el runtime los intercepta antes de despachar.

---

## `examples/tools.py` — Tools simuladas

Registra tools de ejemplo para los grafos que usan nodos `tool`. Se llama una vez al inicio de `run.py`.

```python
register_example_tools()  # registra "get_user_context"
```

`get_user_context(user_id)` simula una consulta a CRM con datos hardcodeados para `u001`–`u004`.

---

## Flujo completo de una ejecución

```
run.py
  load_graph("example.yaml")
    → GraphDefinition (Pydantic)

  GraphCompiler().compile(graph_def)
    → CompiledGraph (validado, edges ordenados)

  StateManager.create_execution(graph_def, initial_input)
    → ExecutionState (PENDING, current_node = entry_node)

  ExecutionRuntime.run(compiled, exec_state)
    → loop:
        get_executor(node_type)
        executor.execute() → state_updates
        graph_state.update(state_updates)
        checkpoint()
        _resolve_transition() o _resolve_condition_node()
        current_node = next_node
        depth += 1
    → ExecutionState (COMPLETED / FAILED / SUSPENDED / ...)

  SessionManager.append_message(ASSISTANT, response)
  SessionManager.update_accumulated_state(graph_state)
```
