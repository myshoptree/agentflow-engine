# Mejoras de Core — Inspirado en OpenAI Agent Builder

Análisis de gaps entre AgentFlow y OpenAI Agent Builder. Este documento se centra exclusivamente en **mejoras al runtime y modelos core** — no en UI ni plataforma.

---

## 1. Nodos faltantes

### 1.1 `NodeType.TRANSFORM`

**Qué hace en OpenAI:** Reshapea datos entre nodos (objeto → array, extrae campos, fuerza tipos) sin invocar un LLM.

**Por qué importa en AgentFlow:** Hoy la única forma de transformar datos entre nodos es usar un AgentNode (costoso, no determinista) o escribir lógica en un ToolNode (requiere código Python registrado). Un nodo Transform puro — declarativo y sin LLM — cubre un gap real.

**Propuesta de implementación:**
```yaml
type: transform
config:
  operations:
    - set: "output_field"
      from: "state.some_field"
    - set: "combined"
      template: "{state.first_name} {state.last_name}"
```

**Qué necesita:**
- Nuevo `TransformNodeConfig` en `models.py`
- Nuevo `TransformExecutor` en `executors/`
- Soporte de operaciones: `set`, `template`, `extract`, `cast`
- Sin LLM, sin llamadas externas — pure Python

---

### 1.2 `NodeType.SET_STATE`

**Qué hace en OpenAI:** Define o sobreescribe variables globales del workflow de forma explícita como un nodo dedicado.

**Estado actual en AgentFlow:** El estado se escribe solo a través de `state_output_mapping` en la config de cada nodo. No hay un nodo cuyo único propósito sea escribir al estado.

**Cuándo es útil:** Inicializar variables antes de un loop, resetear un contador, marcar flags de control de flujo.

**Propuesta:**
```yaml
type: set_state
config:
  assignments:
    retry_count: 0
    status: "pending"
    # soporte a referencias del estado actual:
    previous_intent: "state.intent"
```

**Nota:** Podría fusionarse con `TransformNode` — evaluar si son uno o dos nodos.

---

### 1.3 `NodeType.GUARDRAIL`

**Qué hace en OpenAI:** Evalúa el output de un nodo anterior contra criterios de seguridad (PII, jailbreak, hallucinations). Pass/fail con routing configurable.

**Por qué importa:** Sin guardrails, la seguridad queda en manos del implementador. En producción, un nodo Guardrail es la diferencia entre un sistema robusto y uno vulnerable.

**Propuesta de implementación:**
```yaml
type: guardrail
config:
  checks:
    - type: "pii"          # detecta PII en el output
    - type: "toxicity"     # detecta contenido tóxico
    - type: "custom_llm"   # LLM-as-judge con prompt configurable
      prompt: "Does the output contain medical advice? Answer yes/no."
  on_fail: "guardrail_error_node"  # node_id destino si falla
  on_pass: null  # continúa por edge normal
```

**Qué necesita:**
- Nuevo `GuardrailNodeConfig` + `GuardrailExecutor`
- Integración con librerías como `presidio` para PII, o LLM-as-judge
- El resultado (pass/fail + razón) se escribe al estado
- SPEC.md necesita una nueva sección §11 para guardrails

---

### 1.4 `NodeType.WHILE` (explícito)

**Estado actual:** Los loops se modelan con edges condicionales + `max_depth`. Funciona, pero no es legible ni obvio en YAML.

**Problema:** Un evaluator loop en YAML actual requiere entender la mecánica de edges. Un nodo While explícito hace el intent obvio.

**Propuesta:**
```yaml
type: while
config:
  condition:
    field: "state.eval_score"
    operator: "lt"
    value: 0.8
  body_node: "improve_agent"   # nodo que ejecuta el cuerpo del loop
  max_iterations: 10           # guardia explícita (además de max_depth global)
```

**Alternativa más simple:** Mantener el modelo actual de edges + agregar azúcar sintáctica en el compilador que detecte el patrón y lo valide mejor.

---

## 2. Mejoras al modelo de condiciones

### 2.1 Adoptar CEL (Common Expression Language) como alternativa al DSL propio

**OpenAI usa CEL** para If/else y While: `input.output_parsed.operating_procedure == "q-and-a"`

**CEL tiene:**
- Implementación Python: `cel-python`
- Determinista — no permite I/O ni efectos secundarios
- Estándar reconocido (usado por Google, CNCF, OPA)
- Más expresivo que el DSL actual de AgentFlow

**DSL actual de AgentFlow:**
- Pros: sin dependencias externas, 100% controlado, validable en compilación
- Contras: menos expresivo, no estándar, curva de aprendizaje propia

