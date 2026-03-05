# Plan de Implementación — Core Improvements

Basado en `roadmap-core-improvements.md`. Las mejoras están ordenadas por la priorización del roadmap: impacto/esfuerzo. Cada ítem incluye los archivos afectados, las reglas de SPEC involucradas y los criterios de aceptación.

---

## Reglas antes de empezar

1. Revisar `SPEC.md` antes de cada ítem.
2. Si un comportamiento nuevo no está en SPEC, detener e informar antes de implementar.
3. Los tests deben pasar (`uv run pytest`) antes y después de cada ítem.
4. Nunca usar `eval()` en ninguna expresión evaluada en runtime.

---

## Fase A — Impacto alto, esfuerzo bajo

### A1. `NodeType.NOTE`

**Objetivo:** Nodo que el runtime ignora completamente — solo sirve para documentar el YAML.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `NOTE = "note"` a `NodeType`
  - Agregar `NoteNodeConfig(BaseModel)` con campo `text: str = ""`
  - Agregar `NoteNodeConfig` al union `NodeConfig`
  - Agregar entrada `NodeType.NOTE: NoteNodeConfig` en `NodeDefinition.get_typed_config()`
- `agentflow/executors/registry.py`
  - Agregar rama `if node_type == NodeType.NOTE` que retorna un `_NoteExecutor` (no-op)
- `agentflow/core/runtime.py`
  - Antes del dispatch al executor, si `node_type == NOTE`: saltar el nodo y resolver la transición normalmente (sin emitir `node.started` / `node.completed` — es decorativo)
- `agentflow/core/compiler.py`
  - Los nodos `NOTE` no participan en validación de alcanzabilidad ni en detección de ciclos (son transparentes al compilador)

**SPEC afectada:** Ninguna regla existente — el nodo es invisible al runtime. No requiere nueva sección.

**Criterio de aceptacion:**
- Un grafo con un nodo `type: note` compila sin errores
- La ejecución lo omite sin cambiar el estado ni emitir eventos
- Un test en `test_compiler.py` verifica que el nodo NOTE en un grafo inalcanzable no genera error de alcanzabilidad

---

### A2. Timeout por nodo

**Objetivo:** Cada nodo puede declarar `timeout_seconds` en su config. Si el executor tarda más, el nodo falla con `NodeTimeoutError`.

**SPEC afectada:** §5 (Fallos y Recuperación). Requiere agregar §5.4 antes de implementar:

> **§5.4 Timeout por nodo**
> - Un nodo puede declarar `timeout_seconds` en su config. Si la ejecución del nodo supera ese tiempo, se trata como un fallo no recuperable del nodo (equivalente a reintentos agotados).
> - El timeout por nodo no cancela el timeout global (§5.3) — ambos aplican de forma independiente.
> - Un timeout de nodo sin `on_error` definido termina la ejecución en `FAILED`.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `timeout_seconds: float | None = None` a `AgentNodeConfig`, `ToolNodeConfig`, `ParallelNodeConfig`
- `agentflow/core/runtime.py`
  - En el dispatch del executor, wrappear la llamada con `asyncio.wait_for(executor.execute(...), timeout=node_timeout)` si `timeout_seconds` está definido
  - Capturar `asyncio.TimeoutError` y tratarlo como fallo del nodo (activar `on_error` o terminar en FAILED)
  - Emitir evento `node.timeout` con `node_id`, `timeout_seconds`

**Criterio de aceptación:**
- Un nodo con `timeout_seconds: 0.001` falla por timeout en tests
- El `on_error` del nodo se activa correctamente
- El timeout global sigue funcionando independientemente
- Test en `test_runtime.py`

---

### A3. `NodeType.SET_STATE`

**Objetivo:** Nodo declarativo que escribe valores fijos o referencias al estado, sin LLM ni código externo.

**SPEC afectada:** §3.1 (Escritura controlada). El SET_STATE solo puede escribir los campos declarados en sus `assignments`. Requiere agregar a §3:

> **§3.4 SET_STATE**
> - Un nodo `set_state` escribe directamente al `graph_state` los campos declarados en `assignments`.
> - Los valores pueden ser literales o referencias a campos del estado actual usando notación `state.field`.
> - Solo puede escribir campos declarados en el `state_schema` del grafo.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `SET_STATE = "set_state"` a `NodeType`
  - Agregar `SetStateNodeConfig(BaseModel)` con `assignments: dict[str, Any]`
  - Agregar al union `NodeConfig` y a `get_typed_config()`
