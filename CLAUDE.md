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
agentflow/
├── core/
│   ├── models.py          # GraphDefinition, NodeDefinition, Edge, EdgeCondition, ExecutionState
│   ├── compiler.py        # GraphCompiler → CompiledGraph
│   ├── runtime.py         # ExecutionRuntime (ciclo principal)
│   ├── state_manager.py   # StateManagerProtocol + InMemoryStateManager
│   ├── session_manager.py # SessionManager — conversaciones multi-turno (SPEC §10)
│   └── observability.py   # StructuredLogger + eventos JSON
├── executors/
│   ├── agent_executor.py  # Integración con Pydantic AI
│   ├── condition_executor.py
│   ├── tool_executor.py
│   ├── parallel_executor.py
│   └── registry.py
├── dsl/
│   └── condition_parser.py  # DSL seguro para EdgeCondition y CompoundCondition (sin eval())
└── tests/
    ├── test_compiler.py
    ├── test_runtime.py
    ├── test_condition_node.py  # ConditionNode con branches AND/OR
    └── test_retry_policy.py

examples/
├── sales_router.yaml              # Router con edges condicionales
├── sales_router_condition.yaml    # Router con ConditionNode AND/OR
└── schemas.py                     # Pydantic output schemas de los ejemplos

run.py                             # Script de demo CLI
```

---

## Componentes Clave

### 1. Graph Definition Layer
- Formato YAML declarativo — fuente única de verdad
- Schema central: `GraphDefinition` (Pydantic)
- Campos importantes: `id`, `version` (semver), `entry_node`, `nodes`, `edges`, `state_schema`, `global_timeout_seconds`, `max_depth`

### 2. Graph Compiler
- Valida referencias (nodos existentes en edges)
- Detecta nodos inalcanzables (BFS desde entry_node)
- Detecta ciclos sin salida (ciclo con todos edges incondicionales = error)
- Valida campos de condiciones contra `state_schema`
- Output: `CompiledGraph` con acceso O(1) y edges ordenados por priority

### 3. Execution Runtime
- Estados: `PENDING → RUNNING → COMPLETED | FAILED | TIMED_OUT | SUSPENDED | CANCELLED`
- Loop principal: verificar timeout/depth → despachar executor → manejar error/retry → aplicar state_output_mapping → resolver transición → checkpoint
- Despacha a ejecutores según `node.type`

### 4. State Manager
- Protocolo abstracto (swappable: in-memory, PostgreSQL)
- Métodos: `create_execution`, `load_execution`, `checkpoint`, `update_status`, `resume`
- Checkpoints inmutables por paso

### 5. Observability Layer
- Eventos JSON estructurados: `execution.*`, `node.*`, `transition.resolved`, `state.checkpoint`
- Campos mínimos: `event_type`, `execution_id`, `graph_id`, `node_id`, `timestamp`

---

## Tipos de Nodos

| Tipo | Descripción |
|------|-------------|
| `agent` | Ejecuta LLM via Pydantic AI, produce structured output |
| `condition` | Evalúa EdgeCondition o CompoundCondition (AND/OR) sobre estado, sin LLM |
| `tool` | Ejecuta acción externa (API, DB, webhook) |
| `parallel` | asyncio.gather de ramas, agrega resultados |
| `human_input` | Suspende ejecución esperando input externo |
| `end` | Finaliza ejecución → COMPLETED |

---

## DSL de Condiciones (Seguro)

**Nunca usar `eval()`**. Operadores permitidos:
`eq`, `neq`, `gt`, `lt`, `gte`, `lte`, `in`, `contains`, `is_null`

```python
# EdgeCondition ejemplo
{"field": "state.intent", "operator": "eq", "value": "HOT"}
{"field": "state.eval_score", "operator": "gte", "value": 0.8}
```

El campo `field` usa dot-notation para acceder al estado: `state.intent` → `execution_state.graph_state["intent"]`.

---

## Integración Pydantic AI

```python
from pydantic_ai import Agent

agent = Agent(
    model=node_config.model,       # e.g. "anthropic:claude-sonnet-4-6"
    result_type=OutputSchema,      # Pydantic model — validado automáticamente
    system_prompt=node_config.system_prompt,
    tools=[...],
)
result = await agent.run(user_message)
# result.data es OutputSchema validado
```

---

## Patrones Soportados

| Patrón | Cómo se implementa |
|--------|-------------------|
| **Router** | AgentNode clasifica → edges con EdgeCondition según `state.intent` |
| **Supervisor** | AgentNode cuyo output determina rama del grafo |
| **Evaluator Loop** | Edge de retorno con condición en score + `max_depth` como guardia |
| **Tool-Orchestrated** | AgentNode con tools Pydantic AI; el agente decide qué tool llamar |
| **Memory-Enriched** | ToolNode al inicio carga memoria en el estado |

---

## Fases de Implementación

### Fase 1 — Core MVP ✅ Completado
- `core/models.py` — todos los schemas Pydantic
- `dsl/condition_parser.py` — DSL seguro + CompoundCondition AND/OR
- `core/compiler.py` — validación completa (refs, alcanzabilidad, ciclos, condiciones, branches)
- `core/runtime.py` — ciclo de ejecución con retry, on_error, timeout, depth guard
- `core/state_manager.py` — InMemoryStateManager con checkpointing
- `core/session_manager.py` — sesiones multi-turno con historial (SPEC §10)
- `core/observability.py` — StructuredLogger con eventos JSON
- `executors/` — AgentExecutor, ConditionExecutor, ToolExecutor, ParallelExecutor, EndExecutor
- ConditionNode con branches explícitos y condiciones AND/OR
- Tests: compiler, runtime, condition_node, retry_policy

### Fase 2 — Persistencia + API REST
- StateManager PostgreSQL con checkpointing real
- API REST: POST /executions, GET /executions/{id}, POST /executions/{id}/resume, DELETE /executions/{id}
- Versionado de grafos en DB

### Fase 3 — Observabilidad + Producción
- Métricas (tokens, duración, error rates)
- Versionado de grafos en DB

---

## Decisiones de Diseño

- **Sin eval()**: DSL de condiciones es un parser seguro con operadores limitados
- **Async everywhere**: todas las operaciones de nodos son `async/await`
- **Contratos inmutables**: cada `state_output_mapping` define qué campos escribe un nodo
- **Version pinning**: una ejecución iniciada con v1 no migra a v2 del grafo
- **max_depth como guardia**: previene loops infinitos en Evaluator Loop
- **Priority en edges**: el edge con mayor priority se evalúa primero; el de priority=0 sin condición es el fallback

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
- **Run tests**: `uv run pytest`
- **Run demo**: `uv run python run.py --yaml examples/sales_router.yaml "mensaje"`
- **Variables de entorno**: copiar `.env.example` a `.env` — nunca commitear `.env`
