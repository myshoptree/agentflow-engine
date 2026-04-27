# AgentNodes

Motor de orquestación de agentes LLM que ejecuta grafos dirigidos con estado compartido y contratos estructurados. No es un chatbot builder — es un runtime de orquestación (AI Workflow Operating System).

**Stack:** Python 3.11+ · Pydantic AI · async/await

---

## Conceptos clave

Un **grafo** define el flujo de trabajo: nodos conectados por edges con condiciones. Cada ejecución es aislada, determinista y siempre alcanza un estado terminal.

```
entrada → [AgentNode] → [ConditionNode] → [AgentNode] → fin
                              ↓
                         [ToolNode] → [AgentNode] → fin
```

### Tipos de nodo

| Tipo | Descripción |
|------|-------------|
| `agent` | Ejecuta un LLM vía Pydantic AI, produce structured output |
| `condition` | Evalúa condiciones sobre el estado sin LLM — bifurca el flujo |
| `tool` | Ejecuta una acción externa (API, DB, webhook) |
| `parallel` | Ejecuta ramas concurrentemente con `asyncio.gather` |
| `human_input` | Suspende la ejecución esperando input externo |
| `set_state` | Asigna valores literales o referencias al estado sin LLM |
| `transform` | Aplica operaciones declarativas sobre el estado (set, template, extract, cast) |
| `start` | Punto de entrada declarativo con contrato explícito de inputs |
| `guardrail` | Evalúa campos del estado contra criterios de seguridad (PII, toxicidad, LLM-judge) |
| `note` | Documentación embebida en el grafo — invisible en runtime |
| `end` | Finaliza la ejecución → `COMPLETED` |

### Estados de ejecución

```
PENDING → RUNNING → COMPLETED
                  → FAILED
                  → TIMED_OUT
                  → SUSPENDED → RUNNING (resume)
                  → CANCELLED
```

---

## Instalación

### Como librería en otro proyecto (repo privado de GitHub)

```bash
# Con pip
pip install git+https://<TOKEN>@github.com/tu-org/agentflow-graph.git

# Con uv
uv add git+https://<TOKEN>@github.com/tu-org/agentflow-graph.git
```

O en el `pyproject.toml` del microservicio:

```toml
dependencies = [
    "agentflow @ git+https://${GITHUB_TOKEN}@github.com/tu-org/agentflow-graph.git",
]
```

### Para desarrollo local