- `agentflow/executors/set_state_executor.py` (nuevo)
  - `SetStateExecutor.execute()`: itera `assignments`, resuelve referencias `state.*` del `graph_state`, retorna el dict de salida
- `agentflow/executors/registry.py`
  - Agregar `NodeType.SET_STATE → SetStateExecutor`
- `agentflow/core/compiler.py`
  - Validar que los campos en `assignments` existen en `state_schema`
  - Validar que referencias `state.*` apuntan a campos existentes en `state_schema`

**Criterio de aceptación:**
- Un nodo SET_STATE escribe `retry_count: 0` al estado antes de un loop
- Una referencia `previous_intent: "state.intent"` copia el valor actual de `intent`
- El compilador rechaza campos no declarados en `state_schema`
- Tests en `test_runtime.py`

---

## Fase B — Impacto alto, esfuerzo medio

### B1. `NodeType.TRANSFORM`

**Objetivo:** Nodo que reshapea datos entre nodos — sin LLM, sin I/O externo. Soporta `set`, `template`, `extract`, `cast`.

**SPEC afectada:** §3.1. Requiere agregar a §3:

> **§3.5 TRANSFORM**
> - Un nodo `transform` aplica operaciones declarativas sobre el estado sin invocar LLMs ni sistemas externos.
> - Las operaciones permitidas son: `set` (copia directa), `template` (interpolación de strings), `extract` (subcampo de dict/list), `cast` (conversión de tipo).
> - El nodo es determinista: mismo estado de entrada produce siempre el mismo estado de salida.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `TRANSFORM = "transform"` a `NodeType`
  - Agregar modelos:
    ```python
    class TransformOperation(BaseModel):
        set: str                        # campo destino
        from_field: str | None = None   # "state.some_field"
        template: str | None = None     # "{state.first} {state.last}"
        extract: str | None = None      # dot-path dentro del valor
        cast: Literal["str","int","float","bool"] | None = None

    class TransformNodeConfig(BaseModel):
        operations: list[TransformOperation]
    ```
  - Agregar al union `NodeConfig` y a `get_typed_config()`
- `agentflow/executors/transform_executor.py` (nuevo)
  - `TransformExecutor.execute()`: evalúa cada operación en orden, retorna dict de salida
  - `template` usa `str.format_map()` con el estado — nunca `eval()`
  - `extract` usa dot-notation segura (mismo helper que `condition_parser.py`)
- `agentflow/executors/registry.py`
  - Agregar `NodeType.TRANSFORM → TransformExecutor`
- `agentflow/core/compiler.py`
  - Validar que los campos `set` existen en `state_schema`
  - Validar que referencias `from_field` y `extract` apuntan a campos válidos

**Criterio de aceptación:**
- Operación `set` copia un campo de estado a otro
- Operación `template` construye un string con `{state.first_name} {state.last_name}`
- Operación `cast` convierte `"42"` a `42` (int)
- El compilador rechaza campos inexistentes
- Tests en `agentflow/tests/test_transform_executor.py` (nuevo)

---

### B2. `StartNodeConfig` explícito

**Objetivo:** El nodo de entrada del grafo declara sus inputs de forma tipada. El compilador valida que esos campos existen en `state_schema`.

**SPEC afectada:** §2.2 (Entrada única). Agregar:

> **§2.6 Contrato de entrada**
> - Si el `entry_node` es de tipo `start`, debe declarar explícitamente los campos de input que acepta en su `config.inputs`.
> - El compilador valida que cada campo declarado en `inputs` existe en el `state_schema`.
> - Si se declara `as_text: true` en un campo de tipo `str`, ese campo se expone automáticamente como `input_as_text` en el estado.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `START = "start"` a `NodeType`
  - Agregar modelos:
    ```python
    class StartInputField(BaseModel):
        name: str
        type: StateFieldType
        as_text: bool = False   # si True, también disponible como input_as_text

    class StartNodeConfig(BaseModel):
        inputs: list[StartInputField] = []
    ```
  - Agregar al union `NodeConfig` y a `get_typed_config()`
- `agentflow/core/compiler.py`
  - Si `entry_node` es de tipo `start`, validar que cada `input.name` existe en `state_schema`
  - Si algún input tiene `as_text: True`, verificar que `input_as_text` está en `state_schema`
- `agentflow/core/runtime.py`
  - Al iniciar la ejecución, si el `entry_node` es `start`: inyectar `input_as_text` al estado si corresponde, luego avanzar al siguiente nodo (el START no tiene executor propio — es un punto de entrada declarativo)
