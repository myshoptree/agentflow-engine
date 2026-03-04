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
└── schemas.py                     # Pydantic output schemas de los ejemplos

run.py                             # Script de demo CLI
```

---

## Patrones soportados

| Patrón | Implementación |
|--------|---------------|
| **Router** | AgentNode clasifica → edges con `EdgeCondition` según `state.intent` |
| **Supervisor** | AgentNode cuyo output determina la rama del grafo |
| **Evaluator Loop** | Edge de retorno condicional en score + `max_depth` como guardia |
| **Tool-Orchestrated** | AgentNode con tools Pydantic AI; el agente decide qué tool llamar |
| **Human-in-the-loop** | `human_input` node suspende → API resume con input externo |

---

## Tests

```bash
uv run pytest
uv run pytest agentflow/tests/test_condition_node.py -v
```

---

## Variables de entorno

Copiar `.env.example` a `.env` y configurar las API keys necesarias según el modelo usado en los grafos (`anthropic:...`, `openai:...`, etc.).
