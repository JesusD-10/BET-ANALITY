# BET ANALIZADOR

Plataforma web de inteligencia deportiva para el análisis avanzado de partidos de fútbol y la generación de pronósticos estadísticos utilizando **API-Football, Sportmonks y Football-Data.org**, un motor probabilístico local y proveedores de IA intercambiables.

---

## 🚀 Características Principales

- ⚽ **Catálogo con triple respaldo automático**: la cadena predeterminada es API-Football → Sportmonks → Football-Data.org. Ante credenciales rechazadas, límite de cuota, timeout, error de red, respuesta inválida o una agenda vacía se prueba el siguiente proveedor; la fuente efectiva siempre se identifica.
- 🏟️ **Portada curada por liga**: muestra hasta 12 partidos de clubes y selecciones populares, con máximo 4 por competición. La búsqueda y `/partidos` conservan acceso al catálogo completo.
- 📊 **Estadísticas e Histórico**: Tres vistas independientes con los últimos 5 H2H, los últimos 5 partidos del local y los últimos 5 del visitante.
- 🟨 **Análisis del Árbitro Asignado**: Estadísticas de amonestaciones (tarjetas amarillas/rojas), faltas por partido y tendencias disciplinarias.
- 🚑 **Lesionados y Sancionados**: Detección de bajas confirmadas o dudas clave en la plantilla y evaluación de su impacto táctico.
- 📋 **Alineaciones y Formaciones**: Once probable calculado desde el uso reciente; desde T-60 se consulta el dato oficial y solo se confirma un equipo cuando el proveedor entrega formación y 11 titulares válidos.
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
El adaptador agrupa hasta cinco IDs mediante `fixtures?ids=...` para obtener los
bloques disponibles de estadísticas y jugadores sin multiplicar llamadas. Los
campos `null` se consideran datos no disponibles, nunca valores cero.
En el detalle se consulta `odds?fixture=...` y solo se aplican cotizaciones que
coinciden exactamente con mercado, selección, línea y periodo; las combinadas
generadas conservan su cuota justa y nunca multiplican precios individuales.

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
