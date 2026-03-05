# UI Requirements — AgentFlow Studio

Especificación de requisitos para la interfaz visual de AgentFlow. Este documento describe procesos, casos de uso y criterios de aceptación — no implementación técnica.

---

## Stack preferido

- **Frontend:** React Router 7, Zod, TanStack Query (React Query), React Flow (canvas)
- **Backend API:** FastAPI + SSE para tiempo real
- **Tiempo real:** Server-Sent Events (SSE) — unidireccional server → client

---

## Usuarios del sistema

| Rol | Descripción |
|-----|-------------|
| **Admin** | Gestiona el tenant: usuarios, API keys, configuración global |
| **Editor** | Diseña grafos, ejecuta workflows, revisa traces |
| **Viewer** | Solo lectura: ve grafos, ejecuciones y traces. No puede editar ni ejecutar |

---

## Módulos principales

1. **Canvas Editor** — diseño visual de grafos
2. **Preview & Debug** — prueba de workflows en tiempo real
3. **Executions Dashboard** — monitoreo de ejecuciones
4. **Trace Viewer** — inspección detallada de una ejecución
5. **Sessions** — gestión de conversaciones multi-turno
6. **Multi-tenancy** — aislamiento y gestión por organización

---

## Módulo 1: Canvas Editor

### Descripción
El editor es el espacio principal donde un usuario diseña un workflow de agentes. El resultado del diseño es un grafo que puede compilarse y ejecutarse por el engine de AgentFlow.

### Proceso
1. El usuario crea un grafo nuevo o abre uno existente.
2. Selecciona nodos del panel lateral y los arrastra al canvas.
3. Conecta nodos entre sí para definir el flujo de ejecución.
4. Configura cada nodo (instrucciones, modelo, condiciones, etc.) desde un panel lateral contextual.
5. Configura las condiciones de cada edge (qué tiene que ser verdad en el estado para tomar esa transición).
6. En cualquier momento puede ver el YAML equivalente del grafo que está diseñando.
7. Guarda el grafo como borrador o lo publica como una nueva versión.

### Casos de uso

**CU-01: Crear grafo desde cero**
- El usuario hace clic en "Nuevo grafo".
- El canvas inicia vacío con un nodo Start por defecto.
- El usuario nombra el grafo y comienza a agregar nodos.

**CU-02: Crear grafo desde template**
- El sistema ofrece templates predefinidos (Sales Router, Evaluator Loop, Human-in-the-loop, etc.).
- El usuario selecciona un template y el canvas se precarga con el grafo correspondiente.
- El usuario puede modificarlo libremente.

**CU-03: Agregar un nodo**
- El usuario arrastra un tipo de nodo desde el panel lateral al canvas.
- El nodo aparece en el canvas con valores por defecto.
- El panel de configuración se abre automáticamente para ese nodo.

**CU-04: Conectar dos nodos**
- El usuario arrastra desde el handle de salida de un nodo al handle de entrada de otro.
- Se crea un edge entre ambos.
- Si el edge es condicional, el usuario configura la condición (campo del estado, operador, valor).

**CU-05: Configurar un nodo Agent**
- El usuario selecciona el nodo en el canvas.
- El panel lateral muestra: nombre, modelo LLM, system prompt, schema de output, mapeo de output al estado, política de reintentos, nodo de error.
- El usuario completa los campos y los cambios se reflejan en el canvas inmediatamente.

**CU-06: Ver y editar el YAML**
- El usuario hace clic en el botón "YAML" en el toolbar.
- Se abre un panel con el YAML generado a partir del canvas actual.
- El usuario puede editar el YAML directamente.
- Los cambios en el YAML se sincronizan con el canvas (con un pequeño delay).

**CU-07: Publicar una versión**
- El usuario hace clic en "Deploy".
- El sistema valida el grafo (compilación).
- Si hay errores, los muestra inline en el canvas (nodo o edge con indicador de error).
- Si no hay errores, se publica como una nueva versión semver del grafo.
- La versión anterior sigue disponible y no se modifica.