- `agentflow/executors/registry.py`
  - Agregar `NodeType.START` con un executor no-op (el runtime avanza al siguiente edge)

**Criterio de aceptación:**
- Un grafo con `entry_node: start` + `type: start` compila correctamente
- El compilador rechaza si `user_message` no está en `state_schema`
- El campo `input_as_text` se inyecta correctamente en el estado
- Tests en `test_compiler.py`

---

### B3. `ExecutionTrace` como objeto de primera clase

**Objetivo:** Agrupar `NodeExecutionRecord`s, `StateCheckpoint`s y metadata de la ejecución en un objeto `ExecutionTrace` consultable.

**SPEC afectada:** §7 (Observabilidad). Agregar §7.4:

> **§7.4 ExecutionTrace**
> - Al completar una ejecución (cualquier estado terminal), el sistema debe poder producir un `ExecutionTrace` que agrupe todos los registros de la ejecución.
> - El trace incluye: `execution_id`, `graph_id`, `graph_version`, `status`, `duration_ms`, `total_tokens`, `node_records`, `checkpoints`, `final_state`.
> - El trace es inmutable una vez que la ejecución llega a estado terminal.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `ExecutionTrace(BaseModel)` con los campos definidos en el roadmap
- `agentflow/core/state_manager.py`
  - Agregar `get_trace(execution_id: str) -> ExecutionTrace` al protocolo `StateManagerProtocol`
  - Implementar en `InMemoryStateManager`: agregar acumulación de `NodeExecutionRecord` durante la ejecución, construir `ExecutionTrace` al finalizar
- `agentflow/core/runtime.py`
  - Al completar/fallar cada nodo, guardar `NodeExecutionRecord` en el state manager
  - Al terminar la ejecución, llamar a `state_manager.finalize_trace(execution_id)`

**Criterio de aceptación:**
- `state_manager.get_trace(execution_id)` retorna un `ExecutionTrace` con todos los nodos ejecutados
- `total_tokens` suma los tokens de todos los `AgentNode` ejecutados
- `duration_ms` refleja el tiempo total de la ejecución
- Tests en `test_runtime.py`

---

### B4. Output schema inline

**Objetivo:** Permitir definir el schema de output de un `AgentNode` directamente en YAML, sin requerir un módulo Python externo.

**SPEC afectada:** Ninguna sección existente aplica directamente. No requiere cambio en SPEC — es una mejora de ergonomía del compilador.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar a `AgentNodeConfig`:
    ```python
    output_schema_inline: dict[str, Any] | None = None  # definición inline del schema
    ```
- `agentflow/core/compiler.py`
  - Validar que `output_schema` y `output_schema_inline` no se usen simultáneamente
  - Nuevo método `_build_inline_schema(spec: dict) -> type[BaseModel]` que genera el modelo Pydantic dinámicamente usando `create_model()` de Pydantic v2
- `agentflow/executors/agent_executor.py`
  - Si `output_schema_inline` está definido, usar el modelo generado por el compilador en lugar de importar por nombre

**Tipos soportados en inline:**
```yaml
output_schema_inline:
  intent:
    type: str
    enum: ["HOT", "WARM", "COLD"]
  confidence:
    type: float
```

**Criterio de aceptación:**
- Un AgentNode con `output_schema_inline` funciona igual que con `output_schema`
- Los `enum` restringen los valores del campo (validación Pydantic)
- El compilador rechaza si se usan ambos (`output_schema` + `output_schema_inline`)
- Test que ejecuta un grafo completo con schema inline

---

### B5. `execution_id` explícito en creación

**Objetivo:** Permitir crear una ejecución con un `execution_id` predefinido para correlación con sistemas externos. SPEC §9.3 ya lo menciona pero no está implementado.

**Archivos a modificar:**
- `agentflow/core/state_manager.py`
  - `create_execution(graph_id, graph_version, initial_input, execution_id: str | None = None)`
  - Si se provee `execution_id` y ya existe → `ValueError` (SPEC §9.3)
- `agentflow/core/runtime.py`
  - `execute(compiled_graph, initial_input, execution_id: str | None = None)` — pasar al state manager

**Criterio de aceptación:**
- Crear ejecución con ID específico: el `execution_id` retornado es el provisto
- Crear con el mismo ID dos veces: segunda llamada lanza error
- Test en `test_runtime.py`

---

## Fase C — Impacto alto, esfuerzo alto

### C1. `NodeType.GUARDRAIL`

**Objetivo:** Nodo que evalúa el output de nodos anteriores contra criterios de seguridad (PII, toxicity, custom LLM-as-judge). Routing configurable en pass/fail.

