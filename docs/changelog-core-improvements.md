# AgentFlow — Resumen de Mejoras Core

Este documento resume todos los cambios implementados en la sesión de mejoras del core de AgentFlow.
Cada ítem incluye el archivo afectado, la sección de SPEC.md que lo respalda y el estado de cobertura de tests.

---

## Nuevos Tipos de Nodo

### 1. NOTE
**Propósito**: Nodo de documentación embebida en el grafo. No produce output, no modifica estado, no emite eventos.
**Caso de uso**: Anotar limitaciones, workarounds o decisiones de diseño directamente en el YAML.

**Archivos modificados:**
- `agentflow/core/models.py` — `NodeType.NOTE` agregado al enum
- `agentflow/executors/registry.py` — mapeado a `_NoOpExecutor`
- `agentflow/core/compiler.py` — excluido de advertencias de alcanzabilidad

**SPEC**: No aplica (nodo utilitario sin comportamiento de negocio)

**Ejemplo**: `examples/11_evaluator_loop_with_set_state.yaml` — nodo `_nota_incremento`

---

### 2. SET_STATE
**Propósito**: Asigna literales o referencias a campos del estado de forma declarativa, sin LLM.
**Caso de uso**: Inicializar contadores antes de un loop, limpiar campos, marcar flags.

**Archivos modificados/creados:**
- `agentflow/core/models.py` — `NodeType.SET_STATE`, `SetStateNodeConfig`
- `agentflow/executors/set_state_executor.py` — nuevo executor
- `agentflow/executors/registry.py` — registrado
- `agentflow/core/compiler.py` — `_validate_set_state()` verifica campos contra `state_schema`

**SPEC**: §3.4

**Ejemplo**: `examples/07_set_state_and_transform.yaml`, `examples/11_evaluator_loop_with_set_state.yaml`

---

### 3. TRANSFORM
**Propósito**: Aplica operaciones declarativas sobre el estado sin invocar LLMs ni sistemas externos.
**Operaciones**: `set` (copia), `template` (interpolación por regex), `extract` (dot-notation en dict), `cast` (str/int/float/bool).

**Archivos modificados/creados:**
- `agentflow/core/models.py` — `NodeType.TRANSFORM`, `TransformOperation`, `TransformNodeConfig`
- `agentflow/executors/transform_executor.py` — nuevo executor con `_render_template()` basado en regex
- `agentflow/executors/registry.py` — registrado
- `agentflow/core/compiler.py` — `_validate_transform()` verifica operaciones

> **Nota de implementación**: La operación `template` usa sustitución por regex (`re.compile(r"\{(state\.[^}]+)\}")`) en lugar de `str.format_map()`. Esto es necesario porque Python interpreta `{state.field}` como acceso de atributo `state` en un format string, lo que causa `KeyError`. El regex permite referenciar cualquier `state.*` field de forma segura.

**SPEC**: §3.5

**Ejemplo**: `examples/07_set_state_and_transform.yaml`, `examples/10_node_timeout.yaml`

---

### 4. START
**Propósito**: Punto de entrada declarativo con contrato explícito de inputs. Opcionalmente expone el input como texto plano (`input_as_text`).
**Diferencia con `entry_node` normal**: Valida en compilación qué campos acepta y si están en `state_schema`.

**Archivos modificados/creados:**
- `agentflow/core/models.py` — `NodeType.START`, `StartInputField`, `StartNodeConfig`
- `agentflow/executors/registry.py` — mapeado a `_NoOpExecutor` (runtime avanza automáticamente)
- `agentflow/core/compiler.py` — `_validate_start()` verifica campos de `inputs` contra `state_schema`
- `agentflow/core/runtime.py` — manejo especial para inyectar `input_as_text` si `as_text: true`

**SPEC**: §2.6

**Ejemplo**: `examples/08_start_node.yaml`

---

### 5. GUARDRAIL
**Propósito**: Evalúa campos del estado contra criterios de seguridad. Produce `pass`/`fail` con razón. Si falla, redirige a `on_fail`.
**Checks disponibles**: `pii` (presidio-analyzer), `toxicity` (heurístico sin deps), `custom_llm` (LLM-as-judge).

**Archivos modificados/creados:**
- `agentflow/core/models.py` — `NodeType.GUARDRAIL`, `GuardrailCheckType`, `GuardrailCheck`, `GuardrailNodeConfig`
- `agentflow/executors/guardrail_executor.py` — nuevo executor con los tres tipos de check
- `agentflow/executors/registry.py` — registrado
- `agentflow/core/compiler.py` — `_validate_guardrail()`, `on_fail` incluido en BFS de alcanzabilidad
- `agentflow/core/runtime.py` — routing a `on_fail` cuando `state_updates["result"] == "fail"`

