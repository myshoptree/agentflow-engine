# AgentFlow — Especificaciones de Negocio

Este documento define las reglas y restricciones de negocio del sistema.
**Toda implementación nueva debe revisarse contra estas especificaciones antes de considerarse válida.**

---

## 1. Contrato de Ejecución

### 1.1 Determinismo
- Dado el mismo grafo, el mismo estado inicial y el mismo input, la ejecución **debe producir el mismo resultado**.
- El engine no puede tomar decisiones de ruteo fuera de las reglas definidas en el grafo.

### 1.2 Aislamiento de ejecuciones
- Cada ejecución tiene su propio estado. **No existe estado compartido entre ejecuciones concurrentes.**
- Una ejecución no puede leer ni modificar el estado de otra.

### 1.3 Completitud
- Toda ejecución debe terminar en un estado terminal: `COMPLETED`, `FAILED`, `TIMED_OUT` o `CANCELLED`.
- Una ejecución **nunca puede quedar en estado `RUNNING` indefinidamente** sin mecanismo de timeout activo.

---

## 2. Grafos

### 2.1 Definición única de verdad
- El comportamiento del sistema está completamente definido por el grafo. No puede haber lógica de negocio implícita fuera de él.
- Dos grafos con el mismo `id` pero distinta `version` son grafos diferentes. Una ejecución iniciada con una versión **no migra a otra versión** durante su ciclo de vida.

### 2.2 Entrada única
- Todo grafo tiene exactamente **un nodo de entrada** (`entry_node`). No pueden existir múltiples puntos de entrada.

### 2.3 Alcanzabilidad
- Todo nodo definido en el grafo debe ser alcanzable desde el `entry_node`. Un nodo inalcanzable es un **error de definición** que debe detectarse antes de ejecutar.

### 2.4 Terminación garantizada
- Todo grafo debe tener al menos un camino que conduzca a un nodo `end`.
- Un ciclo donde **todos** los edges son incondicionales (sin condición de salida) es un **error de definición** — el grafo nunca terminaría.
- El campo `max_depth` actúa como guardia de último recurso contra loops no terminantes. Cuando se alcanza, la ejecución termina en `FAILED`.

### 2.5 Prioridad de transiciones

- Cuando múltiples edges salen del mismo nodo, se evalúan en orden de `priority` descendente.
- El primer edge cuya condición sea verdadera determina el siguiente nodo.
- Un edge sin condición (`condition: null`) actúa como fallback y solo se toma si ningún edge condicional anterior fue verdadero.
- Si ningún edge aplica y no hay fallback, la ejecución termina en `FAILED` con razón `no_valid_transition`.

### 2.6 Contrato de entrada (StartNode)

- Si el `entry_node` es de tipo `start`, debe declarar explícitamente los campos de input que acepta en `config.inputs`.
- El compilador valida que cada campo declarado en `inputs` existe en el `state_schema`.
- Si se declara `as_text: true` en un campo de tipo `str`, ese campo también se expone como `input_as_text` en el estado antes de continuar al siguiente nodo.
- El nodo `start` no tiene executor propio — es un punto de entrada declarativo que el runtime avanza automáticamente.

---

## 3. Estado

### 3.1 Escritura controlada
- Un nodo **solo puede escribir los campos que declara en su `state_output_mapping`**. No puede sobreescribir arbitrariamente campos del estado.
- Los campos no mapeados permanecen intactos después de la ejecución de un nodo.

### 3.2 Inmutabilidad de checkpoints
- Cada checkpoint es un snapshot inmutable del estado en un momento dado. **No puede modificarse retroactivamente.**
- El historial de checkpoints es la fuente de verdad para auditoría y recuperación.

### 3.3 Estado inicial

- El estado inicial de una ejecución se construye a partir de los valores `default` del `state_schema` del grafo, sobrescritos por el `initial_input` provisto al iniciar la ejecución.

### 3.4 SET_STATE

- Un nodo `set_state` escribe directamente al `graph_state` los campos declarados en `assignments`.
- Los valores pueden ser literales o referencias a campos del estado actual usando notación `state.field`.
- Solo puede escribir campos declarados en el `state_schema` del grafo. El compilador rechaza campos no declarados.

### 3.5 TRANSFORM

- Un nodo `transform` aplica operaciones declarativas sobre el estado sin invocar LLMs ni sistemas externos.
- Las operaciones permitidas son: `set` (copia directa), `template` (interpolación de strings), `extract` (subcampo de dict/list por dot-notation), `cast` (conversión de tipo).
- El nodo es determinista: mismo estado de entrada produce siempre el mismo estado de salida.
- Las operaciones `template` usan sustitución por regex sobre el patrón `{state.field}` — nunca `eval()`. La interpolación es exclusivamente para referencias al estado (`state.*`), no expresiones arbitrarias.

---

## 4. Condiciones y Transiciones

