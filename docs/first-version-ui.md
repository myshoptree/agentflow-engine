# Primera Versión UI — AgentFlow Studio v0.1

Especificación de la versión mínima viable de la interfaz. El objetivo es simple: **el usuario diseña un workflow, obtiene el YAML, y puede ejecutarlo**.

Todo lo que no contribuye directamente a ese objetivo queda fuera de esta versión.

---

## Objetivo de la v0.1

> El usuario crea un agente con nombre propio, define su lógica con nodos, y puede chatear con él directamente desde la interfaz.

El producto que el usuario construye no es un "grafo" ni un "workflow" — es un **agente** con el que puede conversar. El grafo es la implementación interna; el agente es lo que el usuario nombra, configura y prueba. El YAML es el artefacto técnico exportable para quien quiera usar el engine directamente.

---

## Lo que está dentro y fuera de scope

### Dentro
- Canvas para diseñar el grafo visualmente
- Formularios de configuración por tipo de nodo
- Generación del YAML en tiempo real
- Panel de ejecución con resultado final
- Soporte para los nodos más usados: `Agent`, `Condition`, `End`
- Soporte para edges condicionales simples (un campo, un operador, un valor)
- Templates de ejemplo para arrancar rápido

### Fuera de esta versión
- Autenticación y multi-tenancy
- Versiones de grafos
- Historial de ejecuciones
- Trace detallado en tiempo real (solo resultado final)
- Sesiones multi-turno
- Nodos avanzados: Parallel, Transform, Set State, Guardrail, MCP
- Edición directa del YAML desde la UI (solo lectura en v0.1)
- Sincronización YAML ↔ canvas bidireccional
- Persistencia en base de datos (el YAML se descarga o copia)

---

## Vista Home — Lista de Workflows

Esta es la pantalla de entrada a la aplicación. El usuario llega aquí antes de abrir o crear un workflow.

### Descripción

La pantalla tiene dos zonas:

**Zona superior — llamada a la acción:**
- Título: "Create a workflow"
- Subtítulo descriptivo corto de qué es un workflow
- Botón prominente "+ Create" para iniciar un workflow nuevo

**Zona inferior — workflows guardados:**
- Dos tabs: **Drafts** y **Templates**
- **Drafts:** lista de workflows que el usuario ha guardado anteriormente. Cada card muestra nombre del workflow, fecha de última modificación y autor.
- **Templates:** lista de templates predefinidos listos para usar como punto de partida.

### Comportamiento de los Drafts

Cada card de draft muestra:
- Ícono del workflow (genérico en v0.1)
- Nombre del workflow
- Fecha y hora de última modificación
- Nombre del autor (valor fijo en v0.1, sin auth)

Al hacer clic en un draft, se abre el canvas con ese workflow cargado.

### Comportamiento de los Templates

Cada card de template muestra:
- Nombre del template
- Descripción breve de para qué sirve

Al hacer clic en un template, el sistema lo carga en el canvas listo para editar y lo guarda automáticamente como un nuevo draft.

### Persistencia en v0.1

Sin base de datos en v0.1, los drafts se persisten en **localStorage** del browser. El usuario no pierde su trabajo al recargar la página, pero los drafts no se comparten entre dispositivos ni usuarios.

### Casos de uso

**CU-00: Ver la pantalla de inicio**
- El usuario abre la aplicación.
- Si no tiene drafts, ve el botón "+ Create" y puede explorar templates.
- Si tiene drafts, los ve listados en el tab Drafts.

**CU-00b: Crear un workflow nuevo**
- El usuario hace clic en "+ Create".
- El sistema navega al canvas con un grafo vacío y nombre por defecto ("New workflow").

**CU-00c: Continuar editando un draft**
- El usuario hace clic en un card de draft.
- El canvas carga el grafo con su última configuración guardada.

**CU-00d: Usar un template**
- El usuario va al tab Templates y hace clic en uno.
- El canvas carga el template listo para editar.
- El sistema lo guarda automáticamente como un nuevo draft.

