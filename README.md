# Yelena Software

Un **centro de software** para sistemas basados en **Void Linux**, diseñado alrededor de **XBPS** (X Binary Package System). **Yelena Software** utiliza **Python** y **PySide6** para ofrecer una interfaz visual ligera que reúne una **tienda de aplicaciones**, un **gestor de actualizaciones** y diversas **utilidades de paquetería**.

La aplicación permite explorar aplicaciones, consultar información de paquetes, administrar instalaciones y eliminaciones, revisar actualizaciones del sistema y utilizar herramientas de mantenimiento de XBPS desde una interfaz unificada.

## Organización del proyecto

```text
backend/       Adaptadores XBPS, Flatpak, FPM y supervisor común de procesos
engine/        Selección y enrutamiento del motor de paquetes
core/          Configuración, catálogo, búsqueda e internacionalización
ui/            Páginas y widgets de PySide6
resources/     Iconos, imágenes, estilos, datos y archivos del sistema
  icons/       SVG de la aplicación y de remotos
  images/      Imágenes locales (incluido el Easter egg KDI)
  styles/      Tema Qt y hojas de estilo
  data/        Catálogos, blacklist y datos auxiliares
translations/  Catálogos JSON por idioma
tests/         Pruebas automatizadas
```

## Requisitos de Dependencias

Antes de ejecutar Yelena Software, asegúrate de contar con los siguientes paquetes en tu sistema Void:

- `python3` (entorno de ejecución)
- `python3-pyside6` (módulos base de PySide6)
- `python3-pyside6-widgets` (componentes de interfaz y widgets de Qt)
- `python3-pyside6-gui` (componentes gráficos como iconos, imágenes, colores y fuentes)
- `python3-pyside6-svg` (soporte para recursos SVG)
- `xbps` (sistema nativo de gestión de paquetes)
- `polkit` y `pkexec` (autorización para operaciones administrativas)

Puedes instalar las dependencias principales con:

```bash
sudo xbps-install -S python3 python3-pyside6 python3-pyside6-widgets python3-pyside6-gui python3-pyside6-svg
```

## Instalación y Ejecución

Para descargar el proyecto y preparar el entorno de ejecución, ejecuta:

```bash
# Clonar el repositorio
git clone https://github.com/Yelena-Software/yelena-software.git
cd yelena-software

# Ejecutar la aplicación
python3 main.py
```

Si utilizas un entorno virtual de Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PySide6
python3 main.py
```

## Uso

Una vez iniciada, la aplicación ofrece sus funciones mediante una interfaz gráfica basada en PySide6:

```text
Funciones principales:
  Tienda de aplicaciones para explorar software
  Gestor de actualizaciones del sistema
  Buscar aplicaciones y paquetes en los repositorios XBPS
  Mostrar información detallada de paquetes
  Instalar y eliminar aplicaciones
  Listar paquetes instalados
  Limpiar la caché de paquetes
  Consultar dependencias y utilidades de paquetería
```

Con **FPM 2.0.8 o posterior**, las búsquedas también muestran resultados AppImage de AM mediante NISSA v2, con versión/tamaño del inventario e icono del catálogo cuando están disponibles. En **Ajustes → Paquetes** está la acción **«Instalar am-pack (repositorio de AppImage de portable-linux-apps.github.io)»**: usa el instalador upstream verificado por FPM, instala AppMan si hace falta y activa/sincroniza `am-FUSION`. Las instalaciones, actualizaciones y eliminaciones del catálogo AM se ejecutan mediante `fpm aimg`; FPM guarda qué frontend se eligió y la GUI prefiere AppMan en modo de usuario.

Los AppImages instalados desde archivo local y desde `am-FUSION` se conservan como registros distintos. Si ambos corresponden a la misma aplicación, la copia local se muestra como **«Aplicación (Local)»** para distinguirla de la instalada desde AM.

Las operaciones de sistema de XBPS pueden solicitar permisos administrativos mediante polkit/`pkexec`. Las instalaciones AppImage locales y las de am-FUSION se enrutan por `fpm aimg`; la GUI no las sustituye por comandos XBPS. Si un script upstream de AM/AppMan invoca `sudo`, Yel-Soft antepone, solo a ese proceso y sin elevar FPM completo, un wrapper temporal que entrega la solicitud a `pkexec` para mostrar la autorización gráfica. Los argumentos se pasan sin shell y la opción `sudo -S` se descarta: no se lee ni se solicita una contraseña por stdin.

Las tareas AppImage abren automáticamente **Tareas** con la consola visible. La salida FPM/AM se transmite en vivo incluso cuando el instalador reescribe progreso con `\r`; durante fases silenciosas se muestra una barra indeterminada y el tiempo transcurrido, sin inventar porcentajes. Cuando FPM entrega el evento estructurado `FPM_AIMG_EVENT`, la GUI muestra bytes descargados y versión detectada; si se conoce el tamaño total, también calcula el avance. La versión y el tamaño finales actualizan los datos de la aplicación. La cancelación termina el grupo de procesos FPM/AM para evitar descargas huérfanas. El supervisor común está en `backend/process_stream.py`.

Si una salida AM contiene el error `sudo: a terminal is required ...` o `sudo: a password is required`, Yel-Soft marca la tarea como fallida aunque AM devuelva código cero y muestra el diagnóstico `appimage_task_privilege_required`; revisa si `pkexec`/polkit está instalado y consulta el registro visible de la tarea. La frase `sudo ... -S` pertenece al propio sudo y no indica `xbps-install -S`. FPM 2.0.8 conserva la selección AppMan/AM, el tamaño del inventario común, los metadatos NISSA v2 y emite los eventos estructurados de progreso/instalación que consume la GUI.

Los backends se cargan antes de iniciar los hilos de tareas; esto evita errores de importación como `KeyError: 'backend'` al ejecutar la aplicación desde IDEs que interceptan imports, incluido Thonny.

Durante una actualización del sistema, el applet muestra una única notificación de estado y anima el icono de la bandeja; se silencian los avisos periódicos hasta que termina la transacción.



## Licencia

Yelena Software se distribuye bajo la **GNU General Public License v3.0**. Consulta el archivo `LICENSE` para conocer el texto completo de la licencia y las condiciones de redistribución.

Los componentes de terceros mantienen sus propias licencias. XBPS se distribuye bajo una licencia BSD simplificada de dos cláusulas, mientras que PySide6 y Qt for Python utilizan las licencias comunitarias de Qt, incluyendo LGPLv3 y GPLv3.[1] [2]

No redistribuyas iconos, traducciones, fuentes u otros recursos de terceros sin revisar sus avisos de copyright y sus condiciones de uso.