### Criterios de aceptación

- [ ] Un usuario Editor puede crear, editar y guardar un grafo.
- [ ] Un usuario Viewer no puede modificar el canvas (solo modo lectura).
- [ ] El canvas muestra errores de compilación inline antes de publicar (nodo inalcanzable, ciclo sin salida, condición inválida, referencia a campo inexistente en el estado).
- [ ] El YAML generado es siempre ejecutable por el engine de AgentFlow sin modificaciones.
- [ ] Los cambios en el canvas se guardan como borrador automáticamente (autosave).
- [ ] Publicar una versión genera un nuevo identificador de versión semver y no modifica versiones anteriores.
- [ ] El grafo puede tener múltiples versiones y el usuario puede navegar entre ellas.
- [ ] Los templates predefinidos se cargan correctamente y son modificables.

---

## Módulo 2: Preview & Debug

### Descripción
Permite probar el workflow diseñado enviando un input real y observando la ejecución nodo por nodo en tiempo real, sin salir del canvas.

### Proceso
1. El usuario abre el panel de Preview desde el canvas.
2. Escribe un input de prueba (ej: un mensaje de usuario).
3. Hace clic en "Ejecutar".
4. Observa en tiempo real cómo avanzan los nodos: cuál está ejecutando, cuánto tardó, qué escribió al estado.
5. Los nodos del canvas se colorean a medida que se completan.
6. Si hay un nodo de Human Input, el panel solicita la respuesta del usuario antes de continuar.
7. Al terminar, el usuario ve el resultado final y el estado completo del grafo.

### Casos de uso

**CU-08: Ejecutar preview**
- El usuario escribe un input y hace clic en "Ejecutar".
- El sistema crea una ejecución con el grafo en su versión borrador actual.
- El panel muestra los eventos en tiempo real a medida que llegan del engine.

**CU-09: Observar nodo activo en el canvas**
- Mientras la ejecución corre, el nodo que está ejecutando en ese momento tiene un indicador visual de actividad (animación).
- Al completarse, el nodo cambia a color de éxito o error según el resultado.

**CU-10: Responder a Human Input durante preview**
- El grafo llega a un nodo `human_input` y la ejecución se suspende.
- El panel de preview muestra el prompt configurado en ese nodo.
- El usuario escribe su respuesta y hace clic en "Continuar".
- La ejecución se reanuda desde el punto de suspensión.

**CU-11: Ver estado final**
- Al completarse la ejecución, el panel muestra el estado final del grafo (todos los campos y sus valores).
- También muestra el status final (COMPLETED, FAILED, TIMED_OUT) con la razón si aplica.

### Criterios de aceptación

- [ ] El panel de preview puede abrirse sin salir del canvas.
- [ ] Los eventos de ejecución aparecen en tiempo real (latencia < 500ms desde que el engine los emite).
- [ ] El nodo en ejecución está visualmente diferenciado de los nodos pendientes y completados.
- [ ] Un nodo fallido muestra el mensaje de error en el panel sin interrumpir el resto del trace.
- [ ] El flujo de Human Input puede completarse desde el panel de preview sin ir a otra vista.
- [ ] El estado final se muestra completo y legible.
- [ ] Si la ejecución falla, el panel muestra claramente en qué nodo falló y por qué.

---

## Módulo 3: Executions Dashboard

### Descripción
Vista central para monitorear todas las ejecuciones del tenant: las que están corriendo ahora, las completadas, las fallidas y las suspendidas esperando input humano.

### Proceso
1. El usuario navega a la sección de Ejecuciones.
2. Ve una lista de ejecuciones con su estado, grafo, profundidad y duración.
3. Puede filtrar por grafo, estado y rango de fechas.
4. Las ejecuciones en estado RUNNING se actualizan en tiempo real.
5. Las ejecuciones SUSPENDED muestran que esperan acción humana.
6. El usuario puede hacer clic en cualquier ejecución para ver su detalle.

