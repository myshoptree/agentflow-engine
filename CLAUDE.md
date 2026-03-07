# AgentFlow — Agent Graph Engine

## Visión del Proyecto

Motor de orquestación de agentes LLM que ejecuta grafos dirigidos con estado compartido y contratos estructurados. **No es un chatbot builder** — es un runtime de orquestación (AI Workflow Operating System).

Stack: **Python + Pydantic AI**, single-tenant primero.

---

## Regla crítica: revisar SPEC.md antes de implementar

**Antes de escribir o modificar cualquier código, revisar [`SPEC.md`](./SPEC.md).**

`SPEC.md` contiene las especificaciones de negocio del sistema — reglas que definen el comportamiento correcto independientemente del stack. Toda implementación nueva debe validarse contra estas reglas:

- ¿La implementación mantiene el **determinismo** de ejecuciones? (SPEC §1.1)
- ¿El estado está **aislado** por ejecución? (SPEC §1.2)
- ¿Toda ejecución tiene un estado terminal garantizado? (SPEC §1.3)
- ¿El compilador detecta grafos con ciclos sin salida? (SPEC §2.4)
- ¿Las condiciones usan solo operadores permitidos — sin eval()? (SPEC §4.1)
- ¿Los checkpoints son inmutables? (SPEC §3.2)
- ¿El routing de errores respeta on_error antes de FAILED? (SPEC §5.2)
- ¿Los eventos de observabilidad mínimos están presentes? (SPEC §7.2)

Si una implementación rompe una regla de `SPEC.md`, la implementación está mal — no la especificación.

### Cuando la implementación introduce comportamiento no cubierto en SPEC.md

Si durante la implementación surge un comportamiento, restricción o decisión de negocio que **no existe aún en `SPEC.md`**:

1. **No asumir** — no inventar la regla ni implementar sin respaldo.
2. **Detener e informar** al usuario: describir el comportamiento nuevo y por qué necesita una especificación.
3. **Esperar aprobación** del usuario sobre cómo debe comportarse el sistema.
4. **Solo después de aprobación**, agregar la nueva especificación a `SPEC.md` en la sección correspondiente.
5. Luego proceder con la implementación.

**`SPEC.md` debe crecer junto con el sistema — nunca quedarse desactualizado respecto a lo que el código realmente hace.**

---

## Estructura del Proyecto

```
agentflow/              ← paquete instalable
├── core/
│   ├── models.py          # GraphDefinition, NodeDefinition, Edge, ExecutionState, ExecutionTrace
│   ├── compiler.py        # GraphCompiler → CompiledGraph (validación estática completa)
│   ├── runtime.py         # ExecutionRuntime (ciclo principal)
│   ├── state_manager.py   # StateManagerProtocol + InMemoryStateManager
│   ├── session_manager.py # SessionManager — conversaciones multi-turno (SPEC §10)
│   ├── grader.py          # GraderRunner — evaluación de ExecutionTrace (SPEC §12)
│   └── observability.py   # StructuredLogger + eventos JSON
├── executors/
│   ├── agent_executor.py      # Pydantic AI + output_schema_inline
│   ├── condition_executor.py
│   ├── tool_executor.py
│   ├── parallel_executor.py
│   ├── set_state_executor.py  # SET_STATE (SPEC §3.4)
│   ├── transform_executor.py  # TRANSFORM con regex template (SPEC §3.5)
│   ├── guardrail_executor.py  # PII, toxicity, custom_llm (SPEC §11)
│   └── registry.py
└── dsl/
    └── condition_parser.py  # DSL seguro + evaluate_cel_condition (SPEC §4.4)

tests/                  ← no se instalan con el paquete
├── test_compiler.py
├── test_runtime.py
├── test_condition_node.py     # ConditionNode con branches AND/OR
├── test_retry_policy.py
├── test_new_node_types.py     # NOTE, SET_STATE, TRANSFORM, START, timeout, inline schema
├── test_parallel_node.py      # PARALLEL: concurrencia, merge, fallos, compilador
├── test_guardrail.py          # toxicity, PII, custom_llm
├── test_grader.py             # deterministic, heuristic, llm_judge
└── test_cel_conditions.py     # CEL: sintaxis, fallback, advertencia

examples/
├── sales_router.yaml                     # Router con edges condicionales
├── sales_router_condition.yaml           # Router con ConditionNode AND/OR
├── 01_evaluator_loop.yaml                # Loop con score y feedback
├── 02_sequential_pipeline.yaml           # Pipeline de 4 agentes en cadena
├── 03_tool_plus_agent.yaml               # ToolNode + AgentNode personalizado
├── 04_supervisor.yaml                    # Supervisor que delega a especialistas
├── 05_error_handling.yaml                # on_error routing y fallback
├── 06_human_in_the_loop.yaml             # Suspend/resume con confirmación humana
├── 07_set_state_and_transform.yaml       # SET_STATE, TRANSFORM, output_schema_inline
├── 08_start_node.yaml                    # START con contrato de inputs + NOTE
├── 09_guardrail.yaml                     # GUARDRAIL toxicity + custom_llm
├── 10_node_timeout.yaml                  # timeout_seconds por nodo + circuit breaker
├── 11_evaluator_loop_with_set_state.yaml # Evaluator loop con SET_STATE + NOTE
├── 12_parallel.yaml                      # PARALLEL enriquecimiento concurrente
├── schemas.py                            # Pydantic output schemas de los ejemplos
└── tools.py                              # Tools simuladas (productos, usuarios)

run.py    # Script de demo CLI
SPEC.md   # Especificaciones de negocio — fuente de verdad
docs/     # Documentación técnica y changelogs
```

