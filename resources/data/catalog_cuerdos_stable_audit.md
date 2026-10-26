# Auditoría de aplicaciones en CuerdOS Stable

**Fecha de verificación:** 2026-10-08  
**Arquitectura:** `x86_64`  
**Índice consultado:** [CuerdOS Stable](https://pkgs.cuerdos.es/stable/)  
**Metadatos:** [`x86_64-repodata`](https://pkgs.cuerdos.es/stable/x86_64-repodata)

Se contrastaron nombre, versión, descripción, licencia y tamaño de descarga con los archivos `.xbps` y el índice del repositorio. El catálogo de inicio conserva solo programas de usuario reconocibles: cuatro navegadores y dos aplicaciones adicionales. Se retiraron las utilidades de sistema, FPM, Kron Installer, NWG Drawer, Runit Tools y los paquetes de Xfce que se habían agregado anteriormente.

Las fichas usan el motor `xbps`, tienen el tag `CuerdOS stable` y conservan el `pkgname` exacto. **Para instalarlas, CuerdOS Stable debe estar configurado y habilitado en XBPS.** No se cambió la configuración de repositorios del sistema.

## Navegadores

| Nombre | Paquete | Versión del índice | Categoría | Licencia informada |
|---|---|---:|---|---|
| Brave Browser | `brave-browser` | `1.96.60_1` | Internet | MPL-2.0, BSD-3-Clause, custom:chromium |
| Helium Browser | `helium` | `0.17.0.1_1` | Internet | GPL-3.0-only, BSD-3-Clause, custom:chromium |
| LibreWolf | `librewolf` | `151.0.3_2` | Internet | MPL-2.0, GPL-2.0-or-later, LGPL-2.1-or-later |
| Zen Browser | `zen-browser` | `1.23b_1` | Internet | MPL-2.0, GPL-2.0-or-later, LGPL-2.1-or-later |

Brave y Helium aparecen como lanzamientos binarios y declaran `custom:chromium`; ese dato queda preservado en sus fichas.

## Otros programas conocidos

| Nombre | Paquete | Versión del índice | Interfaz | Categoría | Descripción del índice |
|---|---|---:|---|---|---|
| Distrobox | `distrobox` | `1.8.2.5_2` | CLI | Utilities | Ejecuta distribuciones Linux desde la terminal |
| Thonny | `thonny` | `4.1.7_2` | GUI | Development | IDE de Python para principiantes |

Se excluyeron bibliotecas, paquetes `-devel`, plugins, daemons, greeters, gestores de sesión, herramientas de administración y componentes de Xfce.