### Casos de uso

**CU-12: Ver ejecuciones activas**
- El dashboard distingue visualmente las ejecuciones RUNNING de las terminadas.
- Las ejecuciones RUNNING muestran un indicador de progreso y se actualizan sin recargar la página.

**CU-13: Atender una ejecución suspendida**
- El dashboard marca las ejecuciones SUSPENDED con un indicador diferente.
- El usuario puede acceder directamente a la ejecución para proveer el input humano pendiente.

**CU-14: Filtrar ejecuciones**
- El usuario puede filtrar por: grafo, estado (RUNNING, COMPLETED, FAILED, SUSPENDED, CANCELLED), rango de fechas.
- Los filtros se aplican sin recargar la página.

**CU-15: Cancelar una ejecución**
- Desde el dashboard, el usuario puede cancelar una ejecución RUNNING o SUSPENDED.
- La ejecución pasa a estado CANCELLED.
- La acción requiere confirmación explícita.

### Criterios de aceptación

- [ ] El dashboard muestra todas las ejecuciones del tenant, no de otros tenants.
- [ ] Las ejecuciones RUNNING se actualizan en tiempo real sin recargar.
- [ ] Las ejecuciones SUSPENDED están visualmente diferenciadas y son accionables.
- [ ] Los filtros funcionan en combinación (grafo + estado + fecha).
- [ ] Cancelar una ejecución requiere confirmación y refleja el cambio de estado inmediatamente.
- [ ] Un usuario Viewer puede ver el dashboard pero no puede cancelar ejecuciones.

---

## Módulo 4: Trace Viewer

### Descripción
Vista detallada de una ejecución específica. Muestra el recorrido completo: qué nodos se ejecutaron, en qué orden, qué input recibieron, qué output produjeron, cuánto tardaron y qué transiciones tomaron.

### Proceso
1. El usuario accede a una ejecución desde el dashboard o desde el historial de una sesión.
2. Ve una representación del grafo con los nodos coloreados según su resultado.
3. Ve una timeline cronológica de todos los eventos de la ejecución.
4. Puede expandir cada evento para ver el detalle completo (input/output, tokens usados, duración).
5. Si la ejecución está en curso, los eventos llegan en tiempo real.
6. Si la ejecución está SUSPENDED, puede proveer el input humano directamente desde esta vista.

### Casos de uso

**CU-16: Ver trace de ejecución completada**
- El usuario abre una ejecución completada.
- Ve el grafo con todos los nodos coloreados (verde=éxito, rojo=fallo, gris=no ejecutado).
- La timeline muestra cada evento en orden cronológico.

**CU-17: Ver trace en vivo**
- El usuario abre una ejecución RUNNING.
- Los eventos llegan en tiempo real y se agregan a la timeline.
- El nodo activo en el canvas tiene animación de actividad.
- Cuando la ejecución termina, la vista se estabiliza con el estado final.

**CU-18: Inspeccionar output de un nodo**
- El usuario hace clic en un evento `node.completed` en la timeline.
- Se expande mostrando: input que recibió el nodo, output que produjo, campos que escribió al estado, tokens usados, duración.

**CU-19: Ver la razón de un fallo**
- El usuario abre una ejecución FAILED.
- La timeline muestra claramente en qué nodo falló.
- El evento de fallo muestra el mensaje de error completo.
- Si hubo reintentos, se muestran todos los intentos con sus errores.

**CU-20: Responder Human Input desde el trace**
- La ejecución está SUSPENDED en un nodo `human_input`.
- El trace muestra el estado de suspensión y el prompt del nodo.
- El usuario escribe su respuesta y la envía.
- La ejecución se reanuda y los nuevos eventos aparecen en la timeline.

