# AGENTS.md — Mapa de agentes

## Proyecto
Tengo empezado un pipeline de datos en python para un corpus de comedia multi-fuente, con ingesta etl en tres flujos. Los datos vienen de google drive, de una base de datos supabase y de un bot de telegram. Sin gpu, sin llm de pago para la teoria y con límite de coste en los flujos de chistes. Done cuando los tres flujos procesan el corpus y validate_corpus pasa sin errores. Entrego un script de orquestación por flujo. Tengo 4 semanas.

## Modo del harness
EJECUTOR

## Agentes


### leader — modo DIRECTOR
**Scope:** Orquesta la ejecución del harness y escala al usuario cuando toca
**Tools:** ninguna definida


### planner — modo ARQUITECTO
**Scope:** Descompone objetivos en tareas atómicas y asigna complejidad a cada una
**Tools:** ninguna definida


### implementer — modo BISTURÍ
**Scope:** Implementa las tareas del backlog
**Tools:** ninguna definida


### reviewer — modo FISCAL
**Scope:** Revisa y valida el output contra los criterios de aceptación
**Tools:** ninguna definida


### integrator — modo NOTARIO
**Scope:** Formaliza en git el trabajo aprobado: rama al iniciar la tarea, commit+push+PR al cerrarla
**Tools:** ninguna definida


### watchman — modo CENTINELA
**Scope:** Verifica CI y merge tras la integración; si falla, reabre el ciclo con el reviewer
**Tools:** ninguna definida



## Flujo

1. leader →


2. planner →


3. implementer →


4. reviewer →


5. integrator →


6. watchman


