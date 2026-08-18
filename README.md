# Yelena Software

Un **centro de software** para sistemas basados en **Void Linux**, diseñado alrededor de **XBPS** (X Binary Package System). **Yelena Software** utiliza **Python** y **PySide6** para ofrecer una interfaz visual ligera que reúne una **tienda de aplicaciones**, un **gestor de actualizaciones** y diversas **utilidades de paquetería**.

La aplicación permite explorar aplicaciones, consultar información de paquetes, administrar instalaciones y eliminaciones, revisar actualizaciones del sistema y utilizar herramientas de mantenimiento de XBPS desde una interfaz unificada.

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

Las operaciones de instalación, eliminación y actualización pueden solicitar permisos administrativos mediante polkit, `pkexec`, `sudo` o `doas`, según la configuración del sistema.


## Licencia

Yelena Software se distribuye bajo la **GNU General Public License v3.0**. Consulta el archivo `LICENSE` para conocer el texto completo de la licencia y las condiciones de redistribución.

Los componentes de terceros mantienen sus propias licencias. XBPS se distribuye bajo una licencia BSD simplificada de dos cláusulas, mientras que PySide6 y Qt for Python utilizan las licencias comunitarias de Qt, incluyendo LGPLv3 y GPLv3.[1] [2]

No redistribuyas iconos, traducciones, fuentes u otros recursos de terceros sin revisar sus avisos de copyright y sus condiciones de uso.
