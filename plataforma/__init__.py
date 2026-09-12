import sys

from plataforma.base import Plataforma


def crear_plataforma(**kwargs) -> Plataforma:
    if sys.platform.startswith("linux"):
        from plataforma.linux import PlataformaLinux

        return PlataformaLinux(**kwargs)
    raise NotImplementedError(f"No hay implementación de Plataforma para {sys.platform!r} todavía")