> **Nota de implementación**: El executor siempre preserva la clave `result` en su dict de retorno. Esto permite que el runtime detecte `"fail"` sin necesitar conocer el `output_mapping` configurado.

**SPEC**: §11

**Ejemplo**: `examples/09_guardrail.yaml`

---

## Mejoras a Nodos Existentes

### 6. Timeout por nodo (`timeout_seconds`)
**Propósito**: Interrumpe la coroutine de un nodo con `asyncio.wait_for()` si excede el límite. No es reintentable — se trata como fallo definitivo.
**Diferencia con `global_timeout_seconds`**: El timeout global verifica entre nodos; el timeout de nodo interrumpe mid-ejecución.

**Archivos modificados:**
- `agentflow/core/models.py` — `timeout_seconds: float | None` en `AgentNodeConfig`, `ToolNodeConfig`, `ParallelNodeConfig`
- `agentflow/core/runtime.py` — `NodeTimeoutError`, envuelve ejecución en `asyncio.wait_for()`; `NodeTimeoutError` es no-reintentable (sale del retry loop inmediatamente)
- `docs/runtime-guards.md` — actualizado con documentación de `timeout_seconds` por nodo; corregidos dos errores previos: `max_depth` default era 100 (real: 50), `on_error` estaba dentro de `config:` (debe estar en `NodeDefinition`)

**SPEC**: §5.4

**Ejemplo**: `examples/10_node_timeout.yaml`

---

### 7. `output_schema_inline` en AgentNode
**Propósito**: Define el schema de output del agente directamente en el YAML, sin necesitar un módulo Python externo con clases Pydantic.
**Implementación**: Genera un modelo Pydantic dinámico con `pydantic.create_model()` en tiempo de ejecución. Soporta `type: str|int|float|bool|list|dict` y `enum: [...]` → `Literal[tuple(values)]`.

**Archivos modificados:**
- `agentflow/core/models.py` — `output_schema_inline: dict[str, Any] | None` en `AgentNodeConfig`
- `agentflow/executors/agent_executor.py` — `_build_inline_schema()` con `create_model()`; tiene prioridad sobre `output_schema`
- `agentflow/core/compiler.py` — `_check_agent_inline_schema()` rechaza uso simultáneo de `output_schema` + `output_schema_inline`

**SPEC**: No tiene sección propia (extensión del contrato de escritura §3.1)

**Ejemplo**: `examples/07_set_state_and_transform.yaml`, `examples/08_start_node.yaml`, `examples/09_guardrail.yaml`, `examples/10_node_timeout.yaml`, `examples/11_evaluator_loop_with_set_state.yaml`

---

## Nuevas Capacidades de Infraestructura

### 8. CEL como lenguaje de condición alternativo
**Propósito**: Permite expresiones más complejas en edges sin romper la seguridad del DSL existente. CEL es opt-in por edge.
**Comportamiento en runtime**: Si la evaluación falla, se trata como `False` y se emite evento `warning.cel_evaluation_error`.

**Archivos modificados/creados:**
- `agentflow/core/models.py` — `condition_cel: str | None`, `condition_language: Literal["dsl","cel"] = "dsl"` en `Edge`
- `agentflow/dsl/condition_parser.py` — `evaluate_cel_condition()` con `cel-python`; `ImportError` → `ConditionEvaluationError`
- `agentflow/core/compiler.py` — detección de ambigüedad DSL+CEL simultáneos, validación de sintaxis CEL en compilación (warning si `cel-python` no instalado)
- `agentflow/core/runtime.py` — rama CEL en `_resolve_transition()`
- `agentflow/core/observability.py` — `warn_cel_evaluation_error(from_node, expr)`

**SPEC**: §4.4

---

### 9. ExecutionTrace
**Propósito**: Objeto de primera clase que agrega todos los registros de una ejecución para evaluación y auditoría.
**Contenido**: `execution_id`, `graph_id`, `graph_version`, `status`, `duration_ms`, `total_tokens`, `node_records`, `checkpoints`, `final_state`.

**Archivos modificados:**
- `agentflow/core/models.py` — clase `ExecutionTrace`
- `agentflow/core/state_manager.py` — `get_trace()` en el protocolo e implementación `InMemoryStateManager`; agrega `_execution_start_times` para computar `duration_ms`

**SPEC**: §7.4

---

### 10. GraderRunner
**Propósito**: Evalúa `ExecutionTrace` con tres tipos de graders para pipelines de quality assurance.