### Criterios de aceptación

- [ ] El trace muestra todos los eventos de la ejecución en orden cronológico.
- [ ] Cada evento de nodo muestra: input, output, duración y (si aplica) tokens usados.
- [ ] Las transiciones muestran qué condición se evaluó y si fue verdadera o falsa.
- [ ] Si hubo reintentos, se muestran todos los intentos y sus errores.
- [ ] En una ejecución RUNNING, los eventos nuevos aparecen en tiempo real (< 500ms de latencia).
- [ ] El grafo visual del trace es de solo lectura — no permite edición.
- [ ] Un usuario Viewer puede ver el trace completo.

---

## Módulo 5: Sessions

### Descripción
Gestión de conversaciones multi-turno. Una sesión agrupa múltiples ejecuciones bajo un mismo hilo de conversación para un grafo dado. Permite ver el historial de mensajes y el estado acumulado entre turnos.

### Proceso
1. El usuario navega a la sección de Sesiones.
2. Ve la lista de sesiones activas del tenant.
3. Abre una sesión y ve el historial de mensajes (usuario / asistente).
4. Puede enviar un nuevo mensaje desde esta vista, lo que crea una nueva ejecución en la sesión.
5. Cada mensaje del historial tiene vinculada la ejecución que lo generó.
6. El usuario puede acceder al trace de cualquier ejecución de la sesión.

### Casos de uso

**CU-21: Ver historial de conversación**
- El usuario abre una sesión.
- Ve los mensajes en orden cronológico, con distinción visual entre mensajes de usuario y respuestas del asistente.

**CU-22: Enviar un mensaje desde la vista de sesión**
- El usuario escribe un mensaje y lo envía.
- Se crea una nueva ejecución en la sesión.
- La respuesta aparece en el historial cuando la ejecución completa.
- El estado acumulado de la sesión se actualiza.

**CU-23: Ver ejecución de un mensaje**
- El usuario hace clic en un mensaje del historial.
- Puede acceder al trace de la ejecución que generó ese mensaje.

### Criterios de aceptación

- [ ] El historial de una sesión es append-only — no se pueden modificar mensajes pasados.
- [ ] Cada mensaje está vinculado a su ejecución y permite navegar al trace.
- [ ] El estado acumulado entre turnos se muestra en la vista de sesión.
- [ ] Una sesión de un tenant no es visible para otro tenant.
- [ ] El usuario Viewer puede ver sesiones pero no puede enviar mensajes.

---

## Módulo 6: Multi-tenancy

### Descripción
El sistema soporta múltiples organizaciones (tenants) completamente aisladas. Cada tenant tiene su propio espacio de grafos, ejecuciones y sesiones. Un usuario pertenece a uno o más tenants y tiene un rol dentro de cada uno.

### Proceso general
1. Un usuario se autentica en el sistema.
2. Si pertenece a más de un tenant, selecciona con cuál organización quiere trabajar.
3. Toda acción que realice (ver grafos, ejecutar, crear sesiones) opera dentro del tenant seleccionado.
4. No puede ver ni acceder a recursos de otro tenant, incluso conociendo el ID.
5. Un Admin puede invitar a otros usuarios al tenant y asignarles roles.
6. Un Admin puede generar API Keys para integración programática.

### Casos de uso

**CU-24: Seleccionar organización al iniciar sesión**
- El usuario se autentica.
- Si tiene acceso a más de un tenant, ve un selector de organización.
- Selecciona la organización con la que quiere trabajar.
- El sistema carga el espacio de trabajo de esa organización.

**CU-25: Cambiar de organización sin re-autenticarse**
- Desde el header, el usuario puede cambiar de organización activa.
- El sistema recarga el espacio de trabajo de la nueva organización seleccionada.
- Los datos de la organización anterior no son accesibles.