**SPEC afectada:** Requiere nueva sección §11 antes de implementar. Borrador:

> **§11. Guardrails**
> **§11.1 Propósito**
> - Un nodo `guardrail` evalúa campos del estado contra criterios de seguridad declarados. No genera output de negocio — solo produce un resultado `pass` o `fail` con razón.
>
> **§11.2 Checks soportados**
> - `pii`: detecta Información Personal Identificable en el valor del campo.
> - `toxicity`: detecta contenido tóxico o dañino.
> - `custom_llm`: LLM-as-judge — evalúa el campo con un prompt configurable. La respuesta debe ser `yes` (falla) o `no` (pasa).
>
> **§11.3 Routing**
> - Si todos los checks pasan: la ejecución continúa por el edge normal del nodo.
> - Si algún check falla: la ejecución se redirige a `on_fail` (node_id). Si `on_fail` no está definido, la ejecución termina en `FAILED`.
> - El resultado (`pass`/`fail`) y la razón se escriben al estado en los campos declarados en `output_mapping`.
>
> **§11.4 Provider-agnostic**
> - Los checks deben funcionar sin depender de proveedores específicos (no OpenAI Moderation). El check `pii` puede usar `presidio-analyzer`. El check `custom_llm` usa el modelo configurado en el nodo.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `GUARDRAIL = "guardrail"` a `NodeType`
  - Agregar modelos:
    ```python
    class GuardrailCheckType(str, Enum):
        PII = "pii"
        TOXICITY = "toxicity"
        CUSTOM_LLM = "custom_llm"

    class GuardrailCheck(BaseModel):
        type: GuardrailCheckType
        field: str               # campo del estado a evaluar
        prompt: str | None = None  # solo para custom_llm
        model: str | None = None   # modelo para custom_llm

    class GuardrailNodeConfig(BaseModel):
        checks: list[GuardrailCheck]
        on_fail: str             # node_id destino si falla
        output_mapping: dict[str, str] = {}  # {state_field: "result"/"reason"}
    ```
  - Agregar al union y a `get_typed_config()`
- `agentflow/executors/guardrail_executor.py` (nuevo)
  - `GuardrailExecutor.execute()`: ejecuta cada check en orden, retorna `{result: "pass"|"fail", reason: str}`
  - Check PII: intentar importar `presidio_analyzer`; si no está disponible, lanzar error de configuración claro
  - Check custom_llm: usar Pydantic AI con prompt configurable
- `agentflow/executors/registry.py`
  - Agregar `NodeType.GUARDRAIL → GuardrailExecutor`
- `agentflow/core/runtime.py`
  - Después de ejecutar un nodo GUARDRAIL, verificar el resultado y redirigir a `on_fail` si aplica (antes de la resolución normal de transición)
- `agentflow/core/compiler.py`
  - Validar que `on_fail` referencia un nodo existente
  - Validar que los `field` en checks existen en `state_schema`

**Dependencias externas:**
- `presidio-analyzer` (opcional para check PII) — agregar a `pyproject.toml` como dependencia opcional
- El `custom_llm` check usa `pydantic-ai` (ya en el proyecto)

**Criterio de aceptación:**
- Un guardrail con check PII detecta un email en el output y redirige a `on_fail`
- Un guardrail con `custom_llm` usa un LLM para evaluar el campo
- Si PII no está instalado, el error es claro y descriptivo
- Tests en `agentflow/tests/test_guardrail.py` (nuevo) — con mocks para PII y LLM

---

### C2. Graders + API de Traces

**Objetivo:** Sistema de evaluación de traces. Requiere que B3 (`ExecutionTrace`) esté completado.

**SPEC afectada:** Agregar §12 antes de implementar:

> **§12. Graders y Evaluación**
> **§12.1 Propósito**
> - Un grader evalúa un `ExecutionTrace` y asigna un score o label. Es un contrato de calidad sobre ejecuciones pasadas.
>
> **§12.2 Tipos de grader**
> - `deterministic`: compara `final_state[field]` contra un valor esperado usando el DSL de condiciones existente.
> - `llm_judge`: un LLM evalúa el trace completo usando un prompt configurable. Retorna score 0.0–1.0.
> - `heuristic`: reglas sobre métricas del trace (tokens totales, duración, número de reintentos).
>
> **§12.3 Inmutabilidad**
> - Los graders no modifican el trace. Producen un `GradeResult` asociado al trace, no parte de él.

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar `GraderType`, `Grader`, `GradeResult` (ver roadmap §4.3)
- `agentflow/core/grader.py` (nuevo)
  - `GraderRunner.grade(trace: ExecutionTrace, grader: Grader) -> GradeResult`
  - Implementaciones para `deterministic`, `llm_judge`, `heuristic`