---

## Componentes Clave

### 1. Graph Definition Layer
- Formato YAML declarativo — fuente única de verdad
- Schema central: `GraphDefinition` (Pydantic)
- Campos: `id`, `version` (semver), `entry_node`, `nodes`, `edges`, `state_schema`, `global_timeout_seconds`, `max_depth` (default: 50)

### 2. Graph Compiler
- Valida referencias (nodos existentes en edges)
- Detecta nodos inalcanzables (BFS desde entry_node) — incluye edges implícitos: `on_error`, branches de ConditionNode, `on_fail` de GuardrailNode, branches de ParallelNode
- Detecta ciclos sin salida (ciclo con todos edges incondicionales = error)
- Valida campos de condiciones contra `state_schema`
- Valida configs de SET_STATE, TRANSFORM, START, GUARDRAIL, PARALLEL
- Detecta ambigüedad DSL+CEL en mismo edge
- Output: `CompiledGraph` con acceso O(1) y edges ordenados por priority

### 3. Execution Runtime
- Estados: `PENDING → RUNNING → COMPLETED | FAILED | TIMED_OUT | SUSPENDED | CANCELLED`
- Loop principal: verificar timeout global/depth → despachar executor → timeout de nodo (`asyncio.wait_for`) → manejar error/retry → aplicar `state_output_mapping` → guardrail routing → resolver transición (DSL o CEL) → checkpoint
- `NodeTimeoutError` es no-reintentable — sale del retry loop inmediatamente
- NOTE y START son transparentes al runtime (no emiten eventos, avanzan automáticamente)

### 4. State Manager
- Protocolo abstracto (swappable: in-memory, PostgreSQL)
- Métodos: `create_execution`, `load_execution`, `checkpoint`, `update_status`, `resume`, `get_trace`, `save_grade`, `get_grades`
- Checkpoints inmutables por paso

### 5. Observability Layer
- Eventos JSON estructurados: `execution.*`, `node.*`, `transition.resolved`, `state.checkpoint`, `warning.cel_evaluation_error`
- Campos mínimos: `event_type`, `execution_id`, `graph_id`, `node_id`, `timestamp`

---

## Tipos de Nodos

| Tipo | Descripción | SPEC |
|------|-------------|------|
| `agent` | Ejecuta LLM via Pydantic AI, produce structured output | §3.1 |
| `condition` | Evalúa EdgeCondition o CompoundCondition (AND/OR) sobre estado, sin LLM | §4 |
| `tool` | Ejecuta acción externa (API, DB, webhook) | §5 |
| `parallel` | asyncio.gather de ramas, agrega resultados (último gana en conflicto) | §1.2 |
| `human_input` | Suspende ejecución esperando input externo | §6 |
| `set_state` | Asigna literales o referencias al estado sin LLM | §3.4 |
| `transform` | Operaciones declarativas: set, template (regex), extract (dot-notation), cast | §3.5 |
| `start` | Punto de entrada declarativo con contrato explícito de inputs | §2.6 |
| `guardrail` | Evalúa campos del estado contra checks de seguridad con routing `on_fail` | §11 |
| `note` | Documentación embebida — invisible en compilador (sin warning) y runtime | — |
| `end` | Finaliza ejecución → COMPLETED | §1.3 |

---

## DSL de Condiciones (Seguro)

**Nunca usar `eval()`**. Operadores DSL permitidos:
`eq`, `neq`, `gt`, `lt`, `gte`, `lte`, `in`, `contains`, `is_null`

```python
# EdgeCondition ejemplo
{"field": "state.intent", "operator": "eq", "value": "HOT"}
{"field": "state.eval_score", "operator": "gte", "value": 0.8}
```

El campo `field` usa dot-notation: `state.intent` → `execution_state.graph_state["intent"]`.

### CEL como alternativa opt-in (SPEC §4.4)

```yaml
edges:
  - from_node: classifier
    to_node: vip_handler
    condition_language: cel
    condition_cel: "state.score >= 0.9 && state.plan == 'enterprise'"
    priority: 20
```

Un error de evaluación CEL en runtime → condición tratada como `False` + evento `warning.cel_evaluation_error`.

---

## output_schema_inline

Define el schema del agente directamente en YAML — genera un modelo Pydantic con `create_model()`:

```yaml
my_agent:
  type: agent
  config:
    model: "anthropic:claude-haiku-4-5-20251001"
    system_prompt: "..."
    output_schema_inline:
      intent:
        type: str
        enum: ["HOT", "WARM", "COLD"]
      confidence:
        type: float
        default: 0.0
    state_output_mapping:
      intent: intent
```

No puede coexistir con `output_schema` — el compilador lo rechaza.

---

## Integración Pydantic AI

```python
from pydantic_ai import Agent

agent = Agent(
    model=node_config.model,       # e.g. "anthropic:claude-haiku-4-5-20251001"
    system_prompt=node_config.system_prompt,
    output_type=OutputSchema,      # Pydantic model — validado automáticamente
)
result = await agent.run(user_message)
# result.output es OutputSchema validado
```

---

## Patrones Soportados

| Patrón | Cómo se implementa |
|--------|-------------------|
| **Router** | AgentNode clasifica → edges con EdgeCondition según `state.intent` |
| **Supervisor** | AgentNode cuyo output determina rama del grafo |
| **Evaluator Loop** | Edge de retorno con condición en score + `max_depth` como guardia |
| **Tool-Orchestrated** | ToolNode carga datos externos al estado antes del AgentNode |
| **Parallel Enrichment** | ParallelNode con ramas concurrentes → AgentNode consolidador |
| **Guardrail** | AgentNode genera → GuardrailNode valida → routing a `on_fail` si falla |
| **Human-in-the-loop** | HumanInputNode suspende → resume con input externo |
| **Circuit Breaker** | ToolNode con `timeout_seconds` + `on_error` hacia nodo de fallback |

---

## Fases de Implementación

### Fase 1 — Core MVP ✅ Completado
- `core/models.py` — todos los schemas Pydantic incluyendo nuevos tipos de nodo y ExecutionTrace
- `dsl/condition_parser.py` — DSL seguro + CompoundCondition AND/OR + CEL opt-in
- `core/compiler.py` — validación completa: refs, alcanzabilidad (con edges implícitos), ciclos, condiciones DSL/CEL, SET_STATE, TRANSFORM, START, GUARDRAIL, PARALLEL
- `core/runtime.py` — ciclo de ejecución con retry, on_error, timeout global, timeout por nodo (NodeTimeoutError), depth guard, guardrail routing, CEL en transiciones
- `core/state_manager.py` — InMemoryStateManager con checkpointing, ExecutionTrace, graders
- `core/grader.py` — GraderRunner: deterministic, heuristic, llm_judge
- `core/session_manager.py` — sesiones multi-turno con historial (SPEC §10)
- `core/observability.py` — StructuredLogger con eventos JSON + warn_cel_evaluation_error
- `executors/` — Agent (+ inline schema), Condition, Tool, Parallel, SetState, Transform, Guardrail, End, NoOp (NOTE/START)
- Tests: 87 tests, 9 archivos

### Fase 2 — Persistencia + API REST
- StateManager PostgreSQL con checkpointing real
- API REST: POST /executions, GET /executions/{id}, POST /executions/{id}/resume, DELETE /executions/{id}
- Versionado de grafos en DB

### Fase 3 — Observabilidad + Producción
- Métricas (tokens, duración, error rates)
- Dashboard de traces y grades

---

## Decisiones de Diseño

- **Sin eval()**: DSL de condiciones es un parser seguro. CEL es opt-in con fallback a `False` en error.
- **Async everywhere**: todas las operaciones de nodos son `async/await`
- **Contratos inmutables**: cada `state_output_mapping` define qué campos escribe un nodo
- **Version pinning**: una ejecución iniciada con v1 no migra a v2 del grafo
- **max_depth como guardia**: previene loops infinitos. Default: 50.
- **Priority en edges**: el edge con mayor priority se evalúa primero; el de priority=0 sin condición es el fallback
- **NodeTimeoutError no reintentable**: timeout de nodo sale del retry loop inmediatamente — es un fallo definitivo
- **NOTE transparent**: sin warnings de alcanzabilidad, sin eventos en runtime
- **PARALLEL merge**: último branch en la lista gana en conflicto de clave (orden deterministico)
- **GuardrailExecutor siempre preserva `result`**: el runtime detecta `"fail"` sin conocer el `output_mapping`
- **output_schema_inline**: `create_model()` en tiempo de ejecución, no en compilación

---

## Convenciones de Código

- Python 3.11+
- Pydantic v2 para todos los modelos
- `async/await` para todos los ejecutores
- Type hints completos
- Tests con `pytest` + `pytest-asyncio`
- YAML para definición de grafos (via `pyyaml`)

## Herramientas de desarrollo

- **Package manager**: `uv` — usar siempre `uv run`, `uv sync`, `uv add`
- **Run tests**: `uv run pytest` (tests en `tests/`, fuera del paquete)
- **Run demo**: `uv run python run.py --yaml examples/sales_router.yaml "mensaje"`
- **Variables de entorno**: copiar `.env.example` a `.env` — nunca commitear `.env`
- **Dependencias opcionales**: `uv add google-cel-python` (CEL), `uv add presidio-analyzer` (PII)
