# Monitor SNIES Pregrado · Universitario

[![Dashboard](https://img.shields.io/badge/Dashboard-GitHub_Pages-960A11?style=for-the-badge)](https://dirplaneacionun.github.io/MONITOR-SNIES-PREGRADO-UNIVERSITARIO/)

Herramienta de monitoreo semanal de los programas académicos universitarios de pregrado con estado activo registrados en el Sistema Nacional de Información de la Educación Superior (SNIES) del Ministerio de Educación Nacional de Colombia. Es operada por la Dirección de Planeación de la Universidad del Norte.

Cada ejecución descarga el reporte oficial, lo normaliza, clasifica cada programa en una división académica de la Universidad del Norte, detecta los programas **nuevos**, **modificados** e **inactivos** respecto de la ejecución anterior, actualiza el histórico, publica el Dashboard y envía un reporte por correo electrónico.

- **Dashboard público:** <https://dirplaneacionun.github.io/MONITOR-SNIES-PREGRADO-UNIVERSITARIO/>
- **Guía completa de uso y administración:** [MANUAL.md](MANUAL.md)

---

## Contenido

1. [Capacidades](#1-capacidades)
2. [Arquitectura y flujo funcional](#2-arquitectura-y-flujo-funcional)
3. [Requisitos](#3-requisitos)
4. [Instalación](#4-instalación)
5. [Configuración](#5-configuración)
6. [Ejecución](#6-ejecución)
7. [Guía de uso del Dashboard](#7-guía-de-uso-del-dashboard)
8. [Estructura del proyecto](#8-estructura-del-proyecto)
9. [Datos generados](#9-datos-generados)
10. [Consideraciones operativas y de seguridad](#10-consideraciones-operativas-y-de-seguridad)
11. [Pruebas](#11-pruebas)
12. [Solución de problemas](#12-solución-de-problemas)
13. [Preguntas frecuentes](#13-preguntas-frecuentes)

---

## 1. Capacidades

| Capacidad | Descripción |
|---|---|
| Descarga automatizada | Obtiene el reporte de programas del portal del SNIES con los filtros: institución activa, programa activo, nivel académico pregrado y nivel de formación universitario. Verifica después que cada fila cumpla esos filtros. |
| Normalización | Unifica encabezados y valores (mayúsculas, sin tildes, conservando la Ñ), valida el esquema, excluye filas con código SNIES inválido o repetido y tipifica fechas y números. |
| Clasificación por división | Asigna a cada programa una división académica de la Universidad del Norte mediante un sistema de votación ponderada de tres capas, con reglas verificables y listas de revisión. |
| Detección de novedades | Compara cada ejecución con la anterior: programas nuevos (incluidos los reactivados), inactivos y modificados (créditos, municipio de oferta, nombre, número de periodos y modalidad). |
| Histórico | Conserva todas las ejecuciones en archivos Parquet, uno por año, y acumula las novedades en archivos Parquet independientes. |
| Dashboard | Sitio web estático con cuatro apartados (Inicio, Nuevos, Modificados, Inactivos), filtros encadenados, selector de fechas, gráficas interactivas, tablas ordenables y exportación a Parquet. |
| Reporte por correo | Mensaje institucional con indicadores, resumen de la ejecución, novedades por grupo de universidades y tablas de registros, en versiones HTML y texto plano. |
| Protecciones | Detiene la ejecución sin guardar datos si la descarga es incompleta, si el portal no aplicó los filtros o si falla la descarga, la normalización, la clasificación o la validación, y avisa por correo a la cuenta remitente. |
| Control de calidad | Pruebas unitarias y pruebas de punta a punta del Dashboard que se ejecutan en GitHub Actions. |

## 2. Arquitectura y flujo funcional

```
cron externo ──► GitHub Actions: SNIES Monitor (workflow_dispatch)
                       │
                       ▼
   Pruebas unitarias ──► Scripts/run_snies.py (orquestador)
                       │
   1. Descarga ........ portal SNIES (Selenium + Chrome)
   2. Normalización ... ETL-PROGRAMAS-SNIES
   3. Clasificación ... CLASIFICAR-DIVISIONES-UNINORTE-SNIES
   4. Validación ...... filtros del portal, continuidad con la ejecución anterior
   5. Detección ....... nuevos, modificados, inactivos
   6. Guardado ........ Historico/*.parquet y Novedades/*.parquet
   7. Dashboard ....... docs/data/*.js y docs/*.html
   8. Correo .......... reporte a los destinatarios
                       │
                       ▼
   Publicación en el repositorio ──► GitHub Pages (carpeta docs/)
```

El orquestador ejecuta las etapas en orden. Si una etapa falla, las siguientes no se ejecutan, se registra el error y se envía una alerta técnica únicamente a la cuenta remitente. Los datos se guardan antes de generar el Dashboard y de enviar el correo; si el correo falla, los datos y el Dashboard quedan actualizados y la ejecución termina con código de error.

## 3. Requisitos

| Componente | Requisito |
|---|---|
| Python | 3.11 o superior. Versión validada: 3.14. |
| Navegador | Google Chrome instalado (la descarga utiliza Selenium; el controlador `chromedriver` se obtiene automáticamente con `webdriver-manager`). |
| Sistema operativo | Windows, Linux o macOS. El flujo automatizado se ejecuta en Ubuntu (GitHub Actions). |
| Conectividad | Acceso a `hecaa.mineducacion.gov.co` (portal SNIES) y a `smtp.gmail.com:587` para el envío de correo. |
| Cuenta de correo | Cuenta remitente con contraseña de aplicación y lista de destinatarios (solo para el envío del reporte). |
| Repositorio | GitHub con Actions y Pages habilitados para la operación automatizada. |

Las dependencias de Python están fijadas con versiones exactas en [`requirements.txt`](requirements.txt). El clasificador verifica sus salidas con las versiones fijadas de `pandas` y `openpyxl`; no se deben actualizar sin ejecutar antes el flujo completo.

### Dependencias principales

| Biblioteca | Uso |
|---|---|
| `pandas`, `numpy`, `pyarrow` | Procesamiento de datos y archivos Parquet. |
| `openpyxl`, `python-calamine`, `xlsxwriter` | Lectura y escritura de archivos Excel. |
| `selenium`, `webdriver-manager` | Descarga automatizada del portal del SNIES. |
| `rich`, `tqdm` | Consola y progreso de las herramientas de normalización y clasificación. |
| `pillow` | Pruebas (comparación de los logotipos). |

El Dashboard incluye en `docs/assets/vendor` las librerías de JavaScript Plotly (gráficas), flatpickr (calendarios), hyparquet y hyparquet-writer (generación de archivos Parquet en el navegador); sus versiones se registran en `Librerias/package.json`.

## 4. Instalación

```powershell
git clone https://github.com/dirplaneacionun/MONITOR-SNIES-PREGRADO-UNIVERSITARIO.git
cd MONITOR-SNIES-PREGRADO-UNIVERSITARIO
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

En Linux o macOS, la activación del entorno virtual es `source .venv/bin/activate`.

## 5. Configuración

### 5.1 Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `SMTP_USER` | Para enviar correo | Dirección de la cuenta remitente. También recibe las alertas técnicas. |
| `SMTP_PASS` | Para enviar correo | Contraseña de aplicación de la cuenta remitente (no la contraseña de inicio de sesión). |
| `DESTINATARIOS` | Para enviar correo | Direcciones que reciben el reporte, separadas por coma. |
| `SNIES_HEADLESS` | No | Con el valor `0`, la descarga muestra la ventana de Chrome. Por defecto el navegador no muestra ventana. |

En GitHub Actions, las tres primeras variables se configuran como *secrets* del repositorio (`Settings → Secrets and variables → Actions`). En ejecuciones locales sin correo no es necesario definirlas.

### 5.2 Parámetros en código

| Parámetro | Archivo | Valor | Función |
|---|---|---|---|
| `CAIDA_MAXIMA` | `Scripts/run_snies.py` | 0,10 | Caída máxima de programas frente a la ejecución anterior; si se supera, la descarga se considera incompleta. |
| `UMBRAL_MAXIMO` | `Scripts/run_snies.py` | 10.000 | Número de programas a partir del cual se asume una descarga sin filtros. |
| `MAX_LOGS` | `Scripts/run_snies.py` | 10 | Registros de ejecución conservados por herramienta. |
| `RETRO_AVISO_MB` / `RETRO_CRITICO_MB` | `Scripts/run_snies.py` | 40 / 80 | Umbrales de advertencia del tamaño de `retroalimentacion.json`. |
| `SMTP_HOST` / `SMTP_PORT` | `Scripts/send_report.py` | `smtp.gmail.com` / 587 | Servidor de correo saliente. |
| `INTENTOS_SMTP` / `ESPERA_SMTP` | `Scripts/send_report.py` | 3 / 5 y 15 s | Intentos de conexión y autenticación SMTP ante fallas transitorias, y espera antes del segundo y del tercero. |
| `DASHBOARD_URL` | `Scripts/send_report.py` | URL del sitio | Enlace al Dashboard que se incluye en el correo. |
| `LIMITE_HTML` | `Scripts/send_report.py` | 95 KB | Tamaño máximo del HTML del correo. |

### 5.3 Publicación del Dashboard

En `Settings → Pages` del repositorio: **Source** = *Deploy from a branch*; **Branch** = `main`; carpeta `/docs`.

## 6. Ejecución

### 6.1 Ejecución automatizada

El flujo **SNIES Monitor** (`.github/workflows/snies_daily.yml`) se activa mediante la API de GitHub (`workflow_dispatch`), invocada por un servicio externo de programación con un *token* de acceso limitado a este repositorio. La periodicidad es semanal y se administra en ese servicio. También puede ejecutarse manualmente desde `Actions → SNIES Monitor → Run workflow`, con dos opciones:

| Opción | Valor por defecto | Efecto |
|---|---|---|
| Enviar el correo a los destinatarios | Activada | Desactivarla ejecuta todo el flujo sin enviar el reporte. |
| Aceptar una caída de más del 10 % de los programas | Desactivada | Solo debe activarse si la reducción fue confirmada en el portal del SNIES. |

### 6.2 Ejecución local

Una ejecución local escribe en `Historico/`, `Novedades/`, `Logs/` y `docs/` de la copia de trabajo. Se recomienda realizarla en una copia independiente del repositorio.

```powershell
python Scripts/run_snies.py                      # ejecución completa (requiere variables de correo)
python Scripts/run_snies.py --sin-correo         # sin enviar correos
python Scripts/run_snies.py --archivo RUTA.xlsx --fecha AAAA-MM-DD --sin-correo
                                                 # procesa un reporte ya descargado
python Scripts/run_snies.py --forzar             # acepta una caída real de más del 10 %
```

### 6.3 Herramientas complementarias

```powershell
python Scripts/generar_dashboard.py                    # regenera docs/data y docs/*.html desde Historico/ y Novedades/
python Scripts/reclasificar.py --simular               # informa qué divisiones cambiarían con las reglas vigentes
python Scripts/reclasificar.py                         # reclasifica el histórico y las novedades
python Scripts/completar_modificados.py --simular      # informa cambios del histórico no registrados en Modificados
python Scripts/completar_modificados.py                # agrega los cambios faltantes a Modificados
```

### 6.4 Vista local del Dashboard

```powershell
python -m http.server 8766 --directory docs
```

El Dashboard queda disponible en <http://localhost:8766>. Debe servirse por HTTP: abrir los archivos directamente desde el sistema de archivos no habilita la exportación a Parquet.

## 7. Guía de uso del Dashboard

El Dashboard no requiere inicio de sesión ni instalación. Sus cuatro apartados se acceden desde el menú superior:

| Apartado | Contenido |
|---|---|
| **Inicio** | Indicadores de la última ejecución, evolución y distribución de los programas activos, y tablas de novedades. |
| **Nuevos** | Programas que aparecen en el SNIES y no estaban en la ejecución anterior (incluye los reactivados). |
| **Modificados** | Un registro por cada cambio detectado en créditos, municipio de oferta, nombre, número de periodos o modalidad. |
| **Inactivos** | Programas que estaban activos en la ejecución anterior y ya no figuran en el reporte. |

**Filtros.** La barra superior es común a los cuatro apartados y permanece visible al desplazarse en pantallas de más de 640 px. Contiene, de izquierda a derecha: buscador por código de programa o institución, universidades, programas académicos, departamentos, municipios, sectores, modalidades, campos de CINE específico, divisiones, tipos de cambio (solo en Modificados) y fechas. Los filtros se combinan con la condición **Y**, se encadenan (cada lista solo ofrece los valores que tienen resultados) y actualizan de inmediato los indicadores, las gráficas y las tablas.

**Uso básico**

1. Abrir **Inicio** y revisar los indicadores de la última ejecución: programas activos, nuevos, modificados e inactivos.
2. Acotar la información con los filtros. En las listas desplegables, **AISLAR** deja marcada una sola opción; **LIMPIAR FILTROS** restablece todos los filtros.
3. Consultar las gráficas: al pasar el cursor se muestra el valor exacto; en las leyendas, un clic oculta una serie y un doble clic la aísla.
4. Revisar las tablas: un clic en un encabezado ordena la columna; **DESCARGAR** exporta a Parquet los registros filtrados.
5. Compartir una vista: los filtros se guardan en la dirección de la página, de modo que copiarla comparte la selección.

El [MANUAL](MANUAL.md#parte-2-dashboard) describe cada filtro, indicador, gráfica y tabla, y los flujos de uso paso a paso.

## 8. Estructura del proyecto

```
MONITOR-SNIES-PREGRADO-UNIVERSITARIO/
├── .github/
│   ├── dependabot.yml                  Actualización de dependencias (Python, acciones y librerías del Dashboard)
│   └── workflows/
│       ├── snies_daily.yml             Flujo semanal: pruebas, ejecución, publicación
│       ├── reclasificar.yml            Reclasificación de divisiones (manual)
│       └── pruebas_dashboard.yml       Pruebas unitarias y de punta a punta del Dashboard
├── Historico/                          Histórico de programas activos (un Parquet por año)
├── Novedades/                          Nuevos, Modificados e Inactivos acumulados (Parquet)
├── Logs/                               Registros de ejecución del orquestador y de la reclasificación
├── Librerias/                          Registro de versiones de las librerías JavaScript del Dashboard
├── Recursos Uninorte/                  Logotipos institucionales y manual de identidad visual
├── Scripts/
│   ├── run_snies.py                    Orquestador del flujo completo
│   ├── historico.py                    Lectura y escritura del histórico por año
│   ├── catalogos.py                    Grupos de universidades, modalidades y tipos de cambio
│   ├── generar_dashboard.py            Genera los datos del Dashboard
│   ├── generar_paginas.py              Genera las páginas HTML del Dashboard
│   ├── send_report.py                  Correo del reporte y alerta técnica
│   ├── reclasificar.py                 Reclasificación de divisiones del histórico
│   ├── completar_modificados.py        Completa Modificados con cambios del histórico
│   ├── ETL-PROGRAMAS-SNIES/            Normalización del reporte
│   └── CLASIFICAR-DIVISIONES-UNINORTE-SNIES/
│                                       Clasificación en divisiones de la Universidad del Norte
├── docs/                               Dashboard publicado (GitHub Pages)
│   ├── index.html, nuevos.html, modificados.html, inactivos.html
│   ├── assets/                         Código, estilos, tipografía, imágenes y librerías
│   └── data/                           Datos del Dashboard generados en cada ejecución
├── tests/
│   ├── test_unitarias.py               Pruebas unitarias
│   ├── test_etl_clasificador.py        Pruebas unitarias del ETL y del clasificador
│   └── e2e_dashboard.py                Pruebas de punta a punta del Dashboard
└── requirements.txt                    Dependencias de Python con versiones fijas
```

## 9. Datos generados

| Archivo | Contenido |
|---|---|
| `Historico/Historico Programas Activos AAAA.parquet` | Programas activos de todas las ejecuciones del año `AAAA`, con la columna `FECHA_DE_OBTENCION`. Cada ejecución ocupa un grupo de filas independiente. |
| `Novedades/Nuevos.parquet` | Programas que aparecen en una ejecución y no estaban en la anterior, con `TIPO_NOVEDAD` (`NUEVO` o `REACTIVADO`). |
| `Novedades/Modificados.parquet` | Un registro por cada cambio detectado, con `TIPO_CAMBIO_DETECTADO` y `CAMBIO_DETECTADO` (`valor anterior → valor nuevo`). |
| `Novedades/Inactivos.parquet` | Programas de la ejecución anterior que ya no figuran, con los datos que tenían. |
| `docs/data/*.js` | Datos compactos que consume el Dashboard. |
| `Scripts/*/Revision/*.xlsx` | Listas de revisión: filas excluidas por el ETL y programas que requieren revisión de su división. |

Los archivos Parquet pueden abrirse con `pandas`, Power BI, Excel (Power Query) y la mayoría de herramientas analíticas. El diccionario de datos y los criterios de detección están en el [MANUAL](MANUAL.md#anexo-a-diccionario-de-datos).

## 10. Consideraciones operativas y de seguridad

- **Repositorio público:** los registros de ejecución se publican en el repositorio. El formato de registro oculta rutas personales y direcciones de correo antes de escribirlos.
- **Credenciales:** residen únicamente en los *secrets* de GitHub. El flujo declara permisos mínimos (`contents: write`) y no conserva el *token* en la configuración de Git durante la ejecución.
- **Acciones de terceros:** están fijadas a un identificador de confirmación (SHA) exacto.
- **Dashboard:** no realiza solicitudes a servicios externos. Las librerías y la tipografía se incluyen en el repositorio y una política de seguridad de contenido restringe la ejecución a scripts propios.
- **Concurrencia:** nunca se ejecutan dos flujos que escriben datos a la vez; el siguiente espera a que termine el anterior.
- **Tamaño del repositorio:** `retroalimentacion.json` crece con cada ejecución. La herramienta registra su tamaño y advierte al superar 40 MB (GitHub advierte desde 50 MB y rechaza archivos de más de 100 MB).
- **Primera ejecución:** si el histórico no contiene una ejecución anterior, la ejecución queda como línea base: se guarda sin detectar novedades y no se envía correo.

## 11. Pruebas

```powershell
python -m unittest discover -s tests          # pruebas unitarias (segundos)
python tests/e2e_dashboard.py                 # pruebas de punta a punta (requiere Chrome; 10 a 20 minutos)
python tests/e2e_dashboard.py --rapida        # una muestra de opciones por filtro
```

- Las **pruebas unitarias** se ejecutan antes de cada ejecución semanal y de cada reclasificación: si alguna falla, no se modifica ningún dato.
- Las **pruebas de punta a punta** operan el Dashboard con un navegador real y comparan cada indicador, gráfica y tabla con un cálculo independiente sobre los archivos de `Historico/` y `Novedades/`. Se ejecutan automáticamente en las solicitudes de cambios y al publicar cambios en `main` que afecten al Dashboard, sus generadores o las pruebas.

## 12. Solución de problemas

| Síntoma | Causa probable | Acción |
|---|---|---|
| La ejecución termina con «No se guardó ningún dato» y una caída de programas | La descarga fue incompleta o la reducción es real. | Verificar el reporte en el portal del SNIES. Si la reducción es real, ejecutar con la opción de forzar (`--forzar` o la opción del flujo). |
| «El portal no aplicó los filtros esperados» | Cambio en el portal del SNIES o falla de los controles de filtro. | Revisar el portal; ajustar la descarga en `Scripts/run_snies.py` si su estructura cambió. |
| Falla en la etapa de descarga | El portal tardó o cambió su estructura. | Repetir la ejecución; con `SNIES_HEADLESS=0` puede observarse el navegador. |
| Error de autenticación SMTP (535) | Se usó la contraseña de inicio de sesión en lugar de una contraseña de aplicación, o el secreto es incorrecto. | Generar una contraseña de aplicación y actualizar `SMTP_PASS`. |
| El correo llega a menos destinatarios que los configurados | El servidor rechazó direcciones. | El registro indica la posición y el código; revisar `DESTINATARIOS`. |
| El correo muestra «Se muestran N de M registros» | La ejecución tuvo más registros de los que caben en el límite del mensaje. | El detalle completo está en el Dashboard mediante el enlace del correo. |
| Los datos se actualizaron pero el Dashboard no | La publicación en GitHub Pages no está configurada o se encuentra en curso. | Verificar `Settings → Pages` y el estado de la publicación. |
| Falla el paso «Publicar cambios» por conflicto | Otra persona publicó cambios en los mismos archivos durante la ejecución. | Integrar los cambios y volver a ejecutar el flujo. |
| El Dashboard muestra «Sin datos para los filtros seleccionados» | La combinación de filtros no tiene registros. | Pulsar **LIMPIAR FILTROS** o ampliar la selección. |

Los registros detallados se encuentran en `Logs/` y, para cada herramienta, en su carpeta `Logs/`. La página de cada ejecución en GitHub Actions muestra además el resumen de la ejecución.

## 13. Preguntas frecuentes

**¿Con qué periodicidad se actualiza la información?**
Semanalmente. Cada ejecución queda identificada por su fecha de obtención.

**¿Qué universo de programas se monitorea?**
Programas con estado activo, de instituciones con estado activo, de nivel académico pregrado y nivel de formación universitario, de todo el país.

**¿Cómo se identifica un programa?**
Por el código SNIES del programa. Todas las comparaciones entre ejecuciones se hacen por ese código.

**¿Cómo se clasifica un programa en una división de la Universidad del Norte?**
Mediante un sistema de votación ponderada de tres capas (código SNIES conocido, nombre del programa y campos de clasificación CINE y NBC), con reglas y excepciones documentadas en el clasificador. Los programas de la propia Universidad del Norte conservan la división definida en su archivo de referencia.

**¿Dónde se consulta el detalle completo de los programas activos de una ejecución?**
En los archivos `Historico/Historico Programas Activos AAAA.parquet`. El Dashboard presenta las tablas de nuevos, modificados e inactivos.

**¿Cómo se reclasifica el histórico tras ajustar las reglas de clasificación?**
Con el flujo **Reclasificar divisiones** (`Actions → Reclasificar divisiones`), que permite simular antes de publicar, o con `python Scripts/reclasificar.py`.

**¿Se puede ejecutar la herramienta sin enviar correo?**
Sí: con `--sin-correo` en ejecución local o desactivando la opción de correo al ejecutar el flujo manualmente.

---

Fuente de los datos: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de Educación Nacional. Dirección de Planeación, Universidad del Norte.