### 4.1 Evaluación segura
- Las condiciones de transición **no pueden ejecutar código arbitrario**. Solo se permiten los operadores definidos: `eq`, `neq`, `gt`, `lt`, `gte`, `lte`, `in`, `contains`, `is_null`.
- Un operador no reconocido debe causar un error de compilación, no de ejecución.

### 4.2 Referencia a campos válidos
- El campo referenciado en una condición (`field`) debe existir en el `state_schema` del grafo. Una referencia a un campo inexistente es un **error de compilación**.

### 4.3 Compatibilidad de tipos
- El tipo del valor en la condición debe ser compatible con el tipo declarado del campo en `state_schema`. Una incompatibilidad es un **error de compilación**.

### 4.4 Lenguajes de condición permitidos

- El DSL estructurado (`condition_language: "dsl"`) es el modo por defecto. Todas las validaciones de compilación aplican.
- CEL (`condition_language: "cel"`) es un modo alternativo opt-in. La expresión se parsea en compilación (detección de errores de sintaxis), pero la validación de tipos es responsabilidad del autor del grafo. Un error de evaluación en runtime se trata como condición falsa y se registra como evento de advertencia `warning.cel_evaluation_error`.
- No se permiten otros lenguajes de expresión. En particular, expresiones Python arbitrarias (`eval`) están explícitamente prohibidas.
- Un edge no puede tener simultáneamente `condition` (DSL) y `condition_cel` (CEL) — es un error de compilación.

---

## 5. Fallos y Recuperación

### 5.1 Reintentos
- Los errores transitorios (timeout de red, fallo temporal de API) pueden reintentarse. El número máximo de reintentos es configurable por nodo.
- Entre reintentos debe aplicarse backoff. **No se permiten reintentos inmediatos consecutivos.**

### 5.2 Routing de errores
- Si un nodo define `on_error`, un fallo no recuperable (reintentos agotados) **debe** enrutar a ese nodo en lugar de terminar la ejecución en `FAILED` directamente.
- Si no hay `on_error` definido y los reintentos se agotan, la ejecución termina en `FAILED`.

### 5.3 Timeout global
- El `global_timeout_seconds` se aplica a la ejecución completa, no a nodos individuales.
- Cuando expira, la ejecución termina en `TIMED_OUT` independientemente del nodo en curso.

### 5.4 Timeout por nodo

- Un nodo puede declarar `timeout_seconds` en su config. Si la ejecución del nodo supera ese tiempo, se trata como un fallo no recuperable del nodo (equivalente a reintentos agotados).
- El timeout por nodo no cancela el timeout global (§5.3) — ambos aplican de forma independiente.
- Un timeout de nodo sin `on_error` definido termina la ejecución en `FAILED`.
- El timeout de nodo se registra vía el evento `node.failed` con la razón `NodeTimeoutError`. No genera un evento de tipo `node.timeout` separado.

---

## 6. Suspensión y Reanudación

### 6.1 Human-in-the-loop
- Un nodo de tipo `human_input` suspende la ejecución. Esta queda en estado `SUSPENDED` hasta recibir un evento externo de reanudación.
- Una ejecución `SUSPENDED` **no consume recursos activos** mientras espera.

### 6.2 Reanudación válida
- Solo una ejecución en estado `SUSPENDED` puede ser reanudada. Intentar reanudar una ejecución en cualquier otro estado es un **error**.
- Al reanudar, el input externo se incorpora al estado antes de continuar.

---

## 7. Observabilidad

### 7.1 Trazabilidad completa
- Cada ejecución debe registrar: qué nodos se ejecutaron, en qué orden, con qué input/output, cuánto tardaron y si hubo errores.
- Sin este registro, una ejecución no puede considerarse auditada.

### 7.2 Eventos mínimos requeridos
- Los siguientes eventos son obligatorios para toda ejecución: `execution.started`, `execution.completed` o `execution.failed`, `node.started` y `node.completed` o `node.failed` por cada nodo ejecutado.

### 7.3 No pérdida de eventos
- Un evento emitido **no puede perderse silenciosamente**. Si el sistema de observabilidad falla, debe registrarse el fallo — pero no puede suprimir el evento sin dejar rastro.

### 7.4 ExecutionTrace

- Al completar una ejecución (cualquier estado terminal), el sistema debe poder producir un `ExecutionTrace` que agrupe todos los registros de la ejecución.
- El trace incluye: `execution_id`, `graph_id`, `graph_version`, `status`, `duration_ms`, `total_tokens`, `node_records`, `checkpoints`, `final_state`.
- El trace es inmutable una vez que la ejecución llega a estado terminal.

---

## 8. Versiones de Grafos

### 8.1 Inmutabilidad de versiones publicadas
- Una vez que un grafo con una versión específica ha sido utilizado en una ejecución, su definición **no puede modificarse**. Para cambiar el comportamiento se debe crear una nueva versión.

### 8.2 Compatibilidad hacia adelante
- Una nueva versión de un grafo puede agregar nodos o campos de estado. **No puede eliminar campos de estado que eran obligatorios en versiones anteriores** si hay ejecuciones activas con esa versión anterior.

