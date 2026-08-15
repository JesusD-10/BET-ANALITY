# BET ANALIZADOR

Plataforma web de inteligencia deportiva para el análisis avanzado de partidos de fútbol y la generación de pronósticos estadísticos utilizando **API-Football, Sportmonks y Football-Data.org**, un motor probabilístico local y proveedores de IA intercambiables.

---

## 🚀 Características Principales

- ⚽ **Catálogo con triple respaldo automático**: la cadena predeterminada es API-Football → Sportmonks → Football-Data.org. Ante credenciales rechazadas, límite de cuota, timeout, error de red, respuesta inválida o una agenda vacía se prueba el siguiente proveedor; la fuente efectiva siempre se identifica.
- 🏟️ **Portada curada por liga**: muestra únicamente partidos en curso o futuros de clubes y selecciones populares, con máximo 4 por competición. En `/partidos` primero se elige la competición y después se despliega su agenda, al estilo de un marcador deportivo.
- 📊 **Estadísticas e Histórico**: Tres vistas independientes muestran primero los últimos 5 H2H, los últimos 5 partidos del local y los últimos 5 del visitante; cada vista permite desplegar hasta 5 encuentros anteriores adicionales.
- 🟨 **Disciplina y árbitro**: identifica al árbitro asignado y calcula por separado los promedios recientes verificados de faltas, amarillas y rojas de cada equipo. Si la API no entrega métricas históricas del árbitro, se indica `N/D` en vez de inventarlas.
- 🚑 **Lesionados y Sancionados**: Detección de bajas confirmadas o dudas clave en la plantilla y evaluación de su impacto táctico.
- 📋 **Alineaciones y Formaciones**: once probable calculado desde titulares y formaciones recientes, excluyendo bajas confirmadas; desde T-60 se consulta el dato oficial y solo se confirma un equipo cuando el proveedor entrega formación y 11 titulares válidos. Ambos onces se representan sobre una simulación visual 4-3-3.
- 🤖 **Motor multi-IA** (xAI/Grok, DeepSeek, Cerebras y OpenRouter):
  - Contrasta en paralelo hasta cuatro proveedores para cada análisis y acepta resultados parciales dentro de un plazo común.
  - Mantiene un motor local cuando no hay claves, cuota o respuesta externa.
  - El backend exige apoyo independiente, descarta empates entre selecciones opuestas, limita valores atípicos y recalcula **Cuota Justa** ($1 / \text{probabilidad}$) y **Valor Esperado (EV %)**; ninguna IA puede imponer cuotas de una casa.
  - Factores a favor y riesgos identificados para cada opción de mercado.
- 🧩 **Combinadas por Partido**: Bet builders de dos o tres condiciones con probabilidad conjunta ajustada, cuota justa de referencia y advertencias de correlación.
- ✨ **Soñadoras por Partido y del Día**: Selecciones de alta varianza con probabilidad modelada mínima del 30% y cuotas justas de referencia desde 3.00.
- 💬 **Asistente de IA Interactivo**: Chat dedicado para realizar preguntas tácticas y consultas específicas de valor sobre cualquier encuentro.

---

## 🛠️ Arquitectura y Tecnologías

El proyecto se estructura como una arquitectura desacoplada moderna:

```text
BET_ANALIZADOR/
├── backend/            # API REST desarrollada en Python (FastAPI)
│   ├── app/
│   │   ├── api/        # Rutas y controladores REST (FastAPI)
│   │   ├── core/       # Configuración global y variables de entorno
│   │   ├── schemas/    # Modelos de datos Pydantic
│   │   └── services/   # Tres APIs deportivas, orquestador multi-IA y motor analítico
│   └── tests/          # Suite de pruebas unitarias e integración (pytest)
│
├── frontend/           # Aplicación Web Next.js (React + TypeScript)
│   └── app/
│       ├── lib/        # Cliente API e interfaces de TypeScript
│       └── partidos/   # Vistas de agenda y detalle enriquecido del partido
│
├── .env.example        # Plantilla de variables de entorno
└── README.md           # Documentación del repositorio
```

---

## ⚙️ Requisitos Previos e Instalación

### Requisitos:
- **Python** 3.11 o superior.
- **Node.js** 18 o superior.
- Claves de API (opcionales para modo en vivo):
  - Una o más claves de IA: `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY` o `DEEPSEEK_API_KEY`.
  - Una o más de `API_FOOTBALL_KEY`, `SPORTMONKS_API_TOKEN` y `FOOTBALL_DATA_API_TOKEN` para datos reales.

---

## 🔑 Configuración de Variables de Entorno

Crea el archivo `.env.development.local` en la raíz del proyecto basándote en `.env.example`:

```env
APP_ENV=development
DEBUG=true

# Motor multi-IA. Con las cuatro claves, las cuatro IAs interpretan el partido
# en paralelo y el backend agrega las selecciones que alcanzan consenso.
AI_ENABLED=true
AI_ALLOW_PAID_PROVIDERS=true
AI_PROVIDER_TIMEOUT_SECONDS=18
AI_TOTAL_TIMEOUT_SECONDS=22
AI_MAX_PROVIDER_ATTEMPTS=4

XAI_API_KEY=
XAI_BASE_URL=https://api.x.ai/v1
XAI_MODEL=grok-4.3

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

CEREBRAS_API_KEY=
CEREBRAS_BASE_URL=https://api.cerebras.ai/v1
CEREBRAS_MODEL=gpt-oss-120b

OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openrouter/free
OPENROUTER_SITE_URL=https://bet-anality-1.onrender.com

# API-SPORTS / API-Football (conexión directa, no RapidAPI)
# También se acepta el alias api-sports
SPORTS_DATA_PROVIDER=api-football
API_FOOTBALL_KEY=tu_api_sports_key_aqui
API_FOOTBALL_BASE_URL=https://v3.football.api-sports.io
API_FOOTBALL_IS_RAPIDAPI=false
API_FOOTBALL_TIMEOUT_SECONDS=10
API_FOOTBALL_ENRICHMENT_MODE=auto
API_FOOTBALL_OPTIONAL_QUOTA_RESERVE=15

# Segundo proveedor: Sportmonks Football API v3
SPORTMONKS_API_TOKEN=tu_token_sportmonks
SPORTMONKS_BASE_URL=https://api.sportmonks.com/v3/football
SPORTMONKS_TIMEOUT_SECONDS=15

# Último respaldo: Football-Data.org
FOOTBALL_DATA_API_TOKEN=tu_token_football_data
FOOTBALL_DATA_BASE_URL=https://api.football-data.org/v4
FOOTBALL_DATA_TIMEOUT_SECONDS=10

SPORTS_DATA_TOTAL_TIMEOUT_SECONDS=40
```

> **Nota**: Si no se proporcionan claves de IA, el análisis continúa con el motor estadístico local. Si falta el proveedor deportivo, la aplicación usa datos demostrativos identificados como tales.

Para activar toda la cadena en Render configura `API_FOOTBALL_KEY`,
`SPORTMONKS_API_TOKEN` y `FOOTBALL_DATA_API_TOKEN`, manteniendo
`SPORTS_DATA_PROVIDER=api-football`. Si API-Football falla, el backend prueba
Sportmonks y deja Football-Data.org siempre como último respaldo. Una agenda
vacía no se considera una caída, pero tampoco detiene la cadena: se consulta el
siguiente proveedor hasta llegar a Football-Data.org.
Los detalles e historiales se consultan después al proveedor que originó cada ID.

Sportmonks sólo devuelve fixtures de las ligas incluidas en el plan asociado al
token. Un `200` con `data: []` puede significar que ese plan no cubre partidos
para la fecha seleccionada. La agenda solicita únicamente los includes
esenciales `participants;league;state`; sede y árbitros se reservan para el
detalle, reduciendo latencia y posibles restricciones del plan. Render registra
fecha, zona horaria, cantidad bruta, cuota restante y nombre del plan, nunca el
token.

Cerebras ofrece un nivel gratuito y `openrouter/free` limita el enrutamiento a
modelos gratuitos, sujeto a sus cuotas y disponibilidad. xAI/Grok y DeepSeek no
son servicios gratuitos permanentes: `AI_ALLOW_PAID_PROVIDERS=true` permite que
participen y puede generar cargos. Cámbialo a `false` para excluirlos. GitHub Models fue retirado
por GitHub el 30 de julio de 2026, por lo que no se envía tráfico a su antiguo
endpoint; el registro del motor queda preparado para incorporar un reemplazo.

En Render, agrega todas las claves únicamente al servicio **backend** desde
`Environment`. Deben guardarse como secretos; nunca deben ponerse en el frontend,
en una variable `NEXT_PUBLIC_*` ni confirmarse en Git.
Después de guardar las variables, ejecuta un redeploy del backend.

