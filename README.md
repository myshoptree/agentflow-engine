# AgentFlow

Motor de orquestación de agentes LLM que ejecuta grafos dirigidos con estado compartido y contratos estructurados. No es un chatbot builder — es un runtime de orquestación (AI Workflow Operating System).

**Stack:** Python 3.11+ · Pydantic AI · FastAPI · async/await

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
| `parallel` | Ejecuta ramas en paralelo con `asyncio.gather` |
| `human_input` | Suspende la ejecución esperando input externo |
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

Requiere [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo>
cd agentflow
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

uv run python run.py --yaml examples/01_evaluator_loop.yaml "inteligencia artificial en medicina"
uv run python run.py --yaml examples/02_sequential_pipeline.yaml "me cobraron dos veces el mes pasado"
uv run python run.py --yaml examples/03_tool_plus_agent.yaml  # requiere user_id en state
uv run python run.py --yaml examples/04_supervisor.yaml "no puedo acceder a mi cuenta"
uv run python run.py --yaml examples/05_error_handling.yaml "procesa esto"

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
max_depth: 20

nodes:
  inicio:
    type: agent
    config:
      model: "anthropic:claude-sonnet-4-6"
      system_prompt: "Clasifica la intención del usuario."
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

Alternativa más expresiva a múltiples edges condicionales:

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

### Operadores de condición

`eq` · `neq` · `gt` · `lt` · `gte` · `lte` · `in` · `contains` · `is_null`

Las condiciones usan dot-notation: `state.intent` → `graph_state["intent"]`. Nunca se usa `eval()`.

---

## Estructura del proyecto

```
agentflow/
├── core/
│   ├── models.py          # GraphDefinition, NodeDefinition, Edge, ExecutionState
│   ├── compiler.py        # GraphCompiler → CompiledGraph (validación estática)
│   ├── runtime.py         # ExecutionRuntime (ciclo principal)
│   ├── state_manager.py   # InMemoryStateManager
│   ├── session_manager.py # InMemorySessionManager (conversaciones multi-turno)
│   └── observability.py   # StructuredLogger — eventos JSON
├── executors/
│   ├── agent_executor.py
│   ├── condition_executor.py
│   ├── tool_executor.py
│   ├── parallel_executor.py
│   └── registry.py
├── dsl/
│   └── condition_parser.py  # DSL seguro para condiciones
└── tests/

examples/
├── sales_router.yaml              # Router con edges condicionales
├── sales_router_condition.yaml    # Router con ConditionNode AND/OR
├── 01_evaluator_loop.yaml         # Loop con score y feedback
├── 02_sequential_pipeline.yaml    # Pipeline de 4 agentes en cadena
├── 03_tool_plus_agent.yaml        # ToolNode + AgentNode personalizado
├── 04_supervisor.yaml             # Supervisor que delega a especialistas
├── 05_error_handling.yaml         # on_error routing y fallback
├── 06_human_in_the_loop.yaml      # Suspend/resume con confirmación humana
├── schemas.py                     # Pydantic output schemas de los ejemplos
└── tools.py                       # Tools simuladas para el ejemplo 03

run.py                             # Script de demo CLI
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

Clasifica la intención del usuario y bifurca el flujo. Demuestra `ConditionNode` con condiciones compuestas AND/OR.

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
            └──────────────────────────────────
```

```bash
uv run python run.py --yaml examples/01_evaluator_loop.yaml "inteligencia artificial en medicina"
```

---

### Pipeline Secuencial
**`02_sequential_pipeline.yaml`**

Cuatro agentes en cadena donde cada uno enriquece el estado: extrae entidades, analiza sentimiento e intención, define estrategia y redacta la respuesta final.

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

Un agente supervisor analiza la solicitud y decide a qué especialista derivar. El `ConditionNode` lee la decisión y rutea al especialista correspondiente.

```
supervisor → route_to_specialist (condition) → billing_agent
                                             → technical_agent
                                             → account_agent
                                             → sales_agent
                                             → general_agent  [default]
```

```bash
uv run python run.py --yaml examples/04_supervisor.yaml "no puedo acceder a mi cuenta"
```

---

### Error Handling
**`05_error_handling.yaml`**

Demuestra `on_error` routing (SPEC §5.2): si el nodo principal falla después de reintentos, el runtime lo redirige al `error_handler` en vez de terminar en `FAILED`.

```
risky_agent ──[on_error]──→ error_handler → error_end
     ↓
check_result → success_end
```

```bash
uv run python run.py --yaml examples/05_error_handling.yaml "procesa esta solicitud"
```

---

### Human-in-the-loop
**`06_human_in_the_loop.yaml`**

La ejecución se **suspende** en el nodo `human_input` esperando confirmación antes de ejecutar una acción de alto impacto. `run.py` muestra el resumen y el nivel de impacto, y espera la respuesta del usuario antes de resumir.

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

## Tests

```bash
uv run pytest
uv run pytest agentflow/tests/test_condition_node.py -v
```

---

## Variables de entorno

Copiar `.env.example` a `.env` y configurar las API keys necesarias según el modelo usado en los grafos (`anthropic:...`, `openai:...`, etc.).
