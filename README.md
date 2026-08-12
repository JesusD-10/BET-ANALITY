# BET ANALIZADOR

Plataforma web de inteligencia deportiva para el análisis avanzado de partidos de fútbol y la generación de pronósticos estadísticos utilizando **API-Football / Football-Data.org**, un motor probabilístico local y proveedores de IA intercambiables.

---

## 🚀 Características Principales

- ⚽ **Catálogo con respaldo automático**: API-Football es la fuente principal y Football-Data.org toma el relevo ante suspensión, límite de cuota, credenciales rechazadas, timeout o respuesta inválida. La fuente efectiva siempre se identifica en la respuesta.
- 🏟️ **Portada curada por liga**: muestra hasta 12 partidos de clubes y selecciones populares, con máximo 4 por competición. La búsqueda y `/partidos` conservan acceso al catálogo completo.
- 📊 **Estadísticas e Histórico**: Tres vistas independientes con los últimos 5 H2H, los últimos 5 partidos del local y los últimos 5 del visitante.
- 🟨 **Análisis del Árbitro Asignado**: Estadísticas de amonestaciones (tarjetas amarillas/rojas), faltas por partido y tendencias disciplinarias.
- 🚑 **Lesionados y Sancionados**: Detección de bajas confirmadas o dudas clave en la plantilla y evaluación de su impacto táctico.
- 📋 **Alineaciones y Formaciones**: Once probable calculado desde el uso reciente; desde T-60 se consulta el dato oficial y solo se confirma un equipo cuando el proveedor entrega formación y 11 titulares válidos.
- 🤖 **Motor multi-IA** (xAI/Grok, DeepSeek, Cerebras y OpenRouter):
  - Contrasta en paralelo hasta dos proveedores para el análisis y usa rotación/failover para el asistente, siempre dentro de un límite total de tiempo.
  - Mantiene un motor local cuando no hay claves, cuota o respuesta externa.
  - El backend valida mercados y evidencia, promedia estimaciones coincidentes y recalcula **Cuota Justa** ($1 / \text{probabilidad}$) y **Valor Esperado (EV %)**; ninguna IA puede imponer cuotas de una casa.
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
│   │   └── services/   # API-Football, orquestador multi-IA y motor analítico
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
  - `FOOTBALL_DATA_API_TOKEN` o `API_FOOTBALL_KEY`: Para datos reales en tiempo real.

---

## 🔑 Configuración de Variables de Entorno

Crea el archivo `.env.development.local` en la raíz del proyecto basándote en `.env.example`:

```env
APP_ENV=development
DEBUG=true

# Motor multi-IA. Basta configurar una clave; varias permiten distribuir carga
# y continuar con otro proveedor si alguno queda sin cuota.
AI_ENABLED=true
AI_ALLOW_PAID_PROVIDERS=false
AI_PROVIDER_TIMEOUT_SECONDS=4
AI_TOTAL_TIMEOUT_SECONDS=5
AI_MAX_PROVIDER_ATTEMPTS=3

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
API_FOOTBALL_TIMEOUT_SECONDS=3

# Respaldo automático: Football-Data.org
FOOTBALL_DATA_API_TOKEN=tu_token_football_data
FOOTBALL_DATA_BASE_URL=https://api.football-data.org/v4
FOOTBALL_DATA_TIMEOUT_SECONDS=2
```

> **Nota**: Si no se proporcionan claves de IA, el análisis continúa con el motor estadístico local. Si falta el proveedor deportivo, la aplicación usa datos demostrativos identificados como tales.

Para activar el respaldo en Render deben existir simultáneamente
`API_FOOTBALL_KEY` y `FOOTBALL_DATA_API_TOKEN`, manteniendo
`SPORTS_DATA_PROVIDER=api-football`. Si API-Football falla, el backend prueba
Football-Data automáticamente; una agenda vacía válida no se considera un fallo.
Los detalles e historiales se consultan después al proveedor que originó cada ID.

Cerebras ofrece un nivel gratuito y `openrouter/free` limita el enrutamiento a
modelos gratuitos, sujeto a sus cuotas y disponibilidad. xAI/Grok y DeepSeek no
son servicios gratuitos permanentes: solo deben configurarse si la cuenta tiene
saldo, crédito promocional o facturación habilitada. Con
`AI_ALLOW_PAID_PROVIDERS=false`, el backend los ignora aunque sus claves estén
configuradas, evitando cobros accidentales. GitHub Models fue retirado
por GitHub el 30 de julio de 2026, por lo que no se envía tráfico a su antiguo
endpoint; el registro del motor queda preparado para incorporar un reemplazo.

En Render, agrega todas las claves únicamente al servicio **backend** desde
`Environment`. Deben guardarse como secretos; nunca deben ponerse en el frontend,
en una variable `NEXT_PUBLIC_*` ni confirmarse en Git.
Después de guardar las variables, ejecuta un redeploy del backend.

La referencia canónica de endpoints y parámetros es la
[documentación oficial API-Football v3](https://www.api-football.com/documentation-v3).
El adaptador agrupa hasta cinco IDs mediante `fixtures?ids=...` para obtener los
bloques disponibles de estadísticas y jugadores sin multiplicar llamadas. Los
campos `null` se consideran datos no disponibles, nunca valores cero.
En el detalle se consulta `odds?fixture=...` y solo se aplican cotizaciones que
coinciden exactamente con mercado, selección, línea y periodo; las combinadas
generadas conservan su cuota justa y nunca multiplican precios individuales.

Todas las solicitudes del navegador se cancelan a los 10 segundos. Las llamadas del backend usan presupuestos menores y fallback local para evitar esperas prolongadas.

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