Las referencias canónicas son la
[documentación oficial API-Football v3](https://www.api-football.com/documentation-v3),
los [fixtures de Sportmonks Football API v3](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures/get-fixtures-by-date)
y la [API v4 de Football-Data.org](https://www.football-data.org/documentation/quickstart).
El adaptador agrupa hasta veinte IDs mediante `fixtures?ids=...` para obtener los
bloques disponibles de estadísticas y jugadores sin multiplicar llamadas. Los
campos `null` se consideran datos no disponibles, nunca valores cero.
El historial usa `fixtures/headtohead?h2h={local}-{visitante}&last=10` para los
enfrentamientos directos y `fixtures?team={id}&last=10&status=FT-AET-PEN` para
la forma reciente de cada equipo. La interfaz muestra cinco inicialmente y
permite desplegar los cinco anteriores sin una segunda espera.
En el detalle se consulta `odds?fixture=...` y solo se aplican cotizaciones que
coinciden exactamente con mercado, selección, línea y periodo; las combinadas
generadas conservan su cuota justa y nunca multiplican precios individuales.

### Evidencia API-Football utilizada por el motor

El adaptador API-Football cubre el catálogo documentado completo: estado y
cuota; zonas horarias, países, ligas y temporadas; equipos, sedes, jornadas y
clasificación; fixtures, H2H, estadísticas, eventos, alineaciones y rendimiento
de jugadores; lesiones y `predictions`; entrenadores, plantillas, transferencias,
trofeos y periodos de baja; rankings de goleadores, asistencias y tarjetas; y los
catálogos/cotizaciones live y prepartido (`mapping`, bookmakers y bets).

No todos esos endpoints se convierten automáticamente en una variable
predictiva. País, zona horaria, trofeos o una imagen de sede son contexto; no se
presentan a la IA como si aumentaran por sí solos la probabilidad de ganar. El
análisis prepartido sí normaliza y contrasta:

- forma, H2H y marcadores de hasta diez partidos terminados;
- goles a favor/en contra, victorias/empates/derrotas, porterías a cero y
  partidos sin anotar;
- córners, remates, remates al arco, faltas y tarjetas con tamaño de muestra;
- posición, puntos, diferencia de gol y forma de la clasificación;
- minutos, titularidades, rating, goles, asistencias, remates, pases clave,
  recuperaciones y tarjetas de jugadores;
- bajas, dudas, sanciones y alineaciones confirmadas o probables;
- `predictions` de API-Football como señal secundaria identificada, nunca como
  verdad ni como cuota de bookmaker;
- cotizaciones que coincidan exactamente con mercado, selección y línea.

Cada bloque responde con `available`, `partial`, `unavailable` o
`not_requested`, más proveedor, endpoint, fecha y tamaño de muestra. Las cuatro
IAs reciben el mismo resumen estructurado y sólo pueden proponer familias de
mercado respaldadas por evidencia. Primero estiman probabilidad y cuota justa;
después el backend superpone la cuota verificada y calcula EV. El número real de
IAs que respondió se expone en `ai_consensus`; nunca se informa consenso de
cuatro si participaron menos proveedores.

`API_FOOTBALL_ENRICHMENT_MODE=auto` protege la cuota Free: conserva H2H, forma
enriquecida, estadísticas recientes de equipos/jugadores, bajas y odds, y activa
automáticamente standings, estadísticas completas de temporada, predictions y
rankings cuando el plan tiene una cuota amplia. `full` fuerza todo el contexto
opcional y `quota-saving` conserva siempre el perfil básico. El umbral que debe
quedar libre se configura con `API_FOOTBALL_OPTIONAL_QUOTA_RESERVE`.

Las cuotas live y sus IDs de bets se mantienen aislados del motor prepartido;
API-Football documenta catálogos diferentes y no conserva historial live. Los
datos históricos terminados usan cachés largas, mientras injuries, lineups,
predictions y odds respetan sus cadencias documentadas y un cooldown evita
repetir llamadas tras 401, 403, 429 o cuota agotada.

El navegador concede 65 segundos a agenda/recomendaciones y 90 segundos al
detalle analítico. El backend usa 10 s para API-Football, 15 s para Sportmonks,
10 s para Football-Data.org y un presupuesto de ruta deportiva de 40 s. Las
cuatro IAs comparten un plazo de 22 s y se ejecutan en paralelo, por lo que sus
tiempos no se suman.

---

## 🏃‍♂️ Cómo Ejecutar la Aplicación

### 1. Iniciar el Backend (API FastAPI)

En una terminal, navega a la carpeta `backend` y ejecuta:

```bash
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```

El servidor estará corriendo en: **[http://localhost:8000](http://localhost:8000)** (Documentación interactiva disponible en `/docs`).

---

### 2. Iniciar el Frontend (Next.js)

En otra terminal, navega a la carpeta `frontend` y ejecuta:

```bash
cd frontend
npm run dev
```

La aplicación web estará disponible en: **[http://localhost:3000](http://localhost:3000)**.

---

## 🧪 Pruebas Automatizadas

Para ejecutar la suite de pruebas del backend:

```bash
cd backend
pytest
```

---

## 🛡️ Declaración de Responsabilidad
BET ANALIZADOR proporciona análisis cuantitativos probabilísticos con fines informativos. El valor esperado (EV %) y las cuotas calculadas corresponden a estimaciones y no constituyen una garantía de resultados.