Requiere [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo>
cd agentflow-graph
uv sync
cp .env.example .env   # agregar API keys
```

---

## Quickstart

### Ejecutar un grafo desde terminal

```bash
# Mensaje único
uv run python run.py "quiero comprar ya"

# Con un yaml específico
uv run python run.py --yaml examples/sales_router.yaml "quiero comprar ya"
uv run python run.py --yaml examples/sales_router_condition.yaml "quiero comprar ya"

# Modo chat interactivo multi-turno
uv run python run.py --yaml examples/sales_router.yaml --chat
```

### Ejecutar tests

```bash
uv run pytest
uv run pytest tests/test_parallel_node.py -v
```

### Uso como librería en un microservicio

```python
import yaml
from agentflow import (
    GraphDefinition,
    GraphCompiler,
    ExecutionRuntime,
    InMemoryStateManager,
    InMemorySessionManager,
    ExecutionStatus,
    MessageRole,
)

# 1. Cargar y compilar el grafo (una vez al iniciar)
with open("my_graph.yaml") as f:
    graph_def = GraphDefinition(**yaml.safe_load(f))

compiler = GraphCompiler()
compiled = compiler.compile(graph_def)

# 2. Ejecutar
state_manager = InMemoryStateManager()
exec_state = await state_manager.create_execution(graph_def, {"user_input": "hola"})
runtime = ExecutionRuntime(state_manager)
final = await runtime.run(compiled, exec_state)

# 3. Leer resultado
if final.status == ExecutionStatus.COMPLETED:
    response = final.graph_state.get("response")
```

---

## Definición de grafos (YAML)

Los grafos se definen en YAML. El schema central es `GraphDefinition`.

```yaml
id: mi-grafo
version: "1.0.0"
entry_node: inicio

state_schema:
  - name: user_input
    type: str
    required: true
  - name: intent
    type: str
    default: ""

global_timeout_seconds: 60
max_depth: 20          # guardia contra loops infinitos (default: 50)

nodes:
  inicio:
    type: agent
    config:
      model: "anthropic:claude-haiku-4-5-20251001"
      system_prompt: "Clasifica la intención del usuario."
      output_schema_inline:
        intent:
          type: str
          enum: ["HOT", "WARM", "COLD"]
      state_output_mapping:
        intent: intent

  fin:
    type: end
    config: {}

edges:
  - from_node: inicio
    to_node: fin
```

### ConditionNode con branches AND/OR

```yaml
check_intent:
  type: condition
  config:
    branches:
      - condition:
          operator: and
          conditions:
            - {field: state.intent, operator: eq, value: "HOT"}
            - {field: state.confidence, operator: gte, value: 0.8}
        target: closer
      - condition:
          {field: state.intent, operator: eq, value: "WARM"}
        target: educator
    default: nurture
```

### SET_STATE y TRANSFORM

```yaml
# Inicializa estado antes de un loop
reset:
  type: set_state
  config:
    assignments:
      iteration: 0
      status: "pending"

# Construye un campo derivado sin LLM
enrich:
  type: transform
  config:
    operations:
      - set: display_name
        template: "{state.product_name} (${state.product_price})"
      - set: count_int
        from_field: state.count_str
        cast: int
```

### GUARDRAIL

```yaml
content_guard:
  type: guardrail
  on_error: safety_handler
  config:
    checks:
      - type: toxicity
        field: state.response
      - type: custom_llm
        field: state.response
        model: "anthropic:claude-haiku-4-5-20251001"
        prompt: "¿Contiene este texto información sensible? Responde yes o no."
    on_fail: safety_handler
    output_mapping:
      guard_result: result
      guard_reason: reason
```

### Timeout por nodo

```yaml
fetch_external:
  type: tool
  on_error: fallback_fetch
  config:
    tool_name: "fetch_product_from_api"
    timeout_seconds: 3.0   # asyncio.wait_for — interrumpe mid-ejecución
    input_mapping:
      product_id: product_id
    output_mapping:
      product_name: name
```

### Condiciones: operadores DSL y CEL

**DSL** (default): `eq` · `neq` · `gt` · `lt` · `gte` · `lte` · `in` · `contains` · `is_null`

```yaml
edges:
  - from_node: classifier
    to_node: closer
    condition:
      field: state.intent
      operator: eq
      value: "HOT"
    priority: 10
```

**CEL** (opt-in por edge):

```yaml
edges:
  - from_node: classifier
    to_node: vip_handler
    condition_language: cel
    condition_cel: "state.score >= 0.9 && state.plan == 'enterprise'"
    priority: 20
```

Las condiciones usan dot-notation: `state.intent` → `graph_state["intent"]`. Nunca se usa `eval()`.

---

## Estructura del proyecto

```
agentflow/              ← paquete instalable
├── core/
│   ├── models.py          # GraphDefinition, NodeDefinition, Edge, ExecutionState, ExecutionTrace
│   ├── compiler.py        # GraphCompiler → CompiledGraph (validación estática)
│   ├── runtime.py         # ExecutionRuntime (ciclo principal)
│   ├── state_manager.py   # InMemoryStateManager con checkpointing y graders
│   ├── session_manager.py # InMemorySessionManager (conversaciones multi-turno)
│   ├── grader.py          # GraderRunner — evaluación de ExecutionTrace
│   └── observability.py   # StructuredLogger — eventos JSON estructurados
├── executors/
│   ├── agent_executor.py      # Pydantic AI + output_schema_inline
│   ├── condition_executor.py
│   ├── tool_executor.py
│   ├── parallel_executor.py
│   ├── set_state_executor.py
│   ├── transform_executor.py
│   ├── guardrail_executor.py
│   └── registry.py
└── dsl/
    └── condition_parser.py  # DSL seguro + evaluate_cel_condition

tests/                  ← no se instalan
├── test_compiler.py
├── test_runtime.py
├── test_condition_node.py
├── test_retry_policy.py
├── test_new_node_types.py   # NOTE, SET_STATE, TRANSFORM, START, timeout, inline schema
├── test_parallel_node.py    # PARALLEL: concurrencia, merge, fallos
├── test_guardrail.py        # GUARDRAIL: toxicity, PII, custom_llm
├── test_grader.py           # GraderRunner: deterministic, heuristic, llm_judge
└── test_cel_conditions.py   # CEL: sintaxis, fallback, evento de advertencia

examples/
├── sales_router.yaml                    # Router con edges condicionales
├── sales_router_condition.yaml          # Router con ConditionNode AND/OR
├── 01_evaluator_loop.yaml               # Loop con score y feedback
├── 02_sequential_pipeline.yaml          # Pipeline de 4 agentes en cadena
├── 03_tool_plus_agent.yaml              # ToolNode + AgentNode personalizado
├── 04_supervisor.yaml                   # Supervisor que delega a especialistas
├── 05_error_handling.yaml               # on_error routing y fallback
├── 06_human_in_the_loop.yaml            # Suspend/resume con confirmación humana
├── 07_set_state_and_transform.yaml      # SET_STATE, TRANSFORM, output_schema_inline
├── 08_start_node.yaml                   # START con contrato de inputs explícito
├── 09_guardrail.yaml                    # GUARDRAIL con toxicity + custom_llm
├── 10_node_timeout.yaml                 # timeout_seconds por nodo + circuit breaker
├── 11_evaluator_loop_with_set_state.yaml# Variante del evaluator loop con SET_STATE
├── 12_parallel.yaml                     # PARALLEL: enriquecimiento concurrente
├── schemas.py                           # Pydantic output schemas de los ejemplos
└── tools.py                             # Tools simuladas (productos, usuarios)

run.py    # Script de demo CLI
SPEC.md   # Especificaciones de negocio — fuente de verdad
docs/     # Documentación técnica y changelogs
```

---

## Ejemplos

Todos los ejemplos se ejecutan con:

```bash
uv run python run.py --yaml examples/<archivo>.yaml "<mensaje>"
```

---

### Router con ConditionNode AND/OR
**`sales_router_condition.yaml`**

Clasifica la intención del usuario y bifurca el flujo con condiciones compuestas AND/OR.

```
classifier → check_intent (condition) → closer   [HOT + confidence >= 0.8]
                                      → educator [WARM]
                                      → nurture  [default]
```

```bash
uv run python run.py --yaml examples/sales_router_condition.yaml "quiero comprar ahora"
```

---

### Evaluator Loop
**`01_evaluator_loop.yaml`**

Un agente genera contenido, otro lo evalúa con score numérico. Si el score es bajo vuelve al escritor con feedback. `max_depth` actúa como guardia contra loops infinitos.

```
writer → evaluator → check_quality → publisher → end  [score >= 0.8]
            ↑                      → writer            [score < 0.8]
```

```bash
uv run python run.py --yaml examples/01_evaluator_loop.yaml "inteligencia artificial en medicina"
```

---

### Pipeline Secuencial
**`02_sequential_pipeline.yaml`**

Cuatro agentes en cadena donde cada uno enriquece el estado.

```
extractor → analyzer → strategist → responder → end
```

```bash
uv run python run.py --yaml examples/02_sequential_pipeline.yaml "me cobraron dos veces el mes pasado"
```

---

### Tool + Agent
**`03_tool_plus_agent.yaml`**

Un `ToolNode` carga datos del usuario desde un sistema externo (simulado) antes de invocar el LLM. El agente responde de forma personalizada con contexto real.

```
load_context (tool) → check_usage (condition) → alert_agent    [usage >= 90%]
                                              → advisory_agent [usage >= 70%]
                                              → support_agent  [default]
```

```bash
uv run python run.py --yaml examples/03_tool_plus_agent.yaml "¿cómo puedo ver mi factura?"
```

---

### Supervisor
**`04_supervisor.yaml`**

Un agente supervisor decide a qué especialista derivar.

```
supervisor → route (condition) → billing_agent | technical_agent | account_agent | general_agent
```

```bash
uv run python run.py --yaml examples/04_supervisor.yaml "no puedo acceder a mi cuenta"
```

---

### Error Handling
**`05_error_handling.yaml`**

Demuestra `on_error` routing: si el nodo principal falla, el runtime redirige al `error_handler`.

```
risky_agent ──[on_error]──→ error_handler → end
     ↓
check_result → success_end
```

```bash
uv run python run.py --yaml examples/05_error_handling.yaml "procesa esta solicitud"
```

---

### Human-in-the-loop
**`06_human_in_the_loop.yaml`**

La ejecución se **suspende** en el nodo `human_input` esperando confirmación antes de ejecutar una acción de alto impacto.

```
analyzer → await_confirmation (human_input) ← SUSPENDED
                ↓ resume con "sí" / "no"
           check_confirmation (condition) → executor  [confirmado]
                                          → canceller [cancelado]
```

```bash
uv run python run.py --yaml examples/06_human_in_the_loop.yaml "elimina todos los registros del mes pasado"
```

---

### SET_STATE y TRANSFORM
**`07_set_state_and_transform.yaml`**

Demuestra `set_state` para inicializar estado, `transform` con template y cast, y `output_schema_inline` para definir el schema del agente directamente en YAML.

```
initialize (set_state) → greet (agent) → enrich (transform) → responder (agent) → end
```

```bash
uv run python run.py --yaml examples/07_set_state_and_transform.yaml "Hola"
```

---

### START node
**`08_start_node.yaml`**

Demuestra el nodo `start` con contrato explícito de inputs y el nodo `note` para documentación embebida.

```
start → classifier (agent) → check_intent (condition) → specialist | general → end
```

```bash
uv run python run.py --yaml examples/08_start_node.yaml "quiero cancelar mi suscripción"
```

---

### GUARDRAIL
**`09_guardrail.yaml`**

El agente genera una respuesta → el guardrail la evalúa antes de publicarla. Si detecta contenido problemático, redirige al `safety_handler`.

```
responder → content_guard (guardrail) → end
                    ↓ [on_fail]
             safety_handler → end
```

```bash
uv run python run.py --yaml examples/09_guardrail.yaml "¿cómo puedo ayudarte?"
```

---

### Timeout por nodo
**`10_node_timeout.yaml`**

Demuestra `timeout_seconds` por nodo como circuit breaker: si la API externa no responde en 3s, `on_error` activa el fallback de caché.

```
fetch_external (tool, timeout=3s) ──[on_error]──→ fallback_fetch → enrich → responder → end
        ↓ [happy path]
      enrich → responder → end
```

```bash
uv run python run.py --yaml examples/10_node_timeout.yaml "producto p001"
# Con timeout simulado:
SIMULATE_SLOW_API=true uv run python run.py --yaml examples/10_node_timeout.yaml "producto p001"
```

---

### Evaluator Loop con SET_STATE
**`11_evaluator_loop_with_set_state.yaml`**

Variante del evaluator loop clásico que usa `set_state` para inicializar el contador de iteraciones y un nodo `note` para documentar una limitación del DSL.

```
reset_counter (set_state) → writer → evaluator → check →
  ├─ score >= 0.8    → publisher → end
  ├─ iteration >= 3  → force_publish → end
  └─ default         → increment (set_state) → writer
```

```bash
uv run python run.py --yaml examples/11_evaluator_loop_with_set_state.yaml "el cambio climático"
```

---

### Parallel — enriquecimiento concurrente
**`12_parallel.yaml`**

Tres agentes ejecutan en paralelo con `asyncio.gather` — marketing, análisis de precio y estado de stock — y un cuarto consolida los resultados.

```
load_data (set_state) → enrich (parallel) ──┬── marketing_writer ──┐
                                            ├── price_analyst     ──┼→ consolidate → end
                                            └── stock_checker     ──┘
```

```bash
uv run python run.py --yaml examples/12_parallel.yaml "Laptop Pro 15"
```

---

## Capacidades avanzadas

### output_schema_inline

Define el schema de output del agente directamente en YAML, sin necesitar un módulo Python externo:

```yaml
my_agent:
  type: agent
  config:
    model: "anthropic:claude-haiku-4-5-20251001"
    system_prompt: "Clasifica la intención."
    output_schema_inline:
      intent:
        type: str
        enum: ["HOT", "WARM", "COLD"]
      confidence:
        type: float
    state_output_mapping:
      intent: intent
      confidence: confidence
```

### ExecutionTrace y Graders

Evalúa la calidad de ejecuciones pasadas:

```python
from agentflow import GraderRunner, Grader, GraderType

trace = await state_manager.get_trace(execution_id)
runner = GraderRunner()

# Determinista: compara estado final con valor esperado
result = await runner.grade(trace, Grader(
    id="intent_check",
    name="Intent Check",
    type=GraderType.DETERMINISTIC,
    config={"field": "state.intent", "operator": "eq", "value": "HOT"},
))

# Heurístico: evalúa métricas del trace
result = await runner.grade(trace, Grader(
    id="perf_check",
    name="Performance",
    type=GraderType.HEURISTIC,
    config={"max_tokens": 5000, "max_duration_ms": 10000},
))
```

### Sesiones multi-turno

```python
from agentflow import InMemorySessionManager, InMemoryStateManager

state_manager = InMemoryStateManager()
sm = InMemorySessionManager()
session = await sm.create_session(graph_id="sales-router", graph_version="1.0.0")
```

---

## Variables de entorno

Copiar `.env.example` a `.env` y configurar las API keys según el modelo usado (`anthropic:...`, `openai:...`, etc.).

### Dependencias opcionales

| Paquete | Para qué |
|---------|----------|
| `google-cel-python` | Condiciones CEL en edges (`condition_language: cel`) |
| `presidio-analyzer` | Check PII en nodos GUARDRAIL (`type: pii`) |

```bash
uv add google-cel-python   # opcional
uv add presidio-analyzer   # opcional
```