| Tipo | Descripción |
|------|-------------|
| `deterministic` | DSL sobre `final_state`. Score: 1.0 (pass) / 0.0 (fail) |
| `llm_judge` | Pydantic AI agent con prompt configurable. Score: 0.0–1.0 |
| `heuristic` | Umbrales sobre métricas: `max_tokens`, `max_duration_ms`, `max_retries` |

**Archivos creados:**
- `agentflow/core/grader.py` — `GraderRunner` con `_grade_deterministic()`, `_grade_llm_judge()`, `_grade_heuristic()`
- `agentflow/core/models.py` — `GraderType`, `Grader`, `GradeResult`
- `agentflow/core/state_manager.py` — `save_grade()`, `get_grades()` en protocolo e implementación

**SPEC**: §12

---

### 11. Corrección de alcanzabilidad en el compilador
**Propósito**: El BFS de alcanzabilidad ignoraba edges implícitos — nodos referenciados por `on_error`, branches de ConditionNode y `on_fail` de GuardrailNode no se incluían en la exploración. Esto causaba falsos positivos de "nodo inalcanzable" en ejemplos 07–11.

**Fix**: `_check_reachability()` ahora construye los adjacency sets incluyendo `on_error`, todos los targets de branches de ConditionNode (incluyendo `default`) y el `on_fail` de GuardrailNode antes de iniciar el BFS.

**Archivos modificados:**
- `agentflow/core/compiler.py` — `_check_reachability()` extendido

**SPEC**: §2.3 (alcanzabilidad)

---

## Nuevos Ejemplos

| Archivo | Patrón demostrado |
|---------|-------------------|
| `examples/07_set_state_and_transform.yaml` | `set_state` para inicializar estado, `transform` con template y cast, `output_schema_inline` |
| `examples/08_start_node.yaml` | Nodo `start` con contrato de inputs, `note` como documentación, routing por intención |
| `examples/09_guardrail.yaml` | Guardrail con `toxicity` + `custom_llm`, `on_fail` routing, `output_mapping` para auditoría |
| `examples/10_node_timeout.yaml` | `timeout_seconds` por nodo, circuit breaker con `on_error`, `transform` para campo de display |
| `examples/11_evaluator_loop_with_set_state.yaml` | Variante del evaluator loop usando `set_state` + `note` para documentar limitación aritmética |
| `examples/tools.py` | Herramientas de ejemplo: `fetch_product_from_api` (lenta, configurable via `SIMULATE_SLOW_API=true`) y `fetch_product_from_cache` |

---

## Nuevos Tests

| Archivo | Cobertura |
|---------|-----------|
| `agentflow/tests/test_new_node_types.py` | NOTE, SET_STATE, TRANSFORM, START passthrough, `output_schema_inline`, timeout de nodo, compilador con nuevos tipos |
| `agentflow/tests/test_guardrail.py` | GuardrailExecutor: toxicity pass/fail, custom_llm mock, PII mock, routing `on_fail` en runtime |
| `agentflow/tests/test_grader.py` | GraderRunner: deterministic, heuristic, llm_judge (mock) |
| `agentflow/tests/test_cel_conditions.py` | CEL: evaluación correcta, error de sintaxis en compilación, fallback a False en runtime, evento de advertencia |

---

## Cambios en SPEC.md

Secciones nuevas agregadas durante esta sesión:

| Sección | Contenido |
|---------|-----------|
| §2.6 | Contrato de entrada — StartNode |
| §3.4 | SET_STATE |
| §3.5 | TRANSFORM (regex template, no eval) |
| §4.4 | CEL como lenguaje de condición alternativo |
| §5.4 | Timeout por nodo |
| §7.4 | ExecutionTrace |
| §11 | Guardrails (11.1–11.4) |
| §12 | Graders y Evaluación (12.1–12.3) |

Correcciones a SPEC.md:

- **§3.5**: Corregida descripción de `template` — era `str.format_map()`, ahora documenta correctamente la implementación por regex.
- **§5.4**: Eliminada mención de evento `node.timeout` que no existe. El timeout se registra vía `node.failed` con razón `NodeTimeoutError`.

---

## Dependencias opcionales agregadas

| Paquete | Para qué |
|---------|----------|
| `cel-python` | Evaluación de condiciones CEL (opt-in) |
| `presidio-analyzer` | Check PII en GuardrailNode (opt-in) |

Ambas dependencias son opcionales. Si no están instaladas, la funcionalidad correspondiente emite un error claro en lugar de fallar silenciosamente.