**CU-00e: Eliminar un draft**
- El usuario activa el menú contextual del card (hover o clic secundario).
- Selecciona "Eliminar" y confirma.
- El draft desaparece de la lista.

### Criterios de aceptación

- [ ] Al abrir la app, la pantalla home es la primera vista que ve el usuario.
- [ ] El tab Drafts muestra los workflows guardados en localStorage.
- [ ] Si no hay drafts, el tab muestra un estado vacío con indicación de cómo crear el primero.
- [ ] El tab Templates muestra los tres templates predefinidos con nombre y descripción.
- [ ] Hacer clic en "+ Create" navega al canvas con un grafo vacío.
- [ ] Hacer clic en un draft navega al canvas con ese grafo cargado.
- [ ] Hacer clic en un template navega al canvas con el template precargado y lo crea como draft.
- [ ] El canvas guarda automáticamente en localStorage al modificar el grafo (autosave).
- [ ] Eliminar un draft requiere confirmación y actualiza la lista sin recargar.
- [ ] El nombre del draft en la card refleja el nombre del grafo definido en el canvas.

---

## Flujo completo del usuario

```
1. Abrir la UI
       ↓
2. Elegir: empezar desde cero o desde un template
       ↓
3. Agregar nodos al canvas y conectarlos
       ↓
4. Configurar cada nodo (nombre, instrucciones, modelo, condiciones)
       ↓
5. Ver el YAML generado en un panel lateral
       ↓
6. Escribir un input de prueba y ejecutar
       ↓
7. Ver el resultado de la ejecución
       ↓
8. Copiar o descargar el YAML para usarlo con el engine
```

---

## Vista Canvas — Editor

La pantalla principal de diseño. Layout de tres columnas: panel de nodos (izquierda), canvas (centro), config panel (derecha, contextual).

### Toolbar superior

```
← "New agent"  [Draft]        [✏ | ▶]        ···  ⚙  Evaluate  Code  [Publish]
```

- **Flecha ←** — vuelve a la Home
- **Nombre del workflow** — editable inline con clic directo sobre el texto
- **Badge Draft / Published** — estado actual del grafo
- **Toggle Edit / Preview** (centro, ícono lápiz | ícono play) — cambia entre modo edición e modo ejecución
- **···** — menú: renombrar, duplicar, eliminar
- **⚙** — configuración global del grafo (timeout, max_depth, state schema)
- **Evaluate** — fuera de scope v0.1, visible pero deshabilitado
- **Code** — abre panel lateral con el YAML generado
- **Publish** — publica el draft como versión ejecutable

### Panel izquierdo — Nodos disponibles

Nodos agrupados por categoría. Los de v0.1 son funcionales; el resto es visible pero deshabilitado con tooltip "Próximamente".

**Core**
| Nodo | Color | Descripción |
|------|-------|-------------|
| Start | Verde claro | Define las entradas del workflow. Si es un chat workflow, expone `input_as_text` y acumula historial de conversación. Determina el modo del Preview. |
| Agent | Azul | Ejecuta un LLM con instrucciones y produce output estructurado |
| Classify | Amarillo | Variante de Agent optimizada para clasificación y routing |
| End | Verde oscuro | Termina el workflow |
| Note | Gris | Comentario visual, ignorado por el engine |

**Tools** — deshabilitados en v0.1
- File search, Guardrails, MCP

**Logic**
| Nodo | Color | Descripción |
|------|-------|-------------|
| If / else | Naranja | Evalúa condiciones CEL y bifurca el flujo |
| While | Naranja | Loop condicional |
| User approval | Naranja | Suspende esperando confirmación del usuario |

**Data** — deshabilitados en v0.1
- Transform, Set state

---

### El nodo Start y los dos tipos de workflow

El nodo `Start` es el que define la naturaleza del workflow y el comportamiento del Preview.