- `agentflow/core/state_manager.py`
  - Agregar `save_grade(execution_id, grade_result)` y `get_grades(execution_id)` al protocolo

**Criterio de aceptación:**
- Grader deterministic: `final_state.intent == "HOT"` → score 1.0 si coincide, 0.0 si no
- Grader heuristic: score 0.0 si `total_tokens > 5000`
- Tests en `agentflow/tests/test_grader.py` (nuevo)

---

### C3. CEL como lenguaje de condición alternativo

**Objetivo:** Permitir `condition_language: "cel"` en edges como opción opt-in al DSL propio.

**Prerequisito:** B3 debe estar completo (no depende directamente, pero C3 es la mejora más compleja y conviene llegar con la base sólida).

**SPEC afectada:** Agregar §4.4 (ver texto completo en roadmap §6).

**Archivos a modificar:**
- `agentflow/core/models.py`
  - Agregar a `Edge`:
    ```python
    condition_cel: str | None = None
    condition_language: Literal["dsl", "cel"] = "dsl"
    ```
- `agentflow/dsl/condition_parser.py`
  - Agregar `evaluate_cel_condition(expr: str, graph_state: dict) -> bool`
  - Usar `cel-python` (`google-cel-python`): parsear y evaluar el string CEL
  - Un error de evaluación en runtime → retornar `False` + emitir evento `warning.cel_evaluation_error`
- `agentflow/core/compiler.py`
  - Si `condition_language == "cel"`: parsear la expresión en compilación para detectar syntax errors (no validar tipos — quedan para runtime)
  - Si `condition_language == "cel"` y `condition` también está definido → error de compilación (ambigüedad)
- `agentflow/core/runtime.py`
  - En la resolución de transiciones, si el edge tiene `condition_language == "cel"`, llamar a `evaluate_cel_condition`

**Dependencia:** `uv add google-cel-python`

**Criterio de aceptación:**
- `condition: "state.intent == 'HOT' && state.confidence >= 0.8"` con `condition_language: cel` evalúa correctamente
- Un syntax error en la expresión CEL es detectado en compilación
- El DSL propio sigue funcionando sin cambios
- Tests en `agentflow/tests/test_cel_conditions.py` (nuevo)

---

## Orden de implementación recomendado

```
A1 (NOTE)          → A2 (timeout/nodo)  → A3 (SET_STATE)
       ↓
B1 (TRANSFORM)     → B2 (StartNode)     → B3 (ExecutionTrace) → B4 (schema inline) → B5 (exec_id)
       ↓
C1 (GUARDRAIL)     → C2 (Graders)       → C3 (CEL)
```

Cada ítem de Fase A es independiente — pueden implementarse en cualquier orden.
B3 es prerequisito para C2 (Graders necesitan ExecutionTrace).
El resto de la Fase B y C son independientes entre sí dentro de cada fase.

---

## Checklist de SPEC.md

Antes de cerrar la implementación de cada ítem, verificar que `SPEC.md` fue actualizado:

| Ítem | Sección SPEC a agregar/modificar |
|------|----------------------------------|
| A1 (NOTE) | Ninguna |
| A2 (timeout/nodo) | §5.4 (nuevo) |
| A3 (SET_STATE) | §3.4 (nuevo) |
| B1 (TRANSFORM) | §3.5 (nuevo) |
| B2 (StartNode) | §2.6 (nuevo) |
| B3 (ExecutionTrace) | §7.4 (nuevo) |
| B4 (schema inline) | Ninguna |
| B5 (exec_id explícito) | Ninguna (§9.3 ya lo cubre) |
| C1 (GUARDRAIL) | §11 completo (nuevo) |
| C2 (Graders) | §12 completo (nuevo) |
| C3 (CEL) | §4.4 (nuevo) |

---

## Resumen de archivos nuevos a crear

| Archivo | Ítem |
|---------|------|
| `agentflow/executors/set_state_executor.py` | A3 |
| `agentflow/executors/transform_executor.py` | B1 |
| `agentflow/executors/guardrail_executor.py` | C1 |
| `agentflow/core/grader.py` | C2 |
| `agentflow/tests/test_transform_executor.py` | B1 |
| `agentflow/tests/test_guardrail.py` | C1 |
| `agentflow/tests/test_grader.py` | C2 |
| `agentflow/tests/test_cel_conditions.py` | C3 |
