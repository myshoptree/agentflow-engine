# Tipos de Nodos en AgentFlow

## Resumen

| Tipo | Executor | Config Model | Descripción |
|------|----------|--------------|-------------|
| `agent` | `AgentExecutor` | `AgentNodeConfig` | Ejecuta LLM via Pydantic AI, produce output estructurado |
| `condition` | `ConditionExecutor` | `ConditionNodeConfig` | Evalúa condiciones AND/OR sobre estado, sin LLM |
| `tool` | `ToolExecutor` | `ToolNodeConfig` | Ejecuta herramienta externa (API, DB, webhook) |
| `parallel` | `ParallelExecutor` | `ParallelNodeConfig` | Ejecuta ramas en paralelo con `asyncio.gather` |
| `human_input` | `_HumanInputExecutor` | `HumanInputNodeConfig` | Suspende ejecución esperando input externo |
| `end` | `_EndExecutor` | `EndNodeConfig` | Finaliza ejecución → COMPLETED |
| `set_state` | `SetStateExecutor` | `SetStateNodeConfig` | Asigna literales o referencias al estado sin LLM |
| `transform` | `TransformExecutor` | `TransformNodeConfig` | Operaciones declarativas: set, template, extract, cast |
| `start` | `_NoOpExecutor` | `StartNodeConfig` | Punto de entrada declarativo con contrato de inputs |
| `guardrail` | `GuardrailExecutor` | `GuardrailNodeConfig` | Evalúa checks de seguridad con routing `on_fail` |

---

## Detalle por Tipo

### `agent`

Ejecuta un LLM usando Pydantic AI y produce un output estructurado validado.

```yaml
classifier:
  type: agent
  config:
    model: "anthropic:claude-sonnet-4-6"
    system_prompt: "Clasifica el intent del usuario"
    output_schema_inline:
      intent:
        type: str
        enum: ["HOT", "WARM", "COLD"]
      confidence:
        type: float
        default: 0.0
    state_output_mapping:
      intent: intent
      confidence: confidence
```