**Workflow conversacional (chat):**
- Tiene un nodo `Start` de tipo chat
- El `Start` hace dos cosas automáticamente: agrega el input del usuario al historial de conversación, y expone `input_as_text` como variable disponible para los nodos siguientes
- El usuario puede agregar variables de estado adicionales al `Start` (ej: `user_id`, `language`)
- El modo Preview se convierte en un **chat** — el usuario escribe mensajes y el workflow responde
- El historial se acumula entre mensajes dentro de la misma sesión de Preview

**Workflow de tarea (task):**
- No tiene nodo `Start` de chat, o tiene un `Start` con inputs fijos (no conversacionales)
- Se ejecuta con un input estructurado específico (un documento, un JSON, un formulario)
- El modo Preview muestra un **formulario de inputs** según lo que declare el `Start`, no un chat
- Cada ejecución es independiente — no hay historial acumulado

**El Preview se adapta automáticamente** según el tipo de `Start` que tenga el workflow. El usuario no necesita declarar explícitamente qué tipo es — el sistema lo infiere del nodo `Start`.

### Diseño de nodos en el canvas

Los nodos muestran su tipo debajo del nombre. Sin necesidad de abrir el config panel para entender qué hace cada uno.

**Nodo Start** — siempre presente, es el punto de entrada:
```
┌──────────────┐
│ ▶  Start     │
└──────────────┘
```

**Nodo Agent / Classify:**
```
┌─────────────────────────┐
│ ▶  Clasificador         │
│    Agent                │
└─────────────────────────┘
```

**Nodo If / else** — muestra las condiciones como texto inline directamente en el cuerpo del nodo. Cada rama tiene su propio handle de salida con la etiqueta de la condición:
```
┌─────────────────────────┐
│ ⚡ If / else            │
│                         │ ──○──► Closer de Ventas
│   intent == "HOT"       │
│                         │
│   Else                  │ ──○──► Educador
└─────────────────────────┘
```

**Nodo End:**
```
┌──────────────┐
│ ■  End       │
└──────────────┘
```

### Config Panel — Nodo Agent seleccionado

Se abre en el lado derecho al hacer clic en un nodo. Incluye nombre, ícono de duplicar y ícono de eliminar en el header.

```
Clasificador                              [⎘] [🗑]
Call the model with your instructions and tools

Name          [Clasificador                    ]

Instructions                              [+] [✏]
┌─────────────────────────────────────────────┐
│ Eres un clasificador de intención de compra │
│ para un SaaS de Web Moderna.                │
│ Tu única función es analizar el mensaje...  │
└─────────────────────────────────────────────┘

Include chat history                      [toggle ON]

Model                                     [gpt-5.2 ↕]

Reasoning effort                          [low    ↕]

Tools                                     [+]

Output format                             [Text  ↕]

∨ More                              Evaluate ↗
```

Observaciones clave:
- **Include chat history** — toggle que determina si el agente recibe el historial de conversación de la sesión
- **Model** — selector de modelo con stepper (↕)
- **Reasoning effort** — nivel de razonamiento (low / medium / high) para modelos que lo soporten
- **Output format** — Text, JSON, o schema Pydantic
- **More** — sección colapsable con configuración avanzada (retries, on_error, timeout)
- **Evaluate** — acceso directo a evaluación del nodo (fuera de scope v0.1)

### Config Panel — Nodo If / else seleccionado

```
If / else                                 [⎘] [🗑]
Create conditions to branch your workflow

If  ⚠                                         [🗑]
  [Case name (optional)                       ]
  [intent == "HOT"                            ]
  Use Common Expression Language to create a
  custom expression. Learn more.

[+ Add]
```