**CU-26: Invitar usuario al tenant**
- Un Admin accede a la configuración del tenant.
- Invita a un usuario por email y le asigna un rol (Editor o Viewer).
- El usuario invitado recibe un email y puede acceder al tenant.

**CU-27: Gestionar API Keys**
- Un Admin puede generar una API Key para uso programático del tenant.
- La API Key tiene el mismo aislamiento de tenant que un usuario autenticado.
- El Admin puede revocar una API Key en cualquier momento.
- Al revocar, las integraciones que usen esa key pierden acceso inmediatamente.

**CU-28: Acceso denegado entre tenants**
- Un usuario del tenant A intenta acceder a una ejecución del tenant B (conociendo el ID).
- El sistema responde como si el recurso no existiera (no expone que el recurso pertenece a otro tenant).

### Criterios de aceptación

- [ ] Un usuario solo ve recursos (grafos, ejecuciones, sesiones) de su tenant activo.
- [ ] Conocer el ID de un recurso de otro tenant no permite acceder a él.
- [ ] El cambio de organización no requiere re-autenticación pero sí recarga el espacio de trabajo.
- [ ] Un Admin puede invitar, gestionar roles y revocar acceso de usuarios en su tenant.
- [ ] Una API Key revocada deja de funcionar inmediatamente — sin periodo de gracia.
- [ ] Un usuario Viewer no puede invitar usuarios, generar API Keys ni modificar la configuración del tenant.
- [ ] Los eventos SSE de una ejecución solo son accesibles para usuarios del tenant propietario de esa ejecución.
- [ ] El sistema no expone en ninguna respuesta de error el `tenant_id` de otros tenants.

---

## Tiempo real: comportamiento esperado

### Cuándo se necesita tiempo real

| Situación | Comportamiento esperado |
|-----------|------------------------|
| Preview en ejecución | Eventos llegan al panel a medida que el engine los emite |
| Trace Viewer con ejecución RUNNING | Timeline se actualiza con cada evento sin recargar |
| Dashboard con ejecuciones RUNNING | Estado y progreso se actualizan periódicamente |
| Nodo activo en canvas (preview) | El nodo se ilumina mientras está ejecutando |
| Ejecución suspendida (human input) | El sistema notifica que hay acción pendiente |

### Criterios de aceptación de tiempo real

- [ ] La latencia entre que el engine emite un evento y que aparece en la UI es menor a 500ms en condiciones normales de red.
- [ ] Si la conexión en tiempo real se interrumpe, la UI detecta la desconexión y lo indica visualmente.
- [ ] Si la conexión se pierde, la UI puede recuperar el estado más reciente de la ejecución al reconectar.
- [ ] El tiempo real no es un requisito bloqueante: si falla, la UI puede funcionar en modo polling (refresco periódico) sin perder funcionalidad crítica.
- [ ] Una ejecución que termina mientras el usuario no está conectado al stream puede verse completa al abrir el trace después.

---

## Requisitos transversales

### Validación
- Todos los formularios validan en cliente antes de enviar al servidor.
- Los errores de validación son descriptivos y están ubicados junto al campo que los origina.
- Los errores de compilación del grafo (devueltos por el engine) se muestran en el canvas junto al nodo o edge que los causa.

### Permisos
- Las acciones para las que el usuario no tiene permiso están visualmente deshabilitadas con una explicación en tooltip (no simplemente ocultas).
- Nunca se muestra un error 403 genérico — el usuario siempre sabe de antemano qué puede y qué no puede hacer.

### Estados de carga
- Toda operación asíncrona muestra un estado de carga explícito.
- Las operaciones largas (ejecutar un grafo) muestran progreso, no un spinner indefinido.

### Consistencia de datos
- Tras crear, editar o eliminar un recurso, la lista o vista relacionada se actualiza automáticamente sin recargar la página.
- Si una operación falla en el servidor, el estado local de la UI no se modifica — se mantiene el estado anterior.
