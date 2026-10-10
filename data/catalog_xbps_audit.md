# Auditoría del catálogo XBPS

**Fecha de verificación:** 2026-10-08  
**Arquitectura:** `x86_64`  
**Fuentes:** Void `current`, la excepción explícita Void `nonfree` para Vivaldi y una selección separada de CuerdOS `stable`.

## Resultado actual

- **116** fichas con IDs únicos.
- **109** fichas de Void Linux verificadas en `current`.
- **1** ficha de Void `nonfree`: Vivaldi, incluida como excepción solicitada y etiquetada claramente.
- **6** aplicaciones conocidas de CuerdOS Stable, etiquetadas `CuerdOS stable`; requieren ese repositorio habilitado en XBPS y no se presentan como paquetes de Void `current`.
- No se incluyen otros paquetes de Void `nonfree`.
- Se excluyeron del conjunto CuerdOS FPM, Kron Installer, NWG Drawer, Runit Tools, los paquetes de Xfce y otros componentes de sistema.

## Auditoría Void `current`

**Fuente oficial:** [Void Linux — Packages](https://voidlinux.org/packages/)  
**Índice:** `https://xq-api.voidlinux.org/v1/query/x86_64`

Cada entrada `current` se contrastó por coincidencia exacta del nombre y del repositorio. Los nombres que no estaban en `current` se sustituyeron por alternativas verificadas, salvo Vivaldi, que se conservó también como excepción de `nonfree` a solicitud del usuario. Epiphany permanece como una alternativa del repositorio `current`.

Vivaldi se verificó en `nonfree` con versión `8.1.4087.75+1`; la ficha está marcada `nonfree` y destacada para que siga apareciendo aunque ese repositorio no esté habilitado.

| Entrada que no estaba en `current` | Tratamiento | Motivo |
|---|---|---|
| Vivaldi | Se conserva como `xbps:vivaldi` en `nonfree`; también se mantiene Epiphany en `current` | Excepción solicitada explícitamente |
| Steam | Reemplazado por `PrismLauncher` en `current` | No se añadió Steam desde `nonfree` |
| Discord | Reemplazado por `nheko` | Sin coincidencia exacta |
| Spotify | Reemplazado por `ncspot` | Sin coincidencia exacta |
| Pinta | Reemplazado por `kolourpaint` | Sin coincidencia exacta |
| VSCodium | Reemplazado por `lapce` | Sin coincidencia exacta |
| Neofetch | Reemplazado por `cpufetch` | Sin coincidencia exacta |
| Extreme Tux Racer | Reemplazado por `teeworlds` | Sin coincidencia exacta |
| GCompris | Reemplazado por `tuxpaint` | Sin coincidencia exacta |
| TuxMath | Reemplazado por `ktuberling` | Sin coincidencia exacta |
| Kalzium | Reemplazado por `qalculate-qt` | Sin coincidencia exacta |

El ID oficial sensible a mayúsculas de Thunar es `xbps:Thunar`.

## CuerdOS Stable

Solo permanecen los cuatro navegadores, Distrobox y Thonny. El detalle de versiones, licencias, criterios y requisitos del repositorio está en [la auditoría CuerdOS Stable](catalog_cuerdos_stable_audit.md).