**Recomendación:** Evaluar adoptar CEL como opción adicional (no reemplazar el DSL actual). Permitir `condition_language: "cel"` en el edge para grafos que lo necesiten.

---

### 2.2 Condiciones con acceso a outputs de nodos específicos

**OpenAI:** `input.output_parsed.field` — accede al output del nodo anterior de forma explícita.

**AgentFlow actual:** Las condiciones solo acceden a `state.*` — el estado acumulado. No hay forma de referenciar el output de un nodo específico por nombre.

**Problema:** Si dos nodos escriben al mismo campo de estado, se pierde el contexto de quién escribió qué.

**Propuesta:** Agregar namespace de nodo al estado:
```
state.intent              # campo global (actual)
nodes.classifier.intent   # output del nodo "classifier" específico (nuevo)
```

Requiere cambios en `condition_parser.py` y en `state_output_mapping`.

---

## 3. Mejoras al Start node

**OpenAI:** El nodo Start tiene `input_as_text` como variable automática que representa el texto del input del usuario. Expone el input de forma explícita y tipada.

**AgentFlow actual:** El input del usuario llega como `graph_state["user_input"]` — es una convención, no un contrato.

**Propuesta:**
- Definir `StartNodeConfig` con campos de input declarados y sus tipos
- El compilador valida que los campos del Start existen en `state_schema`
- `input_as_text` se convierte en variable automática para todo grafo con input de tipo texto
- Hace el contrato de entrada del grafo explícito y validable

```yaml
nodes:
  start:
    type: start
    config:
      inputs:
        - name: user_message
          type: str
          as_text: true   # expone como input_as_text
        - name: user_id
          type: str
```

---

## 4. Mejoras al sistema de observabilidad (hacia Trace Grading)

**OpenAI:** Trace grading = asignar scores/labels a traces completos para evaluar correctness y calidad.

**AgentFlow ya tiene:** `NodeExecutionRecord` con `input_state`, `output_data`, `duration_ms`, `llm_tokens_used`, `attempt`, `error_message`. Es la materia prima exacta para grading.

**Lo que falta:**

### 4.1 Trace como objeto de primera clase

Hoy un "trace" de AgentFlow está fragmentado en:
- Múltiples `NodeExecutionRecord`
- Múltiples `StateCheckpoint`
- Eventos JSON en stdout

**Propuesta:** Agregar `ExecutionTrace` que agrupa todo:
```python
class ExecutionTrace(BaseModel):
    execution_id: str
    graph_id: str
    graph_version: str
    status: ExecutionStatus
    duration_ms: float
    total_tokens: int
    node_records: list[NodeExecutionRecord]
    checkpoints: list[StateCheckpoint]
    final_state: dict[str, Any]
```

### 4.2 API de traces

```
GET /executions/{id}/trace     → ExecutionTrace completo
GET /traces?graph_id=&status=  → lista de traces con filtros
```

### 4.3 Graders como concepto en el modelo

```python
class GraderType(str, Enum):
    DETERMINISTIC = "deterministic"   # compara output vs expected (usa DSL actual)
    LLM_JUDGE = "llm_judge"           # LLM evalúa el trace
    HEURISTIC = "heuristic"           # reglas sobre métricas (tokens, duración, reintentos)

class Grader(BaseModel):
    id: str
    name: str
    type: GraderType
    config: dict[str, Any]
```

---

## 5. Mejoras menores pero impactantes

### 5.1 `Note` node
Nodo que existe en el grafo pero el runtime ignora completamente. Solo para documentación en YAML. Costo de implementación: mínimo.

```yaml
nodes:
  explain_router:
    type: note
    config:
      text: "Este nodo clasifica el intent antes de rutear al agente correcto"
```

### 5.2 Timeout por nodo (además del global)
OpenAI no lo expone explícitamente pero es una necesidad real. AgentFlow tiene `global_timeout_seconds` pero no timeout individual por nodo.

```yaml
config:
  timeout_seconds: 10  # falla el nodo si el LLM tarda más de 10s
```

Requiere wrapping con `asyncio.wait_for` en cada executor. Necesita entrada en SPEC §5.

### 5.3 Output schema inline (no solo por class name)
Hoy `output_schema` requiere un fully-qualified class name Python (`"examples.schemas.ClassifierOutput"`). Esto acopla el YAML al código.

**Propuesta:** Permitir definir el schema inline en el YAML:
```yaml
config:
  output_schema_inline:
    intent:
      type: str
      enum: ["HOT", "WARM", "COLD"]
    confidence:
      type: float
```

El compilador genera el modelo Pydantic dinámicamente desde la definición. Elimina la dependencia de un módulo Python externo para casos simples.