Observaciones clave:
- **Case name** — nombre opcional para la rama (aparece como label en el nodo del canvas)
- **Expresión CEL** — campo de texto libre donde el usuario escribe la condición (`intent == "HOT"`)
- El valor del campo se muestra con syntax highlighting (strings en color diferente)
- El ícono ⚠ indica que la rama tiene algún problema de validación (campo no conectado a un nodo destino)
- **+ Add** — agrega una nueva rama If al nodo
- La rama **Else** es implícita — siempre existe como fallback y aparece en el nodo del canvas

### Toolbar flotante inferior

Centrado en la parte inferior del canvas, siempre visible:

```
┌───────────────────────────┐
│   🖐    ▷    ↺    ↻      │
└───────────────────────────┘
```

- **🖐** Pan — mover el canvas libremente
- **▷** Select — seleccionar y mover nodos
- **↺** Undo — deshacer última acción
- **↻** Redo — rehacer

### Panel Code (botón "Code" en toolbar)

Panel lateral derecho, no bloqueante. El canvas sigue visible mientras está abierto.

```
┌──────────────────────────────────────────────┐
│  Code                           [Copiar] [×] │
├──────────────────────────────────────────────┤
│                                              │
│  id: new-agent                               │
│  version: "1.0.0"                            │
│  entry_node: clasificador                    │
│  nodes:                                      │
│    clasificador:                             │
│      type: agent                             │
│      config:                                 │
│        model: gpt-5.2                        │
│        system_prompt: |                      │
│          Eres un clasificador...             │
│    ...                                       │
│                                              │
│                          [Descargar .yaml]   │
└──────────────────────────────────────────────┘
```

- Solo lectura en v0.1
- Se actualiza automáticamente al modificar el canvas
- **Copiar** — copia el YAML al portapapeles
- **Descargar** — descarga el archivo `.yaml` para usar con el engine CLI

### Modo Preview (toggle central ▶)

Al activar el modo Preview el layout cambia completamente:

```
┌──────────────────────────────────┬───────────────────────────────┐
│                                  │  New chat                  ↗  │
│   Canvas (solo lectura)          ├───────────────────────────────┤
│                                  │                               │
│   [Start]──[Clasificador]        │         [ícono central]       │
│              ──[If/else]         │                               │
│                 ──[Closer]       │    Preview your agent         │
│                 ──[Educador]     │    Prompt the agent as if     │
│             ──[End]              │    you're the user.           │
│                                  │                               │
│                                  │                               │
│                                  │                               │
│                                  │                               │
│                                  ├───────────────────────────────┤
│                                  │  [📎]  Send a message...  [↑] │
└──────────────────────────────────┴───────────────────────────────┘
```

**Comportamiento del modo Preview:**

- El panel de nodos izquierdo desaparece — el canvas ocupa todo el espacio disponible
- El canvas pasa a modo solo lectura — los nodos no son arrastrables ni editables
- El panel derecho es un **chat** completo, no un panel de configuración
- El estado inicial muestra un mensaje vacío con instrucción "Prompt the agent as if you're the user"
- El header del chat tiene **"New chat"** con ícono de nuevo — permite iniciar una nueva conversación de prueba borrando el historial
- El input tiene soporte para adjuntar archivos (ícono de clip)

**Flujo de una conversación de prueba:**

1. El usuario escribe un mensaje en el input y lo envía
2. El mensaje aparece como burbuja de usuario en el chat
3. El engine inicia la ejecución del grafo — la UI se conecta al stream SSE
4. **A medida que cada nodo se activa, el nodo correspondiente en el canvas muestra actividad en tiempo real** — animación de pulso o borde iluminado mientras está ejecutando
5. Cuando el nodo completa, su estado visual cambia: éxito o error
6. La respuesta del agente aparece como burbuja del asistente en el chat al completarse la ejecución
7. El usuario puede continuar enviando mensajes — cada uno es una nueva ejecución dentro de la misma sesión

**Actividad de nodos en tiempo real:**

Esto es la característica más importante del modo Preview. El canvas no es decorativo mientras se ejecuta — muestra en vivo el camino que está tomando el workflow:

| Estado del nodo | Apariencia |
|-----------------|------------|
| Pendiente (no ejecutado aún) | Apariencia normal |
| Ejecutando ahora | Borde animado / pulso de luz |
| Completado con éxito | Borde o fondo con tono de éxito |
| Fallido | Borde o fondo con tono de error |
| No alcanzado (rama no tomada) | Atenuado / opacidad reducida |

El usuario puede ver exactamente qué rama tomó el If/else, cuáles nodos se ejecutaron y cuáles no — sin necesidad de abrir ningún trace.

**Implicaciones para el backend:**

Esta característica requiere SSE incluso en v0.1. El flujo es:
- El usuario envía un mensaje → se crea una ejecución
- La UI abre un stream SSE a esa ejecución
- Cada evento `node.started` y `node.completed` que llega activa el cambio visual del nodo correspondiente en el canvas
- Cuando llega `execution.completed`, el chat muestra la respuesta

Esto significa que el tiempo real **no es opcional para v0.1** — es parte de la experiencia central del Preview.

**Diferencia clave con el concepto anterior:**
El modo Preview no es "ejecutar una vez y ver el resultado" — es una **conversación multi-turno** con actividad visual en tiempo real del grafo. El valor está en ver el workflow en movimiento mientras se conversa con él.

---

## Templates disponibles

Tres templates para arrancar rápido, basados en los ejemplos existentes del engine:

### Template 1: Sales Router
Un agente clasifica la intención del usuario y lo enruta a un agente especializado (HOT → cierre, WARM → educación, COLD → nurturing).

**Nodos:** Classifier (Agent) → Condition → Closer / Educator / Nurture (Agent) → End

### Template 2: Evaluator Loop
Un agente genera contenido, otro lo evalúa con un score. Si el score es bajo, vuelve a generar. Si es alto, continúa.

**Nodos:** Writer (Agent) → Evaluator (Agent) → Condition → Publisher (Agent) → End

### Template 3: Respuesta Directa
El caso más simple: un solo agente recibe el input y genera una respuesta.

**Nodos:** Agent → End

---

## Casos de uso

**CU-01: Empezar desde cero**
- El usuario abre la UI.
- El canvas está vacío.
- Arrastra un nodo Agent, lo configura con un nombre y un system prompt.
- Arrastra un nodo End y lo conecta al Agent.
- El primer nodo agregado se convierte en entry_node automáticamente.
- Ejecuta con un input de prueba y ve el resultado.

**CU-02: Empezar desde un template**
- El usuario abre el selector de templates.
- Elige "Sales Router".
- El canvas se precarga con los nodos y edges del template.
- El usuario ajusta los system prompts a su caso de uso.
- Ejecuta y obtiene resultado.

**CU-03: Agregar una rama condicional**
- El usuario tiene un Agent conectado a un End.
- Agrega un nodo Condition entre el Agent y el End.
- En el Config Panel del Condition, agrega dos branches con sus condiciones y destinos.
- Agrega los nodos de destino al canvas y los conecta al Condition.
- El canvas refleja las dos rutas posibles visualmente.

**CU-04: Configurar un edge condicional**
- El usuario hace clic en un edge existente.
- El Config Panel muestra los campos de condición: campo del estado, operador, valor.
- El usuario selecciona `state.intent`, operador `eq`, valor `HOT`.
- El edge muestra la condición como label en el canvas.

**CU-05: Ver y descargar el YAML**
- El usuario hace clic en "Ver YAML".
- El modal muestra el YAML completo y válido del grafo actual.
- El usuario hace clic en "Descargar" y obtiene el archivo `.yaml`.
- Puede ejecutarlo con `uv run python run.py --yaml mi-workflow.yaml "mensaje"`.

**CU-06: Ejecutar el workflow**
- El usuario escribe un input en el panel inferior y hace clic en "Ejecutar".
- La UI llama al engine con el grafo actual y el input.
- Muestra un estado de carga mientras procesa.
- Muestra el resultado final (respuesta y status).

