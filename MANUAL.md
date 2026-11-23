# Manual de uso y administración · Monitor SNIES Pregrado · Universitario

Guía operativa de la herramienta de monitoreo de programas académicos universitarios de pregrado del Sistema Nacional de Información de la Educación Superior (SNIES). Está dirigida a dos perfiles:

- **Usuarios del Dashboard y del correo** (partes 1 a 3): consulta y análisis de la información.
- **Administradores** (partes 4 a 8): operación, configuración, mantenimiento y control de calidad.

La descripción general, los requisitos y la instalación se encuentran en el [README](README.md).

---

## Contenido

**Parte 1. Conceptos fundamentales**
1. [Propósito y alcance](#1-propósito-y-alcance)
2. [Conceptos y terminología](#2-conceptos-y-terminología)

**Parte 2. Dashboard**

3. [Estructura general](#3-estructura-general)
4. [Filtros](#4-filtros)
5. [Apartado Inicio](#5-apartado-inicio)
6. [Apartados Nuevos, Modificados e Inactivos](#6-apartados-nuevos-modificados-e-inactivos)
7. [Gráficas: interacción común](#7-gráficas-interacción-común)
8. [Tablas y exportación](#8-tablas-y-exportación)
9. [Vistas compartibles (parámetros de la dirección)](#9-vistas-compartibles-parámetros-de-la-dirección)
10. [Adaptación a pantallas y accesibilidad](#10-adaptación-a-pantallas-y-accesibilidad)
11. [Flujos de uso paso a paso](#11-flujos-de-uso-paso-a-paso)

**Parte 3. Correo electrónico**

12. [Reporte semanal y alerta técnica](#12-reporte-semanal-y-alerta-técnica)

**Parte 4. Operación**

13. [Flujo de ejecución](#13-flujo-de-ejecución)
14. [Programación y ejecución manual](#14-programación-y-ejecución-manual)
15. [Validaciones y protecciones](#15-validaciones-y-protecciones)

**Parte 5. Configuración**

16. [Secretos, destinatarios y cuenta de correo](#16-secretos-destinatarios-y-cuenta-de-correo)
17. [Publicación en GitHub Pages](#17-publicación-en-github-pages)
18. [Ejecución local](#18-ejecución-local)

**Parte 6. Datos y clasificación**

19. [Datos almacenados](#19-datos-almacenados)
20. [Clasificación en divisiones](#20-clasificación-en-divisiones)
21. [Mantenimiento de datos](#21-mantenimiento-de-datos)

**Parte 7. Calidad, seguridad y dependencias**

22. [Pruebas y control de calidad](#22-pruebas-y-control-de-calidad)
23. [Seguridad y privacidad](#23-seguridad-y-privacidad)
24. [Dependencias y actualizaciones](#24-dependencias-y-actualizaciones)

**Parte 8. Referencia**

25. [Limitaciones conocidas](#25-limitaciones-conocidas)
26. [Buenas prácticas](#26-buenas-prácticas)
27. [Solución de problemas](#27-solución-de-problemas)
28. [Glosario](#28-glosario)

**Anexos**: [A. Diccionario de datos](#anexo-a-diccionario-de-datos) · [B. Parámetros de la dirección](#anexo-b-parámetros-de-la-dirección) · [C. Comandos](#anexo-c-comandos) · [D. Constantes de configuración](#anexo-d-constantes-de-configuración)

---

# Parte 1. Conceptos fundamentales

## 1. Propósito y alcance

La herramienta vigila el universo de programas académicos que cumplen simultáneamente estas condiciones en el SNIES:

| Condición | Valor |
|---|---|
| Estado de la institución | Activa |
| Estado del programa | Activo |
| Nivel académico | Pregrado |
| Nivel de formación | Universitario |

Cada semana obtiene el reporte oficial, lo compara con la ejecución anterior y publica los resultados en tres canales:

| Canal | Contenido |
|---|---|
| **Dashboard** | Evolución, distribución y detalle de los programas activos y de las novedades. |
| **Correo electrónico** | Resumen de la ejecución y registros de las novedades. |
| **Archivos de datos** | Histórico y novedades en formato Parquet, para análisis propios. |

## 2. Conceptos y terminología

### 2.1 Términos principales

| Término | Definición |
|---|---|
| **Ejecución** | Una descarga del reporte del SNIES procesada por la herramienta. Se identifica por su fecha de obtención (`FECHA_DE_OBTENCION`). |
| **Programa activo** | Programa que figura en el reporte de una ejecución. |
| **Registro** | Una fila de datos. En el histórico, un programa en una ejecución; en las novedades, un hecho detectado (un programa nuevo, un cambio o una inactivación). |
| **Código SNIES** | Identificador único de un programa (`CODIGO_SNIES_DEL_PROGRAMA`). Todas las comparaciones entre ejecuciones se realizan por este código. |
| **Novedad** | Diferencia detectada entre una ejecución y la inmediatamente anterior: nuevo, modificado o inactivo. |
| **Histórico** | Conjunto de todas las ejecuciones, con todas las columnas del reporte. |
| **Institución padre** | Institución principal a la que pertenece una sede o seccional (`CODIGO_INSTITUCION_PADRE`). Los grupos de universidades se definen por esta institución. |

### 2.2 Tipos de novedad

| Novedad | Regla de detección |
|---|---|
| **Nuevo** | Código SNIES presente en la ejecución y ausente en la anterior. Si el código ya había existido antes (en el histórico o en Inactivos), se clasifica como **REACTIVADO**; en caso contrario, como **NUEVO**. |
| **Inactivo** | Código SNIES presente en la ejecución anterior y ausente en la actual. Se conserva el registro que tenía. |
| **Modificado** | Código SNIES presente en ambas ejecuciones cuyo valor cambió en alguna de las cinco columnas vigiladas. Se genera **un registro por cada columna que cambió**. |

Columnas vigiladas para detectar modificaciones:

| Tipo de cambio | Columna comparada |
|---|---|
| Número de créditos | `NUMERO_CREDITOS` |
| Municipio de oferta | `MUNICIPIO_OFERTA_PROGRAMA` |
| Nombre del programa | `NOMBRE_DEL_PROGRAMA` |
| Número de periodos | `NUMERO_PERIODOS_DE_DURACION` |
| Modalidad | `MODALIDAD` |

Reglas de comparación:

- Los valores se comparan como texto normalizado: `160`, `160.0` y `"160"` son el mismo valor.
- Si un dato desaparece (queda vacío) en la ejecución actual, no se registra como cambio.
- Si el valor de la ejecución anterior estaba vacío, se compara con el último valor conocido del programa.
- Los cambios en el resto de las columnas (por ejemplo, costo de matrícula o resolución) no se detectan como modificaciones; sí quedan reflejados en el histórico.
- El formato del cambio detectado es `valor anterior → valor nuevo` (por ejemplo, `162 → 144`).

### 2.3 Grupos de universidades

Los registros se agrupan por la institución padre:

| Grupo | Instituciones |
|---|---|
| **Universidad del Norte** | Universidad del Norte (código 1713). |
| **Universidades de Mayor Interés** | Colegio de Estudios Superiores de Administración (CESA), Colegio Mayor de Nuestra Señora del Rosario, Corporación Universidad de la Costa (CUC), Pontificia Universidad Javeriana, Universidad de La Sabana, Universidad de los Andes, Universidad EAFIT, Universidad Libre, Universidad Externado de Colombia, Universidad Icesi, Universidad Sergio Arboleda y Universidad Simón Bolívar. |
| **Otras universidades** | Las demás instituciones. |

Las sedes y seccionales (por ejemplo, de la Universidad Libre) se cuentan en el grupo de su institución principal.

### 2.4 Modalidad agrupada

Las gráficas y el filtro de modalidades agrupan los valores originales del SNIES:

| Grupo | Valores originales incluidos |
|---|---|
| **Presencial** | Presencial. |
| **Virtual** | Virtual y sus combinaciones con presencial, a distancia o dual. |
| **A distancia** | A distancia y sus combinaciones con presencial o dual. |
| **Híbrida** | Híbrida (a distancia-virtual), híbrida (dual-virtual) e híbrida (presencial-virtual). |
| **Dual** | Dual y sus combinaciones con presencial o a distancia. |
| **Sin información** | Sin metodología definida o valor vacío. |

Las tablas conservan siempre el valor original de la modalidad.

### 2.5 Divisiones de la Universidad del Norte

Cada programa se asocia a una división académica de la Universidad del Norte (columna `DIVISION_UNINORTE`):

Ciencias Básicas · Ciencias de la Salud · Derecho, Ciencia Política y Relaciones Internacionales · Escuela de Arquitectura, Urbanismo y Diseño · Escuela de Negocios · Humanidades, Artes y Ciencias Sociales · Ingenierías · Instituto de Educación · Instituto de Idiomas · Otro (programas sin división correspondiente).

El procedimiento de asignación se describe en la [sección 20](#20-clasificación-en-divisiones).

### 2.6 Convenciones de presentación

- Los números usan punto como separador de miles (`5.526`) y las fechas el formato `DD/MM/AAAA`.
- Los valores de los datos provienen del SNIES normalizado: mayúsculas y sin tildes (`INGENIERIAS`). Los textos de la interfaz sí llevan tildes.
- Las columnas de las tablas del Dashboard se rotulan con el nombre del campo de los datos, con espacios en lugar de guiones bajos.

---

# Parte 2. Dashboard

## 3. Estructura general

El Dashboard es un sitio web estático: <https://dirplaneacionun.github.io/MONITOR-SNIES-PREGRADO-UNIVERSITARIO/>. No requiere inicio de sesión ni instalación, y no realiza solicitudes a servicios externos.

Todas las páginas comparten la misma estructura:

| Zona | Contenido |
|---|---|
| **Encabezado** | Logotipo institucional, título de la página, subtítulo y un cuadro a la derecha: *ÚLTIMA EJECUCIÓN* con la fecha en Inicio, o *TOTAL* con el número de registros filtrados en las páginas de detalle. |
| **Menú** | Cuatro apartados: **INICIO**, **NUEVOS**, **MODIFICADOS** e **INACTIVOS**. El apartado activo se destaca con una línea roja. |
| **Barra de filtros** | Buscador, filtros desplegables, selector de fechas, botón **LIMPIAR FILTROS** y contador de registros. Permanece visible al desplazarse por la página (en pantallas de más de 640 px). |
| **Contenido** | Indicadores (solo en Inicio), tarjetas de gráficas y tablas. |
| **Pie** | Logotipo y fuente de los datos. |

Los datos se actualizan cada vez que la herramienta se ejecuta. Los filtros y las gráficas se actualizan de inmediato al interactuar, sin recargar la página.

### 3.1 Qué muestra cada apartado

| Apartado | Universo de registros | Pregunta que responde |
|---|---|---|
| **Inicio** | Programas activos en cada ejecución y las tres novedades. | ¿Cuál es el estado general de la oferta y cómo evoluciona? |
| **Nuevos** | Registros de programas nuevos y reactivados. | ¿Qué programas aparecieron, dónde y cuándo? |
| **Modificados** | Registros de cambios en los programas. | ¿Qué cambió, en qué instituciones y en qué sentido? |
| **Inactivos** | Registros de programas que dejaron de figurar. | ¿Qué programas dejaron de ofertarse? |

## 4. Filtros

La barra de filtros es común a los cuatro apartados y se conserva al pasar de uno a otro. Todos los filtros actúan de forma simultánea y se combinan con la condición **Y**: un registro se muestra solo si cumple todos los filtros activos.

### 4.1 Lista de filtros

| Filtro | Campo que filtra | Apartados |
|---|---|---|
| Buscador | Código SNIES del programa o código de la institución padre | Todos |
| Universidades | Institución padre, en tres grupos | Todos |
| Programas académicos | Nombre del programa | Todos |
| Departamentos | Departamento de oferta | Todos |
| Municipios | Municipio de oferta | Todos |
| Sectores | Oficial o privado | Todos |
| Modalidades | Modalidad agrupada | Todos |
| Campos de CINE específico | Campo específico de la clasificación CINE F 2013 A.C., agrupado por campo amplio | Todos |
| Divisiones | División de la Universidad del Norte | Todos |
| Cambios | Tipo de cambio detectado | Solo Modificados |
| Fechas | Fecha de ejecución | Todos |

### 4.2 Buscador

- Campo de texto con el rótulo *Buscar por Código de Programa o Institución*.
- Realiza una **coincidencia exacta** con el código SNIES del programa o con el código de la institución padre. No busca por nombre.
- Se ejecuta con la lupa o con la tecla **Enter**. Para quitar la búsqueda, se borra el texto (la **×** del campo) y se presiona Enter o la lupa.
- Si el código no existe, las gráficas muestran «Sin datos para los filtros seleccionados» y los indicadores en cero.

### 4.3 Filtros desplegables de selección múltiple

Departamentos, municipios, sectores, modalidades, divisiones, tipo de cambio y programas académicos comparten el mismo comportamiento:

| Elemento | Comportamiento |
|---|---|
| **Botón** | Muestra el estado: la etiqueta general (por ejemplo, *TODOS LOS DEPARTAMENTOS*), el nombre de la opción si hay una sola, o la cantidad si hay varias (por ejemplo, *3 DEPARTAMENTOS*). Si hay restricción, se resalta en rojo. Al pasar el cursor se muestran las opciones seleccionadas (hasta 15; con más, su cantidad). |
| **Casilla TODOS** | Seleccionarla marca todas las opciones (sin restricción). No se puede desmarcar: el filtro debe conservar al menos una opción. |
| **Casillas de opciones** | Marcan o desmarcan cada opción. El filtro no admite quedar sin ninguna opción disponible marcada; esa acción se bloquea y el panel muestra el aviso *Debe permanecer al menos una opción seleccionada.* |
| **AISLAR** | Botón que aparece al pasar el cursor sobre una opción (siempre visible en pantallas táctiles). Deja marcada únicamente esa opción. |
| **Cuadro de búsqueda del panel** | En los filtros con lista extensa, filtra las opciones por texto, sin distinguir tildes ni mayúsculas. |
| **Cierre** | El panel se cierra al elegir otro filtro, al hacer clic fuera de él o con la tecla **Esc**. |

Los textos de los filtros por defecto son: *TODAS LAS UNIVERSIDADES*, *TODOS LOS PROGRAMAS ACADÉMICOS*, *TODOS LOS DEPARTAMENTOS*, *TODOS LOS MUNICIPIOS*, *TODOS LOS SECTORES*, *TODAS LAS MODALIDADES*, *TODOS LOS CAMPOS DE CINE ESPECÍFICO*, *TODAS LAS DIVISIONES*, *TODOS LOS CAMBIOS* y *TODAS LAS FECHAS*.

En el filtro de programas, los códigos SNIES que comparten el mismo nombre forman una sola opción; el filtro de universidades acota sus resultados a la institución elegida.

### 4.4 Filtros jerárquicos

**Universidades.** Presenta tres grupos: *UNIVERSIDAD DEL NORTE*, *UNIVERSIDADES DE MAYOR INTERÉS* y *OTRAS UNIVERSIDADES*. Los dos últimos se expanden con la flecha **▸** para elegir instituciones individuales. La casilla de un grupo queda marcada, parcial o vacía según las instituciones disponibles que contiene. **AISLAR** en un grupo selecciona todas sus instituciones. Si dos instituciones tienen el mismo nombre, se distinguen con su código entre paréntesis. El cuadro de búsqueda permite buscar por nombre o por código de institución; si el nombre de un grupo coincide, se muestra con todas sus instituciones.

**Campos de CINE específico.** Presenta los campos amplios de la clasificación CINE y, al expandirlos, sus campos específicos. Funciona igual que el filtro de universidades. Su selección se comparte con el desplegable de la gráfica de evolución por campo de CINE (véase la [sección 5.3](#53-gráficas-de-inicio)).

### 4.5 Filtros dependientes

Las opciones de cada filtro se recalculan tras cada cambio: solo se ofrecen los valores que existen en algún registro que cumpla todos los demás filtros. Por ejemplo, al elegir el departamento *ATLÁNTICO*, el filtro de municipios solo muestra los municipios de ese departamento, y el de fechas solo habilita las fechas con registros. Así nunca se ofrecen combinaciones sin resultados.

Si una selección previa deja de ser compatible con los demás filtros (por ejemplo, al abrir un enlace guardado), el filtro se restablece a *TODOS*.

### 4.6 Selector de fechas

Filtra por **fecha de ejecución**. El botón muestra *TODAS LAS FECHAS* o la selección vigente; una **×** roja junto al botón permite restablecerlo. El panel tiene cuatro modos, pero solo existe un tipo de selección vigente: al elegir con otro modo, reemplaza a la anterior.

| Modo | Selección | Detalles |
|---|---|---|
| **FECHA** | Una o varias ejecuciones | Calendario donde solo se habilitan los días con ejecución disponible. Un nuevo clic sobre una fecha seleccionada la quita. |
| **RANGO** | Todas las ejecuciones entre dos fechas | Se elige la fecha inicial y la final; se incluyen todas las ejecuciones intermedias. Los días con ejecución se destacan en negrita. El panel se cierra al completar el rango. |
| **MES** | Uno o varios meses | Cuadrícula de meses por año; solo se habilitan los meses con ejecuciones. La selección puede no ser contigua. |
| **AÑO** | Uno o varios años | Cuadrícula de años con ejecuciones. |

El botón **TODAS LAS FECHAS** del pie del panel restablece el filtro y cierra el panel.

### 4.7 Restablecer y conteo

- **LIMPIAR FILTROS** restablece todos los filtros (incluido el buscador y las fechas) a su estado inicial.
- El **contador** de la derecha indica el número de registros que cumplen los filtros. En Inicio corresponde a los programas activos en la última ejecución de la selección. En las páginas de detalle corresponde a los registros del apartado; en Modificados añade el número de programas distintos (por ejemplo, `1.466 registros · 1.084 programas distintos`).

## 5. Apartado Inicio

### 5.1 Cómo se interpretan los filtros en Inicio

- Los filtros se aplican a los programas activos de cada ejecución y a las tres tablas de novedades.
- El **corte** es la última ejecución de la selección de fechas. Los indicadores de programas activos, la distribución por sector, los diez principales departamentos y universidades y la distribución por periodos corresponden al estado de los programas en esa ejecución.
- Las gráficas de evolución muestran una serie por cada ejecución de la selección.
- Con *TODAS LAS FECHAS*, el corte es la última ejecución disponible.
- Si la selección de fechas no contiene ninguna ejecución con programas activos, las gráficas de evolución muestran «Sin ejecuciones con programas activos en el periodo seleccionado».

### 5.2 Indicadores

Cuatro tarjetas en la parte superior. Las tarjetas de novedades son enlaces al apartado correspondiente y conservan los filtros vigentes.

| Indicador | Valor | Detalle que se muestra |
|---|---|---|
| **PROGRAMAS ACTIVOS (ÚLTIMA EJECUCIÓN)** | Número de programas activos en el corte, con los filtros aplicados. | Variación con signo frente a la ejecución anterior (`+5 frente al DD/MM/AAAA`), `Sin variación frente al DD/MM/AAAA`, o `Programas activos al DD/MM/AAAA` si no hay ejecución anterior. |
| **NUEVOS (ÚLTIMA EJECUCIÓN)** | Registros nuevos y reactivados de la última ejecución de la selección. | `Ejecución del DD/MM/AAAA`. |
| **MODIFICADOS (ÚLTIMA EJECUCIÓN)** | Registros de cambio de la última ejecución de la selección. | Número de programas distintos y fecha (`9 programas distintos · DD/MM/AAAA`). |
| **INACTIVOS (ÚLTIMA EJECUCIÓN)** | Registros de programas inactivos de la última ejecución de la selección. | `Ejecución del DD/MM/AAAA`. |

Los colores de borde de las tarjetas identifican cada estado: negro (activos), verde (nuevos), amarillo (modificados) y rojo (inactivos).

### 5.3 Gráficas de Inicio

| Gráfica | Tipo | Qué representa |
|---|---|---|
| **Evolución de programas académicos activos** | Línea | Número de programas activos en cada ejecución. El eje vertical no parte de cero, para apreciar la variación. |
| **Evolución de programas académicos nuevos, modificados e inactivos** | Líneas acumuladas | Registros de novedades acumulados desde el inicio del periodo. La pendiente muestra el ritmo de aperturas, cambios y cierres. Empieza en la primera ejecución con novedades. |
| **Programas académicos activos por sector** | Anillo | Distribución entre instituciones oficiales y privadas en el corte, con el total al centro. |
| **Evolución de programas académicos activos por sector** | Líneas | Programas activos oficiales y privados en cada ejecución. |
| **Departamentos con mayor número de programas académicos activos** | Barras horizontales | Los diez departamentos de oferta con más programas en el corte. |
| **Universidades con mayor número de programas académicos activos** | Barras horizontales | Las diez instituciones con más programas en el corte. |
| **Evolución de programas académicos activos por modalidad** | Líneas | Una serie por modalidad agrupada. |
| **Programas académicos activos por número de periodos de duración** | Barras verticales | Cantidad de programas según su duración en periodos académicos (semestres o equivalentes) en el corte. |
| **Evolución de programas académicos activos por campo de CINE específico** | Líneas | Hasta diez campos (véase más abajo). |
| **Evolución de programas académicos activos por división de Uninorte** | Líneas | Una serie por división; el color de cada división es el mismo en todas las páginas. |

**Gráfica por campo de CINE específico.** Bajo la descripción hay un desplegable jerárquico que comparte su selección con el filtro superior de campos de CINE: marcar o desmarcar en uno se refleja en el otro y en toda la página. Con *TODOS* marcado, la gráfica muestra los diez campos con más registros; con una selección, muestra los campos marcados y, si son más de diez, solo los diez con más registros. Cada campo conserva su color.

### 5.4 Tablas de novedades

Una tarjeta con tres pestañas: **NUEVOS**, **MODIFICADOS** e **INACTIVOS**, cada una con el número de registros que cumplen los filtros. Los filtros de la página afectan siempre las tres tablas, aunque solo una esté visible. Las pestañas pueden recorrerse con las flechas del teclado. El funcionamiento de las tablas se describe en la [sección 8](#8-tablas-y-exportación).

## 6. Apartados Nuevos, Modificados e Inactivos

Los tres apartados comparten estructura. Los filtros se aplican a los registros del apartado, y el cuadro *TOTAL* del encabezado muestra el número de registros filtrados. Las gráficas temporales empiezan en la primera ejecución con registros de su métrica, y las evoluciones son **acumuladas**: cada punto suma los registros desde el inicio del periodo seleccionado.

### 6.1 Gráficas comunes

| Gráfica | Tipo |
|---|---|
| **Evolución de programas académicos [nuevos / modificados / inactivos]** | Línea acumulada, con el color del apartado (verde, amarillo —ocre en líneas— o rojo). |
| **Programas académicos [...] por sector** | Anillo. |
| **Evolución de programas académicos [...] por sector** | Líneas acumuladas. |
| **Departamentos con mayor número de programas académicos [...]** | Diez barras horizontales. |
| **Universidades con mayor número de programas académicos [...]** | Diez barras horizontales. |
| **Evolución de programas académicos [...] por modalidad** | Líneas acumuladas por modalidad agrupada. |
| **Programas académicos [...] por número de periodos de duración** | Barras verticales. |
| **Evolución de programas académicos [...] por campo de CINE específico** | Líneas acumuladas, con el desplegable compartido con el filtro. |
| **Evolución de programas académicos [...] por división de Uninorte** | Líneas acumuladas por división. |
| **Registros de programas académicos [...]** | Tabla (sección 8). |

### 6.2 Gráficas exclusivas de Modificados

| Gráfica | Qué representa |
|---|---|
| **Evolución de programas académicos modificados por tipo de cambio** | Líneas acumuladas, una por tipo de cambio (número de créditos, municipio de oferta, nombre del programa, número de periodos y modalidad). Se ubica a la derecha de la evolución general. |
| **Transiciones de modalidad: anterior → nueva** | Mapa de calor: cada fila es la modalidad agrupada anterior y cada columna la nueva; la celda indica el número de cambios. Solo aparecen las modalidades involucradas. |
| **Aumentos y reducciones de número de créditos en programas académicos** | Barras por ejecución: hacia arriba (negro), registros en que los créditos aumentaron; hacia abajo (amarillo), en que se redujeron. La leyenda muestra el total de cada sentido. |
| **Aumentos y reducciones de número de periodos de duración en programas académicos** | Igual que la anterior, para el número de periodos (negro: se alargó; amarillo: se acortó). |

En Modificados, un programa puede tener varios registros (uno por cada columna que cambió); por ello el contador informa también el número de programas distintos.

## 7. Gráficas: interacción común

| Interacción | Comportamiento |
|---|---|
| **Pasar el cursor** | Muestra el valor exacto. En las gráficas de líneas el cuadro es unificado: lista todas las series en esa fecha. |
| **Leyenda** | Las series se ordenan de mayor a menor total, y cada nombre muestra su total (`INGENIERÍAS · 456`). Un clic oculta o muestra una serie; un doble clic aísla una serie. |
| **Barra de herramientas** (al pasar el cursor sobre la gráfica) | Descargar la gráfica como imagen PNG, ampliar con selección, desplazar, acercar, alejar y restablecer los ejes. |
| **Etiquetas del eje horizontal** | Muestran mes y año; cada punto de una serie corresponde a una ejecución. Con una sola ejecución se muestra su fecha. |
| **Eje vertical** | Rotulado como **REGISTROS** (unidad de medida en todas las gráficas). Con valores pequeños, las marcas son números enteros. |
| **Etiquetas largas** | En barras horizontales, los nombres de más de 34 caracteres se abrevian con «…»; el nombre completo aparece al pasar el cursor. |
| **Sin datos** | Si los filtros no dejan registros, la gráfica muestra «Sin datos para los filtros seleccionados». |
| **Una sola ejecución** | La serie se muestra como un punto. |
| **Colores** | Cada categoría conserva su color en todas las páginas (por ejemplo, cada división). Las categorías «Otro» y «Sin información» se muestran en gris. |

## 8. Tablas y exportación

### 8.1 Contenido

Las tablas muestran **todas las columnas** del registro, ordenadas por defecto desde la ejecución más reciente. Las primeras columnas son la fecha de obtención y, según el apartado, el tipo de novedad o de cambio y el cambio detectado, seguidas del nombre de la institución, el nombre del programa, el departamento y municipio de oferta, el sector, la modalidad, los créditos, los periodos, la periodicidad, el costo de matrícula y la división de la Universidad del Norte. El resto de columnas conserva el orden del archivo (véase el [Anexo A](#anexo-a-diccionario-de-datos)).

### 8.2 Formato de los valores

| Tipo | Presentación |
|---|---|
| Fechas | `DD/MM/AAAA`. |
| Costos de matrícula | Entero con separador de miles. |
| Puntaje total | Hasta cuatro decimales. |
| Otros números | Sin separador de miles, alineados a la derecha. |
| Tipo de cambio | Con tilde y en mayúsculas (por ejemplo, `NÚMERO DE CRÉDITOS`), igual que en el filtro, las gráficas y el correo. |
| Vacíos | Celda vacía. |

### 8.3 Orden, paginación y navegación

- **Orden:** un clic en el encabezado de una columna ordena; un segundo clic invierte el sentido. El primer clic ordena fechas y números de mayor a menor y los textos de la A a la Z. El orden respeta el tipo de dato (fecha, número o texto con orden natural) y los vacíos quedan siempre al final. Se puede ordenar con el teclado (**Enter** o **Espacio** sobre el encabezado). El indicador ▲ o ▼ muestra el sentido vigente.
- **Paginación:** 25 registros por página, con los botones **ANTERIOR** y **SIGUIENTE** y el indicador `PÁGINA X DE Y`. Al aplicar un filtro o cambiar el orden se regresa a la primera página.
- **Columna fija:** la primera columna permanece visible al desplazar la tabla horizontalmente; los encabezados permanecen visibles al desplazarla verticalmente.
- **Sin resultados:** la tabla muestra «Sin registros para los filtros seleccionados».

### 8.4 Descarga

El botón **DESCARGAR** genera un archivo **Parquet** con los registros que cumplen los filtros, con todas las columnas y en el orden vigente de la tabla (en Inicio, de la pestaña visible).

| Aspecto | Detalle |
|---|---|
| Nombre del archivo | `Programas Academicos [Nuevos / Modificados / Inactivos] AAAA-MM-DD.parquet`, con la fecha de la última ejecución. |
| Tipos | Las fechas se guardan como fecha, los números como enteros o decimales y los demás campos como texto. |
| Proceso | Se genera en el navegador; no se envían datos a ningún servidor. Mientras se genera, el botón muestra *GENERANDO...*. |
| Sin registros | Se informa con un aviso y no se genera archivo. |

Los archivos Parquet pueden abrirse con `pandas`, Power BI y Excel (Power Query).

## 9. Vistas compartibles (parámetros de la dirección)

Los filtros vigentes se guardan en la dirección de la página (la parte posterior al símbolo `#`). Esto permite:

- Recargar la página sin perder los filtros.
- Compartir o guardar como favorito una vista filtrada.
- Conservar los filtros al cambiar de apartado: el menú y las tarjetas de indicadores incluyen los filtros vigentes. Las selecciones que no existen en el apartado de destino se restablecen (el filtro de tipo de cambio solo existe en Modificados).

Al modificar la dirección a mano, los filtros se aplican de inmediato. Los valores inválidos se descartan sin generar error. Los parámetros disponibles se listan en el [Anexo B](#anexo-b-parámetros-de-la-dirección).

Ejemplo: `modificados.html#universidad=uninorte&fecha=AAAA-MM-DD` muestra los cambios de la Universidad del Norte en la ejecución de esa fecha. Los enlaces «detalle completo» del correo usan este mecanismo.

## 10. Adaptación a pantallas y accesibilidad

| Ancho de pantalla | Adaptación |
|---|---|
| Hasta 1100 px | Los indicadores pasan a dos columnas y las tarjetas de gráficas se apilan en una columna. |
| Hasta 640 px | Los filtros se pliegan tras el botón **FILTROS** (que muestra el número de filtros activos, por ejemplo `FILTROS · 2`); permanecen visibles el buscador y el contador. Las leyendas pasan debajo de la gráfica, con los nombres acortados según el ancho disponible. |

Accesibilidad: los controles son botones, casillas y campos estándar operables con teclado; el foco se señala con un anillo visible; los paneles se cierran con **Esc**; las pestañas se recorren con las flechas; los encabezados de tabla indican su orden mediante atributos de accesibilidad y las gráficas tienen un nombre descriptivo para lectores de pantalla.

## 11. Flujos de uso paso a paso

### 11.1 Conocer las novedades de la última ejecución

1. Abrir **INICIO** y leer los indicadores: *NUEVOS*, *MODIFICADOS* e *INACTIVOS* corresponden a la última ejecución.
2. Hacer clic en una tarjeta para ver el detalle del apartado.
3. En la tabla, el orden por defecto presenta primero los registros más recientes.

### 11.2 Revisar la oferta de la Universidad del Norte

1. Abrir el filtro **Universidades**, pasar el cursor sobre *UNIVERSIDAD DEL NORTE* y pulsar **AISLAR**.
2. Los indicadores, las gráficas y las tablas se actualizan con los programas de la institución.
3. Para comparar con las universidades de mayor interés, marcar además el grupo *UNIVERSIDADES DE MAYOR INTERÉS*.

### 11.3 Analizar un periodo concreto

1. Abrir el selector de fechas y elegir el modo **RANGO**, **MES** o **AÑO**.
2. Elegir el periodo: los indicadores de novedades muestran la última ejecución de la selección, y las gráficas acumuladas parten del inicio del periodo.
3. Pulsar la **×** junto al botón para volver a *TODAS LAS FECHAS*.

### 11.4 Identificar la oferta de una región

1. Elegir en **Departamentos** (y, si se requiere, en **Municipios**) los de interés. Las opciones de los demás filtros se reducen a lo disponible.
2. Revisar en Inicio las gráficas de modalidad, división y campo de CINE.
3. Para ver solo un departamento, usar **AISLAR** sobre su opción.

### 11.5 Revisar los cambios de créditos o de modalidad

1. Abrir **MODIFICADOS**.
2. En **Cambios**, aislar *NÚMERO DE CRÉDITOS* o *MODALIDAD*.
3. Consultar la gráfica de aumentos y reducciones o la de transiciones de modalidad, y la tabla, cuya columna *CAMBIO DETECTADO* muestra `anterior → nuevo`.

### 11.6 Seguir un programa específico

1. Escribir su código SNIES en el buscador y pulsar Enter.
2. Abrir cada apartado: si el programa tiene novedades, aparecen en las tablas. El filtro se conserva al cambiar de apartado.

### 11.7 Obtener los datos para análisis propios

1. Aplicar los filtros deseados.
2. En la tabla, ordenar si se requiere y pulsar **DESCARGAR**.
3. Abrir el archivo Parquet con `pandas`, Power BI o Excel (Power Query).

Para el detalle de todos los programas activos de una ejecución, consultar `Historico/Historico Programas Activos AAAA.parquet` en el repositorio.

### 11.8 Compartir una vista

1. Aplicar los filtros.
2. Copiar la dirección del navegador y enviarla: quien la abra verá la misma selección.

---

# Parte 3. Correo electrónico

## 12. Reporte semanal y alerta técnica

### 12.1 Reporte semanal

Se envía a los destinatarios al finalizar una ejecución exitosa que cuenta con una ejecución anterior para comparar.

| Aspecto | Detalle |
|---|---|
| **Asunto** | `MONITOR SNIES PREGRADO · UNIVERSITARIO · Reporte del DD/MM/AAAA` |
| **Formatos** | Texto plano y HTML (versiones del mismo contenido). El HTML incluye el logotipo institucional incrustado; no lleva archivos adjuntos. |
| **Identidad visual** | La misma del Dashboard: paleta institucional, tipografía y estilos de tarjetas y tablas. En pantallas angostas, los clientes que admiten estilos incrustados muestran los indicadores en dos columnas y las tablas se desplazan horizontalmente dentro de su tarjeta. El ancho máximo del mensaje es de 720 px y la letra de las tablas no es menor de 10 px. |

Estructura del mensaje, en orden:

| Sección | Contenido |
|---|---|
| **Encabezado** | Título, botón **VER DASHBOARD COMPLETO** y la fecha de la ejecución. |
| **Indicadores** | Programas activos (con variación frente a la ejecución anterior), nuevos, modificados (con número de programas distintos) e inactivos. Coinciden con los del Dashboard. |
| **Resumen de la ejecución** | Descripción de la descarga, la variación de programas activos y las novedades detectadas, con cuántas corresponden a la Universidad del Norte y a las Universidades de Mayor Interés. |
| **Novedades por grupo de universidades** | Tabla con nuevos, modificados e inactivos de cada grupo y su total. |
| **Programas académicos nuevos** | Descripción, tabla de registros y nota con las novedades de la Universidad del Norte y de las Universidades de Mayor Interés. |
| **Programas académicos modificados** | Igual, con el tipo de cambio y el cambio detectado. |
| **Programas académicos inactivos** | Igual. |
| **Pie** | Fuente de los datos. |

Columnas de las tablas: institución, programa, municipio, modalidad y división de Uninorte; Nuevos añade el tipo de novedad y Modificados el tipo de cambio y el cambio detectado. Las secciones sin novedades muestran un mensaje en lugar de la tabla.

**Orden de los registros:** primero la Universidad del Norte, luego las Universidades de Mayor Interés y después las demás, cada grupo por institución y programa.

**Tamaño del mensaje.** Los clientes de correo recortan los mensajes muy extensos (Gmail, desde unos 102 KB de HTML). Si el HTML completo supera 95 KB, cada tabla muestra solo sus primeras filas, hasta donde cabe el mensaje, con prioridad para la Universidad del Norte y las Universidades de Mayor Interés. Bajo la tabla, una nota indica `Se muestran N de M registros; el detalle completo está en el Dashboard`, con un enlace a la ejecución correspondiente. Los resúmenes y las notas de grupo siempre cuentan todos los registros. La versión en texto plano nunca se recorta.

### 12.2 Alerta técnica

Cuando una etapa falla, no se envía el reporte a los destinatarios. Se envía una alerta únicamente a la cuenta remitente, con el asunto `ALERTA TÉCNICA · Monitor SNIES Pregrado · DD/MM/AAAA`, que incluye:

- La etapa que falló y el detalle del error.
- El nombre del registro de la ejecución y un enlace a la ejecución en GitHub Actions.
- Las últimas 60 líneas del registro.

La alerta también se envía si fallan las pruebas unitarias previas a la ejecución semanal o la reclasificación.

---

# Parte 4. Operación

## 13. Flujo de ejecución

El orquestador `Scripts/run_snies.py` ejecuta las siguientes etapas:

| # | Etapa | Descripción |
|---|---|---|
| 1 | **Descarga** | Abre el portal del SNIES con Chrome (Selenium), aplica los filtros *Activo*, *Activo (programa)*, *Pregrado* y *Universitario*, solicita la descarga y espera hasta 120 segundos el archivo `Programas.xlsx`. |
| 2 | **Normalización** | Ejecuta `ETL-PROGRAMAS-SNIES`: normaliza encabezados y valores, valida el esquema, excluye filas con código SNIES inválido o repetido y tipifica fechas y números. Resultado: `Salidas/Programas.xlsx`. |
| 3 | **Clasificación** | Ejecuta `CLASIFICAR-DIVISIONES-UNINORTE-SNIES`, que agrega `DIVISION_UNINORTE` y `PUNTAJE_TOTAL`. Registra además el tamaño de la retroalimentación. |
| 4 | **Validación** | Verifica columnas, número de filas, códigos únicos, filtros del portal, categorías conocidas por el Dashboard y continuidad con la ejecución anterior. |
| 5 | **Detección de novedades** | Compara con la ejecución anterior del histórico (nuevos, modificados e inactivos). |
| 6 | **Guardado** | Agrega la ejecución al histórico del año y las novedades a sus archivos. Si se repite una fecha, reemplaza los registros de esa fecha. |
| 7 | **Dashboard** | Regenera los datos y las páginas del Dashboard. |
| 8 | **Correo** | Envía el reporte (omitido en la primera ejecución de un histórico vacío, o con `--sin-correo`). |

Después, el flujo de GitHub Actions publica en el repositorio los cambios en `Novedades/`, `Historico/`, `Logs/`, `Scripts/` y `docs/`.

**Manejo de fallas.** Una falla en las etapas 1 a 6 detiene la ejecución antes de guardar y genera la alerta técnica. Una falla en las etapas 7 u 8 ocurre cuando los datos ya están guardados: la ejecución termina con código de error y se envía la alerta, pero los datos permanecen. Cada herramienta tiene un tiempo máximo de 30 minutos.

**Códigos de salida:** `0` ejecución completada; `1` alguna etapa falló.

## 14. Programación y ejecución manual

### 14.1 Ejecución semanal

El flujo **SNIES Monitor** (`.github/workflows/snies_daily.yml`) no se programa dentro de GitHub: lo activa un servicio externo de programación (cron-job.org) mediante una llamada a la API de GitHub (`workflow_dispatch`) con un *token* de acceso de alcance mínimo (limitado a este repositorio, con permiso de lectura y escritura de Actions). La frecuencia y la hora se administran en ese servicio (configuración semanal: martes, 8:00 a. m., hora de Colombia). Cuando el flujo se activa sin parámetros, envía el correo y aplica la protección contra descargas incompletas.

La llamada que realiza el servicio de programación es:

```
POST https://api.github.com/repos/dirplaneacionun/MONITOR-SNIES-PREGRADO-UNIVERSITARIO/actions/workflows/snies_daily.yml/dispatches
Cuerpo: {"ref": "main"}
```

Debe incluir el encabezado `Authorization: Bearer <token>` y `Accept: application/vnd.github+json`. El nombre del archivo del flujo forma parte de la dirección: no debe cambiarse sin actualizar el servicio de programación.

### 14.2 Pasos del flujo

| Paso | Descripción |
|---|---|
| Checkout | Descarga el código del repositorio (sin conservar el *token* en la configuración de Git). |
| Chrome y Python | Instala Google Chrome y Python 3.14 con caché de dependencias. |
| Dependencias | `pip install -r requirements.txt`. |
| Pruebas unitarias | Si fallan, no se ejecuta el pipeline, no se modifican datos y se envía una alerta a la cuenta remitente. |
| Ejecutar pipeline | `python Scripts/run_snies.py` con las opciones elegidas. |
| Publicar cambios | Confirma y publica los cambios en `main`. Si otra persona publicó cambios durante la ejecución, los integra mediante *rebase*; si hay conflicto en los mismos archivos, el paso falla de forma visible y no publica. |

El flujo tiene un límite de 60 minutos y nunca se ejecuta en paralelo con otro flujo que escriba datos (comparte el grupo de concurrencia `snies-monitor` con la reclasificación; el siguiente espera).

### 14.3 Ejecución manual

1. Entrar a `Actions` en el repositorio y elegir **SNIES Monitor**.
2. Pulsar **Run workflow**, elegir la rama `main` y las opciones.
3. Pulsar **Run workflow** para confirmar.

| Opción | Efecto |
|---|---|
| **Enviar el correo a los destinatarios** | Activada por defecto. Al desactivarla se ejecuta todo el flujo, incluida la publicación de datos, sin enviar el reporte. Es la forma de realizar una ejecución de prueba. |
| **Aceptar una caída de más del 10 % de los programas** | Desactivada por defecto. Solo debe activarse cuando se confirmó en el portal que la reducción es real. |

Una ejecución manual **sí publica datos y, con la opción activada, envía el correo real**.

## 15. Validaciones y protecciones

| Protección | Condición | Consecuencia |
|---|---|---|
| **Filtros del portal** | Cada fila debe tener estado de institución `ACTIVA`, estado del programa `ACTIVO`, nivel académico `PREGRADO` y nivel de formación `UNIVERSITARIO`. | Se detiene sin guardar. Indica que el portal no aplicó un filtro. |
| **Continuidad** | El número de programas no puede caer más del 10 % frente a la ejecución anterior. | Se detiene sin guardar: posible descarga incompleta. Con `--forzar` (o la opción del flujo) se acepta y queda registrado. |
| **Descarga sin filtros** | Más de 10.000 programas. | Se detiene sin guardar. |
| **Esquema** | El ETL valida las columnas esperadas, avisa de cambios de nombre en el portal y detiene el proceso si faltan columnas. | Se detiene sin guardar. |
| **Claves del programa** | Códigos SNIES vacíos, no enteros, no positivos o repetidos. | Las filas se excluyen y se listan en `Revision/Revision Programas.xlsx` del ETL; de un código repetido se conserva la primera aparición. |
| **Caída del ETL** | Las filas a exportar caen más del 10 % respecto de la salida del ETL existente. | El ETL aborta ese archivo; `--force` lo permite. |
| **Coherencia del resultado** | El clasificador debe devolver las mismas filas que el ETL, sin códigos repetidos y con las columnas de clasificación. | Se detiene sin guardar. |
| **Valores fuera del rango plausible** | Fechas anteriores a 1950 o futuras, entre otras. | Se conservan y se registran como alerta (indicando fila y código SNIES). |
| **Categorías no contempladas** | Sectores o modalidades fuera de los catálogos, o más divisiones que colores disponibles. | Advertencia en el registro; la ejecución continúa (la modalidad desconocida se agrupa como *SIN INFORMACIÓN*). |
| **Tamaño de la retroalimentación** | `retroalimentacion.json` ≥ 40 MB (aviso) o ≥ 80 MB (crítico). | Advertencia en el registro y anotación en GitHub Actions; la ejecución continúa. |
| **Escritura atómica** | Los archivos Parquet se escriben en un archivo temporal que reemplaza al definitivo al terminar. | Una interrupción no deja archivos incompletos. |

---

# Parte 5. Configuración

## 16. Secretos, destinatarios y cuenta de correo

### 16.1 Secretos del repositorio

Se administran en `Settings → Secrets and variables → Actions`.

| Secreto | Contenido |
|---|---|
| `SMTP_USER` | Dirección de la cuenta que envía el reporte y recibe las alertas técnicas. |
| `SMTP_PASS` | Contraseña de aplicación de esa cuenta. |
| `DESTINATARIOS` | Direcciones que reciben el reporte, separadas por coma (los espacios sobrantes se ignoran). |

GitHub no permite consultar el valor de un secreto guardado; solo reemplazarlo. El flujo usa también el *token* temporal que GitHub genera en cada ejecución para publicar los cambios; no requiere configuración.

### 16.2 Cambiar los destinatarios

1. Abrir `Settings → Secrets and variables → Actions` y editar `DESTINATARIOS`.
2. Escribir la lista **completa** de direcciones separadas por coma (el valor reemplaza al anterior).
3. Guardar. El cambio se aplica desde la siguiente ejecución.

### 16.3 Cambiar la cuenta remitente (Gmail)

Gmail no admite el inicio de sesión de programas con la contraseña habitual; requiere una contraseña de aplicación.

1. Activar la verificación en dos pasos en la cuenta de Google.
2. Generar una contraseña de aplicación en la configuración de seguridad de la cuenta y copiarla en ese momento.
3. Actualizar `SMTP_USER` con la dirección de la cuenta y `SMTP_PASS` con la contraseña de aplicación.

### 16.4 Usar un proveedor distinto de Gmail

El servidor SMTP se define en `Scripts/send_report.py`:

```python
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
```

Se debe sustituir por el servidor y el puerto del proveedor (la conexión utiliza STARTTLS con verificación de certificado), publicar el cambio y actualizar `SMTP_USER` y `SMTP_PASS`. Algunos proveedores requieren habilitar previamente la autenticación SMTP en la cuenta.

### 16.5 Probar la configuración

Ejecutar manualmente el flujo con la opción de correo activada y revisar el resultado en `Actions`. Un error de autenticación SMTP (código 535) indica credenciales incorrectas o el uso de la contraseña de inicio de sesión en lugar de la contraseña de aplicación.

Si el servidor rechaza solo a algunos destinatarios, el correo llega a los demás y el registro de la ejecución indica cuántos fueron rechazados, su posición en `DESTINATARIOS` y el código SMTP (sin mostrar las direcciones).

Ante una falla transitoria al conectar o autenticar (por ejemplo, `Connection unexpectedly closed`, tiempo de espera agotado o falla de resolución de nombres), la herramienta reintenta hasta tres veces, con esperas de 5 y 15 segundos, y registra una advertencia por cada intento fallido. No se reintentan el rechazo de las credenciales (535), los errores de certificado ni un error durante el envío del mensaje, para no duplicarlo. La alerta técnica usa el mismo mecanismo.

## 17. Publicación en GitHub Pages

El Dashboard se publica desde la carpeta `docs/` de la rama `main`. Configuración requerida en `Settings → Pages`:

| Campo | Valor |
|---|---|
| Source | Deploy from a branch |
| Branch | `main` |
| Carpeta | `/docs` |

Cada publicación de cambios en `main` actualiza el sitio en uno o dos minutos. Si el repositorio se traslada o se renombra, la dirección del sitio cambia y debe actualizarse la constante `DASHBOARD_URL` de `Scripts/send_report.py` y el enlace de insignia del README.

## 18. Ejecución local

### 18.1 Preparación

Véanse los requisitos y la instalación en el [README](README.md#3-requisitos). Para enviar correo desde el equipo local, definir `SMTP_USER`, `SMTP_PASS` y `DESTINATARIOS`:

```powershell
$env:SMTP_USER = "cuenta@dominio.com"
$env:SMTP_PASS = "contraseña-de-aplicación"
$env:DESTINATARIOS = "persona1@dominio.com, persona2@dominio.com"
```

### 18.2 Opciones del orquestador

| Opción | Descripción |
|---|---|
| `--sin-correo` | No envía correos (ni el reporte ni las alertas técnicas). |
| `--archivo RUTA.xlsx` | Procesa un reporte ya descargado en lugar de descargarlo. |
| `--fecha AAAA-MM-DD` | Fecha de obtención de la ejecución (por defecto, la fecha actual en hora de Colombia). |
| `--forzar` | Acepta una caída de programas mayor al 10 %. |

Con `SNIES_HEADLESS=0` la descarga muestra la ventana de Chrome.

### 18.3 Advertencia

Una ejecución local escribe en `Historico/`, `Novedades/`, `Logs/` y `docs/`. Para no alterar los datos del repositorio, ejecutar en una copia independiente o descartar los cambios con Git antes de publicar. Los archivos `Programas.xlsx` de las carpetas `Fuentes/` y `Salidas/` de las herramientas son intermedios que se regeneran en cada ejecución y no se versionan; la carpeta `tmp/` guarda la descarga y capturas de depuración (`debug_snies.png` y `debug_post_filtros.png`) y se elimina al finalizar correctamente.

### 18.4 Vista local del Dashboard

```powershell
python Scripts/generar_dashboard.py                 # regenera los datos si cambiaron Historico/ o Novedades/
python -m http.server 8766 --directory docs         # sirve el Dashboard en http://localhost:8766
```

---

# Parte 6. Datos y clasificación

## 19. Datos almacenados

### 19.1 Archivos

| Archivo | Contenido y reglas |
|---|---|
| `Historico/Historico Programas Activos AAAA.parquet` | Un archivo por año con todas las ejecuciones de ese año (42 columnas: las 41 del reporte procesado y `FECHA_DE_OBTENCION`). Cada ejecución es un grupo de filas independiente, en orden de fecha. Los años nuevos adoptan el esquema del año anterior. |
| `Novedades/Nuevos.parquet` | Registros de programas nuevos y reactivados (columnas del histórico más `TIPO_NOVEDAD`). |
| `Novedades/Modificados.parquet` | Un registro por cambio (columnas del histórico más `TIPO_CAMBIO_DETECTADO` y `CAMBIO_DETECTADO`). |
| `Novedades/Inactivos.parquet` | Registros de programas inactivos, con los datos que tenían en la ejecución anterior. |
| `docs/data/comun.js`, `activos.js`, `nuevos.js`, `modificados.js`, `inactivos.js` | Datos compactos del Dashboard: catálogos, fechas, indicadores, versiones de programas activos con sus rangos de ejecuciones y las tablas de novedades. Se regeneran completos en cada ejecución. |

### 19.2 Convenciones y garantías

- La fecha de ejecución (`FECHA_DE_OBTENCION`) se guarda como fecha sin hora.
- Cada ejecución adopta el esquema del archivo de su año. Las columnas numéricas que el SNIES entrega a veces como número y a veces como texto se guardan con una única representación (`10`, no `10.0`), para que las comparaciones entre ejecuciones sean exactas.
- Si la descarga trae columnas nuevas, se agregan al esquema del año y quedan vacías en las ejecuciones anteriores; nunca se descartan datos.
- Repetir una fecha reemplaza los registros de esa fecha en el histórico y en las novedades.
- Los archivos están separados por año para mantener cada archivo por debajo de los límites de tamaño de GitHub (advertencia desde 50 MB, rechazo sobre 100 MB).

### 19.3 Cobertura temporal

El histórico contiene ejecuciones desde el 23/02/2024. Hasta abril de 2026 las ejecuciones se registraron a intervalos irregulares y desde mayo de 2026 son semanales. Las novedades anteriores al 24/04/2026 provienen de comparaciones entre descargas no consecutivas. Véase la [sección 25](#25-limitaciones-conocidas).

## 20. Clasificación en divisiones

### 20.1 Propósito

Asignar a cada programa una división académica de la Universidad del Norte (`DIVISION_UNINORTE`) y un `PUNTAJE_TOTAL` que expresa la confianza de la votación. El clasificador (`CLASIFICAR-DIVISIONES-UNINORTE-SNIES`) procesa `Programas.xlsx` en cada ejecución.

### 20.2 Procedimiento

| Elemento | Descripción |
|---|---|
| **División fija de la Universidad del Norte** | Los programas de la propia universidad, listados en `Fuentes/ProgramasUN.xlsx`, conservan la división allí definida. |
| **Votación ponderada de tres capas** | Capa 1 (peso 0,50): código SNIES ya conocido en la referencia. Capa 2 (peso 0,25): nombre del programa, por coincidencia exacta o similitud de al menos 0,70. Capa 3 (peso 0,25): campo CINE detallado (0,15) y núcleo básico del conocimiento (0,10), mediante diccionarios de asignación exclusiva y reglas por nombre para los casos ambiguos. |
| **Excepciones por nombre** | El diccionario `NOMBRE_DIVISION_OVERRIDE` (`diccionarios.py`) fija la división de nombres de programa específicos y prevalece sobre la votación. |
| **Refinamiento** | Una pasada adicional ajusta los casos aislados con el consenso del vecindario de campos NBC y CINE y con palabras clave. |
| **Consistencia** | Un mismo código SNIES con un mismo nombre tiene una sola división en todos los archivos; una contradicción detiene el proceso. |
| **Confianza** | Cada programa recibe una confianza (alta, media o baja) según su puntaje y el margen frente a la segunda división. Los programas de confianza insuficiente o con inconsistencias se listan para revisión. |
| **Retroalimentación** | `Retroalimentacion/retroalimentacion.json` conserva las clasificaciones verificadas para que no cambien entre ejecuciones. Antes de actualizarla se guarda una copia de respaldo local (hasta 10, no versionadas) en la misma carpeta. |

### 20.3 Listas de revisión

| Archivo | Contenido |
|---|---|
| `CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Revision/Revision Programas.xlsx` | Programas de la ejecución que requieren revisión de su división (con su confianza y los votos de cada capa). El número de programas por revisar se registra en cada ejecución. |
| `CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Revision/Revision Catalogo Historico.xlsx` | Igual, para el catálogo de programas del histórico completo (generado por la reclasificación). |
| `ETL-PROGRAMAS-SNIES/Revision/Revision Programas.xlsx` | Filas excluidas por el ETL, con su fila de Excel, motivo y columnas con diferencias. |

### 20.4 Ajustar las reglas y reclasificar

1. Agregar o corregir la entrada correspondiente en `Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Scripts/diccionarios.py` (las claves de `NOMBRE_DIVISION_OVERRIDE` se escriben en minúsculas y sin tildes).
2. Publicar el cambio. Las nuevas ejecuciones semanales usan las reglas vigentes.
3. Para aplicar las reglas al histórico y a las novedades existentes, ejecutar el flujo **Reclasificar divisiones** (`Actions → Reclasificar divisiones → Run workflow`):
   - Con la casilla *Publicar los cambios* desmarcada, solo simula y muestra en la página de la ejecución cuántos registros cambiarían de división y las transiciones principales.
   - Con la casilla marcada, reescribe únicamente los archivos con cambios, regenera los datos del Dashboard y publica. No envía correo.

Equivalente local: `python Scripts/reclasificar.py --simular` y `python Scripts/reclasificar.py`.

La reclasificación clasifica una sola vez las combinaciones únicas de código SNIES, nombre, NBC y CINE detallado en una copia temporal del clasificador, de modo que la retroalimentación real no se modifica.

### 20.5 Tamaño de la retroalimentación

El archivo `retroalimentacion.json` crece con el uso. La herramienta registra su tamaño en cada ejecución y advierte desde 40 MB y desde 80 MB (GitHub advierte desde 50 MB y rechaza archivos de más de 100 MB).

## 21. Mantenimiento de datos

### 21.1 Regenerar el Dashboard

`python Scripts/generar_dashboard.py` regenera `docs/data/*.js` y las páginas HTML a partir de `Historico/` y `Novedades/`. Las páginas enlazan sus archivos con un sufijo de versión basado en su contenido, de modo que el navegador descarga de nuevo solo lo que cambió. Al generar los datos, las variantes de departamento y de municipio que difieren solo en la puntuación (por ejemplo, `BOGOTA D.C.` y `BOGOTA, D.C.`) se unifican en la forma más frecuente.

### 21.2 Completar Modificados

`python Scripts/completar_modificados.py` compara cada par de ejecuciones consecutivas del histórico desde el 24/04/2026 (parámetro `--desde`) con las mismas reglas de la detección semanal y agrega a `Modificados.parquet` los cambios que la tabla no tiene. Solo agrega: nunca modifica ni elimina registros. Es idempotente y admite `--simular`.

### 21.3 Registros de ejecución y retención

| Registro | Ubicación | Retención |
|---|---|---|
| Orquestador | `Logs/monitor_AAAA-MM-DD_HH-MM-SS.log` | Últimos 10 |
| Reclasificación | `Logs/reclasificacion_AAAA-MM-DD_HH-MM-SS.log` | Últimos 10 |
| ETL | `Scripts/ETL-PROGRAMAS-SNIES/Logs/etl_*.log` | Últimos 10 |
| Clasificador | `Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Logs/pipeline_*.log` | Últimos 10 |

El registro del orquestador incorpora el de cada herramienta. Las horas se expresan en hora de Colombia.

---

# Parte 7. Calidad, seguridad y dependencias

## 22. Pruebas y control de calidad

| Prueba | Alcance | Ejecución |
|---|---|---|
| **Unitarias** (`tests/test_unitarias.py`) | Detección de novedades, histórico y tipos de datos, protecciones de la descarga, contenido y tamaño del correo, envío, reintentos y rechazos SMTP, generación de las páginas del Dashboard (gráficas, navegación, política de contenido y versión de los recursos), seguridad del sitio y de los flujos de trabajo (incluida la concurrencia entre el flujo semanal y la reclasificación), privacidad de los registros, reclasificación, vigilancia de la retroalimentación y completado de Modificados. | `python -m unittest discover -s tests` (segundos). Se ejecutan antes de cada ejecución semanal y de cada reclasificación. |
| **ETL y clasificador** (`tests/test_etl_clasificador.py`) | Conversión de encabezados, códigos, números y fechas del ETL; exclusión de códigos SNIES inválidos o repetidos y archivo de revisión; conversión de tipos todo o nada y rangos plausibles; protección ante una fuente truncada; decisión y consenso de la votación del clasificador, normalización de texto, overrides por nombre, escritura atómica y verificación de integridad de las salidas. Usan archivos temporales y no modifican los datos del proyecto. | `python -m unittest discover -s tests` (segundos). |
| **De punta a punta** (`tests/e2e_dashboard.py`) | Operan el Dashboard con Chrome: filtros, dependencias entre filtros, selector de fechas, buscador, tablas, paginación, selector CINE, descarga, navegación, enlaces guardados, vista móvil, consistencia visual, rendimiento y errores de consola. Cada valor mostrado se compara con un cálculo independiente sobre `Historico/` y `Novedades/`. | `python tests/e2e_dashboard.py` (10 a 20 minutos; `--rapida` prueba una muestra). |

El flujo **Pruebas del Dashboard** ejecuta ambas en las solicitudes de cambios y al publicar en `main` cambios que afecten el Dashboard, sus generadores o las pruebas, y puede ejecutarse manualmente. No se ejecuta con las actualizaciones semanales de datos.

Recomendación antes de fusionar cambios: abrir una solicitud de cambios, verificar que las pruebas del flujo pasen y revisar la vista del Dashboard.

## 23. Seguridad y privacidad

| Ámbito | Medida |
|---|---|
| **Credenciales** | Se almacenan solo como secretos de GitHub; nunca en el repositorio. El *token* de la programación externa tiene alcance limitado a este repositorio. |
| **Permisos del flujo** | `contents: write` únicamente; el *token* no se guarda en la configuración de Git durante la ejecución y se usa solo en el paso de publicación. |
| **Acciones de terceros** | Fijadas a un identificador de confirmación (SHA) exacto; Dependabot propone actualizaciones mensuales que no se fusionan solas. |
| **Entradas del flujo** | Se pasan como variables de entorno, no se insertan en el script, para evitar inyección de comandos. |
| **Registros públicos** | El formato de registro sustituye las rutas personales por marcadores y las direcciones de correo por `<correo>`. Los rechazos SMTP se registran sin direcciones. |
| **Correo** | Conexión STARTTLS con verificación del certificado y del nombre del servidor. El contenido de las celdas se escapa para evitar inyección de HTML. Las alertas técnicas solo se envían a la cuenta remitente. |
| **Dashboard** | Política de seguridad de contenido: solo scripts propios del sitio, sin objetos incrustados ni formularios y sin solicitudes a otros orígenes. Los valores de la dirección se tratan como datos y se escapan antes de mostrarse. Las librerías y la tipografía se alojan en el propio sitio. La descarga a Parquet se genera en el navegador. |
| **Datos** | Los datos proceden de fuentes públicas del SNIES; no se almacenan datos personales de estudiantes. |

## 24. Dependencias y actualizaciones

### 24.1 Python

Versiones exactas en `requirements.txt` (validadas con Python 3.14):

| Biblioteca | Uso |
|---|---|
| `pandas`, `numpy`, `pyarrow` | Procesamiento de datos y archivos Parquet. |
| `openpyxl`, `python-calamine`, `xlsxwriter` | Lectura y escritura de archivos Excel. |
| `selenium`, `webdriver-manager` | Descarga automatizada del portal. |
| `rich`, `tqdm` | Consola y progreso de las herramientas. |
| `pillow` | Pruebas (comparación de logotipos). |

El clasificador verifica sus salidas con las versiones exactas de `openpyxl` y `pandas`. Antes de actualizar una biblioteca, ejecutar el flujo completo y confirmar que no hay errores ni cambios inesperados de división. Dependabot abre solicitudes mensuales de actualización; no deben fusionarse sin que pasen las pruebas.

### 24.2 JavaScript del Dashboard (incluidas en `docs/assets/vendor`)

| Librería | Versión | Uso |
|---|---|---|
| Plotly.js | 2.35.2 | Gráficas. |
| flatpickr | 4.6.13 | Calendarios del selector de fechas. |
| hyparquet-writer | 0.18.0 | Generación de archivos Parquet en el navegador. |
| hyparquet | 1.32.0 | Dependencia de la anterior. |

Las versiones se registran en `Librerias/package.json`, que Dependabot vigila. Para actualizar una librería, se reemplazan sus archivos en `docs/assets/vendor`, se actualiza la versión en ese registro y se ejecutan las pruebas. La tipografía es Montserrat (licencia SIL Open Font License).

---

# Parte 8. Referencia

## 25. Limitaciones conocidas

- **Periodicidad histórica:** las ejecuciones anteriores al 24/04/2026 no son semanales y las novedades derivadas de ellas pueden estar incompletas (las comparaciones se hicieron entre descargas no consecutivas). Las gráficas de evolución conectan esos puntos sin distinguir intervalos irregulares.
- **Modificados:** solo se vigilan cinco columnas. Otros cambios (costo de matrícula, resolución, título, etc.) no generan registros de modificación.
- **Búsqueda:** el buscador solo admite coincidencia exacta de códigos; no busca por nombre. Para buscar por nombre se usa el filtro de programas académicos.
- **Programas inactivos:** un programa se considera inactivo cuando deja de figurar en el reporte filtrado, es decir, cuando deja de cumplir alguno de los filtros del universo (por ejemplo, por cambio de estado). Si reaparece, se registra como reactivado.
- **Dependencia del portal:** la descarga depende de la estructura del portal del SNIES. Un cambio en el portal detiene la ejecución sin guardar datos.
- **Calidad de la fuente:** algunos valores del SNIES conservan imperfecciones de origen (por ejemplo, caracteres no depurados en algunos nombres o fechas fuera del rango plausible). Se conservan tal como vienen y las fechas atípicas se registran como alertas.
- **Clasificación:** la división es una asignación automática basada en reglas; los programas de confianza insuficiente se listan para revisión y pueden requerir ajustes en las reglas.
- **Tamaño de datos:** el Dashboard carga todos sus datos en el navegador (unos 0,9 MB de archivos de datos sin comprimir). Está dimensionado para el universo vigilado.
- **Detalle de programas activos:** el Dashboard no presenta una tabla de todos los programas activos de cada ejecución; ese detalle se consulta en el histórico.

## 26. Buenas prácticas

- Revisar el correo y el Dashboard tras cada ejecución semanal, y atender las alertas técnicas.
- Ante una caída de programas, verificar primero en el portal del SNIES si es real antes de usar la opción de forzar.
- Realizar ejecuciones manuales de prueba con el correo desactivado.
- Hacer los cambios de código mediante solicitudes de cambios y esperar el resultado de las pruebas antes de fusionar.
- No actualizar `pandas` ni `openpyxl` sin ejecutar antes el flujo completo.
- Mantener actualizada la lista `DESTINATARIOS` y proteger la contraseña de aplicación.
- Antes de reclasificar con publicación, ejecutar la simulación y revisar el resumen.
- Al analizar periodos, interpretar las gráficas acumuladas como registros desde el inicio del periodo seleccionado, y los indicadores como el estado de la última ejecución incluida.
- Citar siempre la fuente (SNIES, Ministerio de Educación Nacional) y la fecha de la ejecución al divulgar resultados.

## 27. Solución de problemas

### 27.1 Operación

| Síntoma | Causa probable | Acción |
|---|---|---|
| La ejecución termina con «no se guardó ningún dato» y una caída de programas | Descarga incompleta o reducción real. | Verificar en el portal; si es real, ejecutar con la opción de forzar. |
| «El portal no aplicó los filtros esperados» | Cambio en el portal o falla de los controles de filtro. | Revisar el portal y, si cambió su estructura, ajustar la descarga en `Scripts/run_snies.py`. |
| Falla en la etapa de descarga | El portal tardó, no respondió o cambió. | Repetir; con `SNIES_HEADLESS=0` observar el navegador; revisar `tmp/debug_snies.png`. |
| «El archivo no apareció tras 120 s» | La descarga no se generó. | Verificar que el portal ofrezca el botón *Descargar programas*. |
| Falla del ETL por columnas ausentes | El portal cambió el esquema del reporte. | Revisar el registro del ETL, que sugiere los renombres probables. |
| Fallan las pruebas unitarias del flujo | Un cambio rompió una regla verificada. | Revisar el correo de alerta y corregir antes de volver a ejecutar; no se modificó ningún dato. |
| Falla «Publicar cambios» por conflicto | Se publicaron cambios en los mismos archivos durante la ejecución. | Integrar los cambios y repetir el flujo. |
| El flujo falla por permisos al publicar | Los permisos del repositorio restringen la escritura de Actions. | Revisar `Settings → Actions → General → Workflow permissions`. |
| El flujo semanal no se activa | La programación externa o su *token* no están vigentes. | Revisar el servicio de programación y renovar el *token* limitado al repositorio. |

### 27.2 Correo

| Síntoma | Causa probable | Acción |
|---|---|---|
| Error SMTP 535 | Contraseña de inicio de sesión en lugar de contraseña de aplicación, o secreto incorrecto. | Generar una contraseña de aplicación y actualizar `SMTP_PASS`. |
| El reporte no llega pero la ejecución terminó | Falla del envío después de guardar los datos; el registro la detalla. | Revisar la alerta técnica y la configuración de los secretos. |
| Advertencia «Falla transitoria de la conexión SMTP» en el registro | Corte momentáneo de la conexión con el servidor de correo. | Ninguna si el correo se envió después: la herramienta reintentó sola. Si fallan los tres intentos, la ejecución termina con error: volver a ejecutar el flujo. |
| Llega a menos destinatarios | El servidor rechazó direcciones. | El registro indica posición y código; revisar `DESTINATARIOS`. |
| No llegó correo en una ejecución exitosa | Era la primera ejecución del histórico (línea base) o se ejecutó con `--sin-correo` o con la opción de correo desactivada. | Verificar las opciones de la ejecución. |
| La tabla del correo está incompleta | Se aplicó el límite de tamaño del mensaje. | Usar el enlace «Dashboard» de la nota bajo la tabla. |

### 27.3 Dashboard

| Síntoma | Causa probable | Acción |
|---|---|---|
| El Dashboard muestra datos antiguos | La publicación de Pages está en curso o el navegador conserva una copia. | Esperar unos minutos y recargar con **Ctrl + F5**; verificar el estado en `Actions`. |
| «Sin datos para los filtros seleccionados» | La combinación de filtros no tiene registros. | Pulsar **LIMPIAR FILTROS** o ampliar la selección. |
| Un filtro no permite desmarcar una opción | Debe permanecer al menos una opción seleccionada. | Marcar otra opción antes, o usar **AISLAR**. |
| El buscador no encuentra un programa | Solo admite códigos exactos. | Usar el código SNIES completo o el filtro de programas. |
| No se descarga el archivo Parquet | La tabla no tiene registros, o la página se abrió desde el sistema de archivos. | Ajustar los filtros; abrir el Dashboard por HTTP. |
| Un enlace compartido abre otra vista | Algunos valores del enlace no existen en el apartado o son incompatibles entre sí. | Los valores inválidos se restablecen; compartir de nuevo la vista. |
| La gráfica por campo de CINE muestra menos series que las marcadas | La gráfica muestra como máximo diez campos (los de más registros). | Reducir la selección. |

### 27.4 Clasificación y datos

| Síntoma | Causa probable | Acción |
|---|---|---|
| Un programa quedó en una división incorrecta | Reglas insuficientes para su nombre o campos de clasificación. | Revisar la lista de revisión; agregar una excepción por nombre y reclasificar. |
| El clasificador se detiene por contradicción de divisiones | Un mismo código y nombre con divisiones distintas. | Revisar el mensaje del registro y corregir las reglas. |
| `retroalimentacion.json` se acerca a 40 MB | Crecimiento acumulado. | Planear su depuración o división; véase la sección 20.5. |
| Una fecha del histórico tiene registros duplicados | Se repitió la ejecución de esa fecha con datos distintos. | Repetir una fecha reemplaza sus registros; no deja duplicados. |

## 28. Glosario

| Término | Significado |
|---|---|
| **CINE F 2013 A.C.** | Clasificación Internacional Normalizada de la Educación adaptada para Colombia; tiene tres niveles: campo amplio, específico y detallado. |
| **Contraseña de aplicación** | Clave especial de 16 caracteres que una cuenta de correo genera para que una aplicación pueda iniciar sesión. |
| **Dashboard** | Sitio web de consulta de la información, publicado con GitHub Pages. |
| **ETL** | Proceso de extracción, transformación y carga; aquí, la normalización del reporte. |
| **GitHub Actions** | Servicio de GitHub que ejecuta los flujos automatizados del repositorio. |
| **GitHub Pages** | Servicio de GitHub que publica el contenido de `docs/` como sitio web. |
| **Línea base** | Primera ejecución del histórico: se guarda sin comparar. |
| **NBC** | Núcleo básico del conocimiento. |
| **Override** | Excepción por nombre de programa que fija su división. |
| **Parquet** | Formato de archivo columnar para datos tabulares, compacto y con tipos. |
| **Reactivado** | Programa que reaparece tras haber existido antes. |
| **Secreto (*secret*)** | Valor sensible que GitHub guarda cifrado y que el flujo puede usar sin exponerlo. |
| **SNIES** | Sistema Nacional de Información de la Educación Superior. |
| **`workflow_dispatch`** | Mecanismo de GitHub que permite iniciar un flujo manualmente o mediante la API. |

---

## Anexo A. Diccionario de datos

Las columnas provienen del reporte de programas del SNIES, normalizadas (mayúsculas, sin tildes, conservando la Ñ). Las columnas calculadas por la herramienta se indican como tales.

### A.1 Institución

| Columna | Descripción |
|---|---|
| `CODIGO_INSTITUCION_PADRE` | Código SNIES de la institución principal. |
| `CODIGO_INSTITUCION` | Código SNIES de la institución o sede. |
| `NOMBRE_INSTITUCION` | Nombre de la institución. |
| `ESTADO_INSTITUCION` | Estado de la institución (en este universo, `ACTIVA`). |
| `CARACTER_ACADEMICO` | Carácter académico de la institución. |
| `SECTOR` | `OFICIAL` o `PRIVADO`. |

### A.2 Programa y registro

| Columna | Descripción |
|---|---|
| `REGISTRO_UNICO` | Registro único del programa. |
| `CODIGO_SNIES_DEL_PROGRAMA` | Código SNIES del programa (identificador entero). |
| `CODIGO_ANTERIOR_ICFES` | Código anterior del programa (texto, para conservar la precisión). |
| `NOMBRE_DEL_PROGRAMA` | Nombre del programa. |
| `TITULO_OTORGADO` | Título que otorga. |
| `ESTADO_PROGRAMA` | Estado del programa (en este universo, `ACTIVO`). |
| `JUSTIFICACION`, `JUSTIFICACION_DETALLADA` | Justificación del registro. |
| `RECONOCIMIENTO_DEL_MINISTERIO`, `RESOLUCION_DE_APROBACION` | Acto administrativo de reconocimiento o aprobación. |
| `FECHA_DE_RESOLUCION`, `FECHA_EJECUTORIA`, `FECHA_DE_REGISTRO_EN_SNIES` | Fechas del acto y del registro. |
| `VIGENCIA_AÑOS`, `VIGENCIA_TRANSITORIA` | Vigencia del registro. |
| `OBSERVACION_DECRETO_117423` | Observación asociada al decreto. |

### A.3 Clasificación académica

| Columna | Descripción |
|---|---|
| `CINE_F_2013_AC_CAMPO_AMPLIO`, `CINE_F_2013_AC_CAMPO_ESPECIFIC`, `CINE_F_2013_AC_CAMPO_DETALLADO` | Campos de la clasificación CINE F 2013 A.C. |
| `AREA_DE_CONOCIMIENTO`, `NUCLEO_BASICO_DEL_CONOCIMIENTO` | Área y núcleo básico del conocimiento. |

### A.4 Oferta

| Columna | Descripción |
|---|---|
| `NIVEL_ACADEMICO`, `NIVEL_DE_FORMACION` | Nivel académico y de formación (en este universo, `PREGRADO` y `UNIVERSITARIO`). |
| `MODALIDAD` | Modalidad de oferta, con el valor original del SNIES. |
| `NUMERO_CREDITOS` | Número de créditos. |
| `NUMERO_PERIODOS_DE_DURACION` | Número de periodos de duración. |
| `PERIODICIDAD`, `PERIODICIDAD_ADMISIONES` | Periodicidad del programa y de las admisiones. |
| `SE_OFRECE_POR_CICLOS_PROPEDEUT` | Indica si se ofrece por ciclos propedéuticos. |
| `PROGRAMA_EN_CONVENIO` | Indica si el programa se ofrece en convenio. |
| `DEPARTAMENTO_OFERTA_PROGRAMA`, `MUNICIPIO_OFERTA_PROGRAMA` | Ubicación de la oferta. |
| `COSTO_MATRICULA_ESTUD_NUEVOS` | Costo de matrícula para estudiantes nuevos. |

### A.5 Columnas calculadas

| Columna | Descripción |
|---|---|
| `DIVISION_UNINORTE` | División de la Universidad del Norte asignada por el clasificador. |
| `PUNTAJE_TOTAL` | Puntaje de la votación de la clasificación. |
| `FECHA_DE_OBTENCION` | Fecha de la ejecución (fecha sin hora). |
| `TIPO_NOVEDAD` | Solo en Nuevos: `NUEVO` o `REACTIVADO`. |
| `TIPO_CAMBIO_DETECTADO` | Solo en Modificados: `NUMERO DE CREDITOS`, `MUNICIPIO DE OFERTA`, `NOMBRE DEL PROGRAMA`, `NUMERO DE PERIODOS` o `MODALIDAD`. |
| `CAMBIO_DETECTADO` | Solo en Modificados: `valor anterior → valor nuevo`. |

## Anexo B. Parámetros de la dirección

Se escriben después de `#`, separados por `&`. Los parámetros repetibles admiten varios valores (`departamento=ANTIOQUIA&departamento=CALDAS`).

| Parámetro | Valores | Filtro |
|---|---|---|
| `buscar` | Código SNIES o de institución padre | Buscador |
| `universidad` | `uninorte`, `mayor`, `otras` (grupos completos) o código de institución padre | Universidades |
| `programa` | Nombre del programa; con `!` como primer valor, los demás se excluyen | Programas académicos |
| `departamento`, `municipio` | Nombre normalizado | Departamentos, municipios |
| `sector` | `OFICIAL`, `PRIVADO` | Sectores |
| `modalidad` | `A DISTANCIA`, `DUAL`, `HIBRIDA`, `PRESENCIAL`, `VIRTUAL`, `SIN INFORMACIÓN` | Modalidades |
| `cine` | Nombre del campo específico | Campos de CINE |
| `division` | Nombre de la división | Divisiones |
| `cambio` | Tipo de cambio (`NUMERO DE CREDITOS`, etc.) | Cambios (solo Modificados) |
| `fecha` | `AAAA-MM-DD` (repetible) | Fechas: ejecuciones |
| `desde` y `hasta` | `AAAA-MM-DD` | Fechas: rango |
| `mes` | `AAAA-MM` (repetible) | Fechas: meses |
| `anio` | `AAAA` (repetible) | Fechas: años |

Solo existe un tipo de selección de fechas a la vez; si el enlace contiene varios, tiene prioridad `fecha`, luego `desde`/`hasta`, `mes` y `anio`.

## Anexo C. Comandos

| Comando | Función |
|---|---|
| `python Scripts/run_snies.py [--sin-correo] [--archivo RUTA] [--fecha AAAA-MM-DD] [--forzar]` | Ejecución completa. |
| `python Scripts/generar_dashboard.py` | Regenera los datos y las páginas del Dashboard. |
| `python Scripts/generar_paginas.py` | Regenera solo las páginas HTML. |
| `python Scripts/reclasificar.py [--simular]` | Reclasifica el histórico y las novedades. |
| `python Scripts/completar_modificados.py [--simular] [--desde AAAA-MM-DD]` | Completa Modificados con cambios del histórico. |
| `python Scripts/ETL-PROGRAMAS-SNIES/Scripts/etl_programas.py [--dry-run] [--file NOMBRE] [--strict] [--force] [-v]` | ETL de forma independiente. |
| `python Scripts/CLASIFICAR-DIVISIONES-UNINORTE-SNIES/Scripts/pipeline_divisiones.py [-a N] [-s] [-v]` | Clasificador de forma independiente (`-a 1` procesa `Programas.xlsx`; `-s` simula). |
| `python -m unittest discover -s tests` | Pruebas unitarias. |
| `python tests/e2e_dashboard.py [--rapida]` | Pruebas de punta a punta. |
| `python -m http.server 8766 --directory docs` | Sirve el Dashboard localmente. |

## Anexo D. Constantes de configuración

| Constante | Archivo | Valor | Descripción |
|---|---|---|---|
| `CAIDA_MAXIMA` | `Scripts/run_snies.py` | 0,10 | Caída máxima de programas frente a la ejecución anterior. |
| `UMBRAL_MAXIMO` | `Scripts/run_snies.py` | 10.000 | Programas a partir de los cuales se asume una descarga sin filtros. |
| `DOWNLOAD_TIMEOUT` | `Scripts/run_snies.py` | 120 s | Espera máxima de la descarga. |
| `SUBPROCESO_TIMEOUT` | `Scripts/run_snies.py` | 1.800 s | Tiempo máximo de cada herramienta (ETL o clasificador). |
| `MAX_LOGS` | `Scripts/run_snies.py` | 10 | Registros conservados. |
| `RETRO_AVISO_MB`, `RETRO_CRITICO_MB` | `Scripts/run_snies.py` | 40, 80 | Umbrales del tamaño de la retroalimentación. |
| `COLS_VIGILAR` | `Scripts/run_snies.py` | 5 columnas | Columnas que generan registros de modificación. |
| `FILTROS_PORTAL` | `Scripts/run_snies.py` | 4 filtros | Valores que debe tener cada fila del reporte. |
| `SMTP_HOST`, `SMTP_PORT` | `Scripts/send_report.py` | `smtp.gmail.com`, 587 | Servidor de correo. |
| `INTENTOS_SMTP`, `ESPERA_SMTP` | `Scripts/send_report.py` | 3, (5, 15) s | Intentos de conexión y autenticación SMTP ante fallas transitorias, y espera antes del segundo y del tercero. |
| `LIMITE_HTML` | `Scripts/send_report.py` | 95 KB | Tamaño máximo del HTML del correo. |
| `DASHBOARD_URL` | `Scripts/send_report.py` | URL del sitio | Enlace del correo al Dashboard. |
| `MAYOR_INTERES`, `UNINORTE` | `Scripts/catalogos.py` | Códigos SNIES | Grupos de universidades. |
| `PESO_CAPA1`, `PESO_CAPA2_NOMBRE`, `PESO_CAPA3_CINE`, `PESO_CAPA3_NBC` | Clasificador, `config.py` | 0,50 · 0,25 · 0,15 · 0,10 | Pesos de la votación. |
| `UMBRAL_SIMILITUD_NOMBRE` | Clasificador, `config.py` | 0,70 | Similitud mínima de nombres. |
| `MAX_BACKUPS_RETRO` | Clasificador, `config.py` | 10 | Copias de respaldo locales de la retroalimentación. |

---

Fuente de los datos: Sistema Nacional de Información de la Educación Superior (SNIES), Ministerio de Educación Nacional. Dirección de Planeación, Universidad del Norte.