### 5.4 `execution_id` explícito en la API
SPEC §9.3 ya lo menciona (idempotencia), pero la implementación actual no expone crear una ejecución con un `execution_id` específico. Necesario para sistemas que generan IDs propios (correlación con sistemas externos).

---

## 6. CEL (Common Expression Language) vs DSL propio

### ¿Qué es CEL?

CEL es un lenguaje de expresiones desarrollado por Google, diseñado para ser seguro, determinista y embebible. Es el estándar usado por OpenAI Agent Builder, Google Cloud IAM, Kubernetes admission webhooks, y Open Policy Agent (OPA).

Especificación: https://github.com/google/cel-spec
Implementación Python: `cel-python` (`google-cel-python`)

### Cómo se ve cada uno en la práctica

**DSL propio de AgentFlow (YAML estructurado):**
```yaml
# Condición simple
condition:
  field: state.intent
  operator: eq
  value: "HOT"

# Condición compuesta AND
condition:
  operator: and
  conditions:
    - field: state.intent
      operator: eq
      value: "HOT"
    - field: state.confidence
      operator: gte
      value: 0.8

# En evaluator loop
condition:
  field: state.score
  operator: lt
  value: 0.8
```

**CEL (string de expresión):**
```yaml
# Condición simple
condition: "state.intent == 'HOT'"

# Condición compuesta AND
condition: "state.intent == 'HOT' && state.confidence >= 0.8"

# En evaluator loop
condition: "state.score < 0.8"

# Expresiones que el DSL actual NO puede hacer
condition: "state.score < 0.8 && state.iterations < 5"
condition: "state.intent in ['HOT', 'WARM']"
condition: "state.email.endsWith('@empresa.com')"
condition: "size(state.items) > 0"
```

---

### Comparación directa

| Dimensión | DSL propio | CEL |
|-----------|-----------|-----|
| **Seguridad** | Sin eval(), operadores fijos | Sin eval(), pero más superficie — depende de la implementación |
| **Expresividad** | Limitada — 9 operadores, sin composición inline | Alta — aritmética, strings, listas, ternarios, funciones |
| **Validación en compilación** | Total — cada campo es Pydantic validado | Parcial — el string se parsea en compilación pero los tipos se infieren en runtime |
| **Legibilidad** | Verbosa en casos complejos (YAML anidado) | Compacta y familiar (similar a Python/JS) |
| **Estandarización** | Propia — nadie más la conoce | Estándar reconocido (Google, CNCF, OPA, OpenAI) |
| **Dependencia externa** | Ninguna | `cel-python` (~50KB) |
| **Soporte AND/OR inline** | Solo vía `CompoundCondition` anidada | Nativo con `&&`, `\|\|` |
| **Aritmética** | No soportada | `state.score * 100 > 80` |
| **Acceso a strings** | No soportado | `state.email.endsWith(...)`, `state.name.startsWith(...)` |
| **Ternarios** | No soportado | `state.score >= 0.8 ? 'pass' : 'fail'` |
| **Funciones sobre listas** | `in`, `contains` limitados | `state.tags.exists(t, t == 'vip')`, `size(state.items) > 0` |
| **Errores de compilación** | Detallados — campo por campo | El string puede parsearse bien pero fallar en tipos en runtime |
| **Curva de aprendizaje** | Alta para quien llega nuevo | Baja — sintaxis similar a lenguajes conocidos |

---

### Limitaciones concretas del DSL actual

Casos que **no se pueden expresar** con el DSL actual de AgentFlow y que sí son necesarios en workflows reales:

```yaml
# 1. Condición sobre múltiples campos sin ConditionNode dedicado
#    Actual: requiere un ConditionNode con CompoundCondition
#    CEL: "state.intent == 'HOT' && state.confidence >= 0.8 && state.retries < 3"

# 2. Condición con aritmética
#    Actual: imposible
#    CEL: "state.total_price * 0.9 > state.min_price"

# 3. Condición sobre tamaño de lista
#    Actual: imposible
#    CEL: "size(state.errors) == 0"

# 4. Condición con string matching
#    Actual: solo eq/neq exacto
#    CEL: "state.email.endsWith('@empresa.com')"

# 5. Múltiples valores posibles sin declarar lista en YAML
#    Actual: operator "in" requiere declarar la lista explícitamente en el YAML
#    CEL: "state.status in ['pending', 'retry']"

# 6. Condición negada
#    Actual: usar neq o is_null
#    CEL: "!(state.approved) && state.amount > 1000"
```

---

### Por qué el DSL propio sigue siendo valioso

1. **Validación en compilación total** — cada `field`, `operator` y `value` es validado por Pydantic antes de ejecutar. Con CEL, un error de tipo en `state.confidence >= "alta"` solo falla en runtime.