**Campos de configuración (`AgentNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `model` | `str` | `"anthropic:claude-sonnet-4-6"` | Modelo LLM a usar |
| `system_prompt` | `str` | `""` | Prompt del sistema |
| `output_schema` | `str \| None` | `None` | Ruta fully-qualified al modelo Pydantic |
| `output_schema_inline` | `dict \| None` | `None` | Schema definido inline (ver B4) |
| `state_output_mapping` | `dict[str, str]` | `{}` | Mapeo `{state_field: output_field}` |
| `tools` | `list[str]` | `[]` | Lista de herramientas disponibles |
| `retry_policy` | `RetryPolicy \| None` | `None` | Política de reintentos |
| `timeout_seconds` | `float \| None` | `None` | Timeout por nodo (SPEC §5.4) |

---

### `condition`

Evalúa condiciones sobre el estado sin usar LLM. Soporta condiciones compuestas AND/OR.

```yaml
router:
  type: condition
  config:
    branches:
      - condition:
          operator: and
          conditions:
            - field: "state.intent"
              operator: eq
              value: "HOT"
            - field: "state.confidence"
              operator: gte
              value: 0.8
        target: vip_handler
      - condition:
          field: "state.intent"
          operator: eq
          value: "WARM"
        target: warm_handler
    default: cold_handler
```

**Campos de configuración (`ConditionNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `branches` | `list[ConditionBranch]` | `[]` | Lista de ramas condicionales |
| `default` | `str \| None` | `None` | Nodo destino si ninguna rama coincide |

**Operadores disponibles:**
- `eq`, `neq` — igualdad
- `gt`, `lt`, `gte`, `lte` — comparación numérica
- `in` — valor en lista
- `contains` — lista contiene valor
- `is_null` — valor es null

---

### `tool`

Ejecuta una herramienta externa registrada (API, DB, webhook).

```yaml
fetch_user:
  type: tool
  config:
    tool_name: "get_user_info"
    input_mapping:
      user_id: "state.user_id"
    output_mapping:
      user_name: "name"
      user_email: "email"
  on_error: fallback_handler
```

**Campos de configuración (`ToolNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `tool_name` | `str` | — | Nombre de la herramienta registrada |
| `input_mapping` | `dict[str, str]` | `{}` | Mapeo `{tool_param: state_field}` |
| `output_mapping` | `dict[str, str]` | `{}` | Mapeo `{state_field: tool_output}` |
| `on_error` | `str \| None` | `None` | Nodo destino en caso de error |
| `retry_policy` | `RetryPolicy \| None` | `None` | Política de reintentos |
| `timeout_seconds` | `float \| None` | `None` | Timeout por nodo |

---

### `parallel`

Ejecuta múltiples ramas en paralelo usando `asyncio.gather`.

```yaml
enrich_user:
  type: parallel
  config:
    branches:
      - fetch_profile
      - fetch_orders
      - fetch_preferences
    output_mapping:
      profile: "profile"
      orders: "orders"
      preferences: "preferences"
```

**Campos de configuración (`ParallelNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `branches` | `list[str]` | — | Lista de node_ids a ejecutar en paralelo |
| `output_mapping` | `dict[str, str]` | `{}` | Mapeo de resultados mergeados |
| `timeout_seconds` | `float \| None` | `None` | Timeout global del paralelo |

**Merge strategy:** En conflicto de claves, el último branch en la lista gana.

---

### `human_input`

Suspende la ejecución esperando input externo del usuario.

```yaml
confirm_order:
  type: human_input
  config:
    prompt: "¿Confirmas la compra?"
    input_mapping:
      confirmation: "user_response"
```

**Campos de configuración (`HumanInputNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `prompt` | `str` | `""` | Mensaje mostrado al usuario |
| `input_mapping` | `dict[str, str]` | `{}` | Mapeo `{state_field: input_key}` |
| `timeout_seconds` | `float \| None` | `None` | Timeout de espera |

---

### `end`

Finaliza la ejecución con estado COMPLETED.

```yaml
done:
  type: end
  config: {}
```

**Campos de configuración (`EndNodeConfig`):** Sin campos.

#### ¿Es obligatorio tener un nodo END?

**No es obligatorio** en el compilador, pero **sí es la forma correcta** de terminar una ejecución en `COMPLETED`.

| Escenario | Resultado | Descripción |
|-----------|-----------|-------------|
| Llega a nodo `END` | `COMPLETED` | Terminación exitosa con checkpoint final |
| Nodo sin transición válida | `FAILED` | Error: "No valid transition from node 'xxx'" |
| `START` sin outgoing edge | `FAILED` | Error de configuración |
| `max_depth` excedido | `FAILED` | Guardia anti-loop activada |
| `global_timeout` excedido | `TIMED_OUT` | Timeout global de la ejecución |
| Error sin `on_error` | `FAILED` | Reintentos agotados sin handler |

#### Ejemplo correcto

```yaml
nodes:
  entry:
    type: start
    config:
      inputs:
        - name: user_input
          type: str
          as_text: true

  classifier:
    type: agent
    config:
      model: "anthropic:claude-sonnet-4-6"
      system_prompt: "Clasifica el intent"
      output_schema_inline:
        intent: { type: str, enum: ["A", "B"] }
      state_output_mapping:
        intent: intent

  done:                    # ← Nodo END explícito
    type: end
    config: {}

edges:
  - from_node: entry
    to_node: classifier

  - from_node: classifier
    to_node: done          # ← Termina en END
```

---

### `set_state`

Asigna valores literales o referencias al estado sin usar LLM.

```yaml
init_state:
  type: set_state
  config:
    assignments:
      attempt_count: 0
      max_attempts: 3
      current_step: "state.previous_step"  # referencia
```

**Campos de configuración (`SetStateNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `assignments` | `dict[str, Any]` | `{}` | Mapeo `{state_field: value_or_ref}` |

**Referencias:** Si el valor empieza con `state.`, se resuelve desde el estado actual.

---

### `transform`

Operaciones declarativas de transformación de datos sin LLM.

```yaml
format_name:
  type: transform
  config:
    operations:
      - set: "full_name"
        template: "{state.first_name} {state.last_name}"
      - set: "email_domain"
        from_field: "state.email"
        extract: "domain"
      - set: "age"
        from_field: "state.age_str"
        cast: "int"
```

**Campos de configuración (`TransformNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `operations` | `list[TransformOperation]` | `[]` | Lista de operaciones |

**Operación (`TransformOperation`):**

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `set` | `str` | Campo destino en el estado |
| `from_field` | `str \| None` | Campo origen (`state.xxx`) |
| `template` | `str \| None` | Template con placeholders `{state.xxx}` |
| `extract` | `str \| None` | Path dot-notation dentro del valor |
| `cast` | `str \| None` | Cast de tipo: `str`, `int`, `float`, `bool` |

---

### `start`

Punto de entrada declarativo con contrato explícito de inputs.

```yaml
entry:
  type: start
  config:
    inputs:
      - name: "user_input"
        type: str
        as_text: true
      - name: "session_id"
        type: str
```

**Campos de configuración (`StartNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `inputs` | `list[StartInputField]` | `[]` | Lista de inputs esperados |

**Input field (`StartInputField`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `name` | `str` | — | Nombre del campo |
| `type` | `StateFieldType` | `str` | Tipo del campo |
| `as_text` | `bool` | `False` | Si true, inyecta en `input_as_text` |

---

### `guardrail`

Evalúa campos del estado contra checks de seguridad con routing `on_fail`.

```yaml
safety_check:
  type: guardrail
  config:
    checks:
      - type: toxicity
        field: "state.user_input"
      - type: pii
        field: "state.user_input"
      - type: custom_llm
        field: "state.response"
        prompt: "¿Esta respuesta es apropiada?"
        model: "anthropic:claude-haiku-4-5-20251001"
    on_fail: moderation_handler
    output_mapping:
      guard_result: "result"
      guard_reason: "reason"
```

**Campos de configuración (`GuardrailNodeConfig`):**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `checks` | `list[GuardrailCheck]` | — | Lista de checks de seguridad |
| `on_fail` | `str` | — | Nodo destino si algún check falla |
| `output_mapping` | `dict[str, str]` | `{}` | Mapeo `{state_field: "result"\|"reason"}` |

**Tipos de check (`GuardrailCheckType`):**
- `pii` — Detección de PII (requiere `presidio-analyzer`)
- `toxicity` — Detección de toxicidad
- `custom_llm` — Evaluación personalizada con LLM

---

## Flujo de Ejecución por Tipo

| Nodo | Runtime Handling | Executor | Output |
|------|------------------|----------|--------|
| `START` | Inyecta `input_as_text`, avanza | `_NoOpExecutor` | `{}` |
| `END` | Checkpoint, `COMPLETED` | `_EndExecutor` | `{}` |
| `HUMAN_INPUT` | Suspende (`SUSPENDED`), espera `resume()` | `_HumanInputExecutor` | — |
| `AGENT` | Dispatch → executor | `AgentExecutor` | `state_output_mapping` |
| `TOOL` | Dispatch → executor | `ToolExecutor` | `output_mapping` |
| `CONDITION` | Dispatch → executor, runtime resuelve rama | `ConditionExecutor` | `{}` |
| `PARALLEL` | Dispatch → executor | `ParallelExecutor` | merge de branches |
| `SET_STATE` | Dispatch → executor | `SetStateExecutor` | `assignments` |
| `TRANSFORM` | Dispatch → executor | `TransformExecutor` | resultados de operaciones |
| `GUARDRAIL` | Dispatch → executor, runtime chequea `result` | `GuardrailExecutor` | `{result, reason}` |

---

## Ubicación en el Código

```
agentflow/
├── core/
│   └── models.py              # NodeType enum + *Config models
│
├── executors/
│   ├── registry.py            # get_executor(node_type) → executor
│   ├── agent_executor.py      # AGENT
│   ├── condition_executor.py  # CONDITION
│   ├── tool_executor.py       # TOOL
│   ├── parallel_executor.py   # PARALLEL
│   ├── set_state_executor.py  # SET_STATE
│   ├── transform_executor.py  # TRANSFORM
│   └── guardrail_executor.py  # GUARDRAIL
```

---

## Referencias SPEC

- §3.1 — AgentNode output_schema
- §3.4 — SET_STATE node
- §3.5 — TRANSFORM node
- §4 — ConditionNode y DSL de condiciones
- §5 — ToolNode y on_error routing
- §6 — HumanInputNode y suspensión
- §11 — GuardrailNode y checks de seguridad
- SPEC B2 — START node (contrato de inputs)
- SPEC B4 — output_schema_inline