---

## 9. API HTTP

### 9.1 Operaciones expuestas
- El sistema debe exponer como mínimo las siguientes operaciones vía HTTP:
  - **Crear ejecución**: recibe `graph_id`, `graph_version` e `initial_input`, retorna `execution_id`.
  - **Consultar estado**: retorna el estado actual de una ejecución dado su `execution_id`.
  - **Reanudar ejecución**: acepta `execution_id` e `input_data`; solo válido si la ejecución está en estado `SUSPENDED`.
  - **Cancelar ejecución**: marca una ejecución `RUNNING` o `SUSPENDED` como `CANCELLED`.

### 9.2 Contratos de respuesta
- Toda respuesta de error debe incluir un código de error de negocio legible (no solo HTTP status) y un mensaje descriptivo.
- Una petición para reanudar una ejecución que no está en `SUSPENDED` debe retornar error — no ejecutarse silenciosamente.

### 9.3 Idempotencia de creación
- Si se provee un `execution_id` explícito al crear una ejecución y ya existe, el sistema debe retornar error — no crear un duplicado ni sobreescribir.

---

## 10. Sesiones y Conversación

### 10.1 Definición de sesión
- Una **sesión** agrupa múltiples ejecuciones bajo un mismo `session_id` y un mismo `graph_id`.
- Cada sesión mantiene un **historial de mensajes** — lista ordenada de pares `{role, content}` donde `role` es `user` o `assistant`.
- El historial es append-only: nunca se modifica un mensaje ya guardado.

### 10.2 Flujo de un mensaje
- Cada mensaje del usuario crea una nueva ejecución dentro de la sesión.
- Antes de ejecutar el grafo, el historial completo de la sesión se inyecta en el estado inicial como `conversation_history`.
- El `AgentNode` recibe el historial y lo pasa al LLM como contexto (message history), no solo como texto plano.
- Al completar la ejecución, la respuesta del agente se agrega al historial de la sesión.

### 10.3 Aislamiento de sesiones
- El historial de una sesión no puede ser accedido por otra sesión.
- Dos sesiones pueden usar el mismo grafo de forma independiente.

### 10.4 Estado acumulado entre mensajes
- El `graph_state` de la última ejecución completada se propaga como estado base de la siguiente ejecución dentro de la misma sesión.
- Esto permite que el agente recuerde datos recolectados (intent, nombre del usuario, etc.) sin repetir clasificaciones.

### 10.5 Operaciones expuestas vía API
- `POST /sessions` — crear sesión para un grafo
- `POST /sessions/{id}/message` — enviar mensaje, ejecutar grafo, recibir respuesta
- `GET /sessions/{id}` — consultar historial y estado actual

---

## 11. Guardrails

### 11.1 Propósito
- Un nodo `guardrail` evalúa campos del estado contra criterios de seguridad declarados. No genera output de negocio — solo produce un resultado `pass` o `fail` con razón.

### 11.2 Checks soportados
- `pii`: detecta Información Personal Identificable en el valor del campo usando `presidio-analyzer`.
- `toxicity`: detecta contenido tóxico o dañino (implementación heurística o LLM).
- `custom_llm`: LLM-as-judge — evalúa el campo con un prompt configurable. La respuesta se normaliza a `pass`/`fail`.

### 11.3 Routing
- Si todos los checks pasan: la ejecución continúa por el edge normal del nodo.
- Si algún check falla: la ejecución se redirige a `on_fail` (node_id). Si `on_fail` no está definido, la ejecución termina en `FAILED`.
- El resultado (`pass`/`fail`) y la razón se escriben al estado en los campos declarados en `output_mapping`.

### 11.4 Provider-agnostic
- Los checks deben funcionar sin depender de proveedores específicos (no OpenAI Moderation). El check `pii` puede usar `presidio-analyzer`. El check `custom_llm` usa el modelo configurado en el nodo.
- Si `presidio-analyzer` no está instalado y se usa el check `pii`, el nodo falla con un error claro de configuración.

---

## 12. Graders y Evaluación

### 12.1 Propósito
- Un grader evalúa un `ExecutionTrace` y asigna un score o label. Es un contrato de calidad sobre ejecuciones pasadas.

### 12.2 Tipos de grader
- `deterministic`: compara `final_state[field]` contra un valor esperado usando el DSL de condiciones existente. Score es 1.0 (pass) o 0.0 (fail).
- `llm_judge`: un LLM evalúa el trace completo usando un prompt configurable. Retorna score 0.0–1.0.
- `heuristic`: reglas sobre métricas del trace (tokens totales, duración, número de reintentos). Score es 1.0 o 0.0.

### 12.3 Inmutabilidad
- Los graders no modifican el trace. Producen un `GradeResult` asociado al trace, no parte de él.
- Un `GradeResult` tiene: `grader_id`, `execution_id`, `score`, `label`, `reason`, `graded_at`.
