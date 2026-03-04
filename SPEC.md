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

---

## 4. Condiciones y Transiciones

### 4.1 Evaluación segura
- Las condiciones de transición **no pueden ejecutar código arbitrario**. Solo se permiten los operadores definidos: `eq`, `neq`, `gt`, `lt`, `gte`, `lte`, `in`, `contains`, `is_null`.
- Un operador no reconocido debe causar un error de compilación, no de ejecución.

### 4.2 Referencia a campos válidos
- El campo referenciado en una condición (`field`) debe existir en el `state_schema` del grafo. Una referencia a un campo inexistente es un **error de compilación**.

### 4.3 Compatibilidad de tipos
- El tipo del valor en la condición debe ser compatible con el tipo declarado del campo en `state_schema`. Una incompatibilidad es un **error de compilación**.

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
