# BET ANALIZADOR

Plataforma web de inteligencia deportiva para el análisis avanzado de partidos de fútbol y la generación de pronósticos estadísticos de alto valor utilizando **API-Football / Football-Data.org** y modelos analíticos probabilísticos alimentados por **OpenAI GPT**.

---

## 🚀 Características Principales

- ⚽ **Catálogo de Partidos en Vivo y Programados**: Conexión directa a proveedores reales de datos deportivos (`Football-Data.org v4` y `API-Football v3`) para mostrar la agenda actualizada de partidos.
- 📊 **Estadísticas e Histórico (Últimos 5-10 Partidos & H2H)**: Recopilación automática de la forma reciente de cada equipo y sus enfrentamientos directos previos.
- 🟨 **Análisis del Árbitro Asignado**: Estadísticas de amonestaciones (tarjetas amarillas/rojas), faltas por partido y tendencias disciplinarias.
- 🚑 **Lesionados y Sancionados**: Detección de bajas confirmadas o dudas clave en la plantilla y evaluación de su impacto táctico.
- 📋 **Alineaciones y Formaciones**: Visualización de los esquemas tácticos (ej. 4-3-3) y nóminas de titulares/suplentes cuando se confirman los datos pre-partido.
- 🤖 **Pronósticos Analíticos con OpenAI GPT**:
  - Calibración de probabilidades por mercado (1X2, Doble Oportunidad, Total Goles, Ambos Anotan, Córners, Tarjetas).
  - Cálculo de **Cuota Justa** ($1 / \text{probabilidad}$) y **Valor Esperado (EV %)**.
  - Factores a favor y riesgos identificados para cada opción de mercado.
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
│   │   └── services/   # Servicios integrados (API-Football, OpenAI, ai_analyzer)
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
  - `OPENAI_API_KEY`: Para la generación de pronósticos con IA.
  - `FOOTBALL_DATA_API_TOKEN` o `API_FOOTBALL_KEY`: Para datos reales en tiempo real.

---

## 🔑 Configuración de Variables de Entorno

Crea el archivo `.env.development.local` en la raíz del proyecto basándote en `.env.example`:

```env
APP_ENV=development
DEBUG=true

# Clave de OpenAI
OPENAI_API_KEY=tu_openai_api_key_aqui
OPENAI_MODEL=gpt-5-mini
OPENAI_TIMEOUT_SECONDS=30

# Proveedor de Datos Deportivos (football-data o api-football)
SPORTS_DATA_PROVIDER=football-data
FOOTBALL_DATA_API_TOKEN=tu_football_data_token_aqui
FOOTBALL_DATA_BASE_URL=https://api.football-data.org/v4
FOOTBALL_DATA_TIMEOUT_SECONDS=15
```

> **Nota**: Si no se proporcionan las claves de API, el sistema funcionará automáticamente con un proveedor de datos demostrativos y un motor estadístico local sin interrumpir la interfaz.

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