2. **Introspectabilidad** — como el DSL es una estructura de datos (no un string), el compilador puede razonar sobre él: saber qué campos referencia una condición, si son compatibles con el `state_schema`, si el operador es válido. Esto habilita validaciones que con un string CEL son mucho más complejas de implementar.

3. **Sin dependencias** — el DSL actual es puro Python sin deps externas. Esto importa en entornos restringidos.

4. **Superficie de ataque reducida** — menos código, menos casos borde, menos vectores de ataque.

---

### Recomendación: modo dual con coexistencia

No reemplazar el DSL — extenderlo con CEL como opción `opt-in` por edge o nodo.

**Propuesta de implementación:**

```yaml
# Modo actual (DSL estructurado) — por defecto
edges:
  - from_node: classifier
    to_node: closer
    condition:
      field: state.intent
      operator: eq
      value: "HOT"

# Modo CEL — opt-in con condition_language
edges:
  - from_node: classifier
    to_node: closer
    condition_language: "cel"
    condition: "state.intent == 'HOT' && state.confidence >= 0.8"
```

**En `models.py`:**
```python
class Edge(BaseModel):
    from_node: str
    to_node: str
    condition: EdgeCondition | None = None
    condition_cel: str | None = None        # nuevo — CEL string
    condition_language: Literal["dsl", "cel"] = "dsl"
    priority: int = 0
```

**En el compilador:** parsear y validar la expresión CEL en compilación (detección de syntax errors), aunque la validación de tipos queda para runtime.

**En `condition_parser.py`:** nuevo `evaluate_cel_condition(expr: str, graph_state: dict) -> bool` usando `cel-python`.

---

### Casos donde usar cada uno

| Caso | DSL propio | CEL |
|------|-----------|-----|
| Routing simple por intent | ✅ Ideal | ✅ También funciona |
| Condición AND/OR simple | ✅ Con CompoundCondition | ✅ Más compacto |
| Condición con aritmética | ❌ No soportado | ✅ |
| Validación de tipo string | ❌ Solo eq/neq | ✅ endsWith, startsWith, matches |
| Condición sobre listas dinámicas | ❌ Limitado | ✅ size(), exists(), all() |
| Grafos que se validan 100% en compilación | ✅ | ⚠️ Parcial |
| Grafos definidos por usuarios no técnicos | ⚠️ Verboso | ✅ Más natural |
| Grafos con auditoría estricta de condiciones | ✅ | ⚠️ Más difícil de inspeccionar |

---

### Impacto en SPEC.md

Si se adopta CEL como opción, requiere nueva entrada en SPEC §4:

> **§4.4 Lenguajes de condición permitidos**
> - El DSL estructurado (`condition_language: "dsl"`) es el modo por defecto. Todas las validaciones de compilación aplican.
> - CEL (`condition_language: "cel"`) es un modo alternativo opt-in. La expresión se parsea en compilación pero la validación de tipos es responsabilidad del autor del grafo. Un error de evaluación en runtime se trata como condición falsa y se registra como evento de advertencia.
> - No se permiten otros lenguajes de expresión. En particular, expresiones Python arbitrarias (`eval`) están explícitamente prohibidas.

---

## 7. Lo que NO adoptar de OpenAI Agent Builder

| Feature OpenAI | Por qué no adoptar en AgentFlow |
|----------------|--------------------------------|
| **CEL como único DSL** | El DSL propio es más seguro por ser más restringido; CEL puede ser opcional |
| **Guardrails acoplados a OpenAI Moderation** | Los guardrails deben ser provider-agnostic |
| **Deploy directo a plataforma** | El valor de AgentFlow es self-hosted; no crear dependencia de cloud propio |
| **Canvas visual como feature core** | Es una capa de presentación separada, no core del runtime |

---

## Priorización sugerida

### Impacto alto, esfuerzo bajo
1. `NOTE` node — mínimo esfuerzo, mejora legibilidad de grafos
2. Timeout por nodo — `asyncio.wait_for` en executors
3. `SET_STATE` node — simplifica YAML de workflows complejos

### Impacto alto, esfuerzo medio
4. `TRANSFORM` node — elimina necesidad de ToolNodes para transformaciones simples
5. `StartNodeConfig` explícito — hace el contrato de entrada validable
6. `ExecutionTrace` como objeto — habilita todo el sistema de evaluación
7. Output schema inline — desacopla YAML de módulos Python

### Impacto alto, esfuerzo alto
8. `GUARDRAIL` node — requiere integración con librerías externas + nueva sección en SPEC
9. Graders + Eval API — requiere capa de query sobre traces persistidos
10. CEL como opción adicional — dependencia externa + parser alternativo