**CU-07: Ver error de ejecución**
- La ejecución falla por un error de configuración (ej: modelo incorrecto, output_schema inválido).
- El panel inferior muestra el mensaje de error en lugar del resultado.
- El status muestra FAILED con la razón.

**CU-08: Identificar error de grafo antes de ejecutar**
- El usuario conecta dos nodos en un ciclo sin salida (loop incondicional).
- El canvas marca el problema visualmente (nodo o edge con color de advertencia).
- El botón "Ejecutar" está deshabilitado hasta que el grafo sea válido.

---

## Criterios de aceptación

### Canvas
- [ ] El usuario puede agregar nodos Agent, Condition y End al canvas.
- [ ] El usuario puede conectar nodos entre sí arrastrando desde los handles.
- [ ] El usuario puede eliminar nodos y edges.
- [ ] El primer nodo agregado es el entry_node por defecto; el usuario puede cambiarlo.
- [ ] Un nodo Condition muestra sus branches configurados como labels en el canvas.
- [ ] Un edge condicional muestra su condición como label truncado.
- [ ] El canvas permite undo/redo de las últimas acciones.

### Config Panel
- [ ] Al seleccionar un nodo, el Config Panel muestra los campos correctos para ese tipo.
- [ ] Al seleccionar un edge, el Config Panel permite configurar la condición.
- [ ] Los cambios en el Config Panel se reflejan en el canvas inmediatamente.
- [ ] El panel de configuración del grafo (sin selección) permite cambiar nombre, timeout y max_depth.

### YAML
- [ ] El YAML generado es válido y ejecutable por el engine sin modificaciones.
- [ ] El YAML se actualiza cada vez que el usuario modifica el canvas o una configuración.
- [ ] El modal YAML permite copiar el contenido al portapapeles.
- [ ] El modal YAML permite descargar el archivo `.yaml`.

### Ejecución
- [ ] El usuario puede ejecutar el grafo actual con un input de texto.
- [ ] El panel muestra un estado de carga mientras el engine procesa.
- [ ] El resultado final (campo `response` o el primer campo de salida del último nodo) se muestra al terminar.
- [ ] Los errores de ejecución se muestran con un mensaje descriptivo.
- [ ] Si el grafo tiene errores de compilación, el botón Ejecutar está deshabilitado.

### Validación
- [ ] Un ciclo incondicional (loop sin salida) se marca en el canvas antes de ejecutar.
- [ ] Un nodo sin conexión de salida (excepto End) se marca como incompleto.
- [ ] Un edge condicional que referencia un campo que no existe en el estado se marca como inválido.

### Templates
- [ ] Los tres templates se cargan correctamente en el canvas.
- [ ] Un template cargado es modificable sin restricciones.
- [ ] Cargar un template sobre un canvas con trabajo previo pide confirmación.

---

## Qué produce esta versión como entregable

Al terminar la v0.1, el usuario puede:

1. **Diseñar** un workflow de agentes sin escribir YAML manualmente.
2. **Obtener** el YAML resultante para versionarlo en git o compartirlo.
3. **Ejecutar** el workflow directamente desde la UI y ver el resultado.
4. **Iterar** — modificar el grafo y volver a ejecutar sin salir de la interfaz.

El YAML generado es compatible 100% con el engine existente. No requiere cambios en el engine para que funcione.

---

## Lo que se necesita del backend para v0.1

Un único endpoint es suficiente para esta versión:

```
POST /execute
  body: { graph: GraphDefinition, input: string }
  response: { status, result, error_message, depth }
```

El endpoint recibe el grafo como objeto (no necesita persistirlo), lo compila, lo ejecuta y retorna el resultado. No requiere base de datos, ni autenticación, ni versioning.

Esto permite que la UI v0.1 funcione incluso antes de que exista la API completa de la Fase 2.
