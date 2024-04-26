#!/usr/bin/env python3
import argparse
import enum
import importlib.util
import logging
import pathlib
import typing

logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(
    description="Remove unused GFX support files from AMD ROCm",
)
parser.add_argument("gfx", nargs=1)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--verbose", action="store_true")

BASEDIRS = [
    "/usr/lib/rocblas/library",
    "/usr/lib64/rocblas/library",
    "{torch}/lib/rocblas/library",
    "{torch}/lib/hipblaslt/library",
]

# remove entire tree, e.g. /usr/lib64/rocm/gfx11
DIRTREES = [
    "/usr/lib/rocm/gfx{shortversion}",
    "/usr/lib64/rocm/gfx{shortversion}",
]


class IsaFeature(enum.IntEnum):
    """ROCR-Runtime src/core/inc/isa.h"""
    unsupported = 0
    any = 1
    disabled = 2
    enabled = 3


class IsaEntry(typing.NamedTuple):
    """ROCR-Runtime src/core/runtime/isa.cpp"""
    name: str
    major: int
    minor: int
    step: int
    sramecc: IsaFeature
    xnack: IsaFeature
    wavefrontsize: int
    # PyTorch ships a limited set of ISAs
    pytorch_support: bool = False

    @property
    def shortisa(self) -> str:
        """Short ISA name without xnack and sramecc flags"""
        return self.name.split(":", 1)[0]

    @property
    def shortgfx(self) -> str:
        """Short gfx version (gfx + major version)"""
        return f"gfx{self.major}"

    @property
    def hsa_gfx_version(self) -> str:
        """version for HSA_OVERRIDE_GFX_VERSION"""
        return f"{self.major}.{self.minor}.{self.step}"


# https://github.com/ROCm/ROCR-Runtime/blob/master/src/core/runtime/isa.cpp
# https://rocm.docs.amd.com/projects/install-on-linux/en/develop/reference/system-requirements.html
# https://llvm.org/docs/AMDGPUUsage.html#processors
# find lib/python*/site-packages/torch/lib -type f | grep -oE 'gfx[^.]*' | sort -u
IF = IsaFeature
ISAS = [
    IsaEntry("gfx700", 7, 0, 0, IF.unsupported, IF.unsupported, 64),
    IsaEntry("gfx701", 7, 0, 1, IF.unsupported, IF.unsupported, 64),
    IsaEntry("gfx702", 7, 0, 2, IF.unsupported, IF.unsupported, 64),
    IsaEntry("gfx801", 8, 0, 1, IF.unsupported, IF.any, 64),
    IsaEntry("gfx801:xnack-", 8, 0, 1, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx801:xnack+", 8, 0, 1, IF.unsupported, IF.enabled, 64),
    IsaEntry("gfx802", 8, 0, 2, IF.unsupported, IF.unsupported, 64),
    IsaEntry("gfx803", 8, 0, 3, IF.unsupported, IF.unsupported, 64),
    IsaEntry("gfx805", 8, 0, 5, IF.unsupported, IF.unsupported, 64),
    IsaEntry("gfx810", 8, 1, 0, IF.unsupported, IF.any, 64),
    IsaEntry("gfx810:xnack-", 8, 1, 0, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx810:xnack+", 8, 1, 0, IF.unsupported, IF.enabled, 64),
    # PyTorch has gfx900
    IsaEntry("gfx900", 9, 0, 0, IF.unsupported, IF.any, 64, True),
    IsaEntry("gfx900:xnack-", 9, 0, 0, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx900:xnack+", 9, 0, 0, IF.unsupported, IF.enabled, 64),
    IsaEntry("gfx902", 9, 0, 2, IF.unsupported, IF.any, 64),
    IsaEntry("gfx902:xnack-", 9, 0, 2, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx902:xnack+", 9, 0, 2, IF.unsupported, IF.enabled, 64),
    IsaEntry("gfx904", 9, 0, 4, IF.unsupported, IF.any, 64),
    IsaEntry("gfx904:xnack-", 9, 0, 4, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx904:xnack+", 9, 0, 4, IF.unsupported, IF.enabled, 64),
    # Radeon Instinct MI50, MI60
    # PyTorch has gfx906 with xnack-
    IsaEntry("gfx906", 9, 0, 6, IF.any, IF.any, 64, True),
    IsaEntry("gfx906:xnack-", 9, 0, 6, IF.any, IF.disabled, 64, True),
    IsaEntry("gfx906:xnack+", 9, 0, 6, IF.any, IF.enabled, 64),
    IsaEntry("gfx906:sramecc-", 9, 0, 6, IF.disabled, IF.any, 64),
    IsaEntry("gfx906:sramecc+", 9, 0, 6, IF.enabled, IF.any, 64),
    IsaEntry("gfx906:sramecc-:xnack-", 9, 0, 6, IF.disabled, IF.disabled, 64, True),
    IsaEntry("gfx906:sramecc-:xnack+", 9, 0, 6, IF.disabled, IF.enabled, 64),
    IsaEntry("gfx906:sramecc+:xnack-", 9, 0, 6, IF.enabled, IF.disabled, 64, True),
    IsaEntry("gfx906:sramecc+:xnack+", 9, 0, 6, IF.enabled, IF.enabled, 64),
    # AMD Instinct MI100 (CDNA)
    # PyTorch has gfx908 with xnack-
    IsaEntry("gfx908", 9, 0, 8, IF.any, IF.any, 64, True),
    IsaEntry("gfx908:xnack-", 9, 0, 8, IF.any, IF.disabled, 64, True),
    IsaEntry("gfx908:xnack+", 9, 0, 8, IF.any, IF.enabled, 64),
    IsaEntry("gfx908:sramecc-", 9, 0, 8, IF.disabled, IF.any, 64),
    IsaEntry("gfx908:sramecc+", 9, 0, 8, IF.enabled, IF.any, 64),
    IsaEntry("gfx908:sramecc-:xnack-", 9, 0, 8, IF.disabled, IF.disabled, 64, True),
    IsaEntry("gfx908:sramecc-:xnack+", 9, 0, 8, IF.disabled, IF.enabled, 64),
    IsaEntry("gfx908:sramecc+:xnack-", 9, 0, 8, IF.enabled, IF.disabled, 64, True),
    IsaEntry("gfx908:sramecc+:xnack+", 9, 0, 8, IF.enabled, IF.enabled, 64),
    IsaEntry("gfx909", 9, 0, 9, IF.unsupported, IF.any, 64),
    IsaEntry("gfx909:xnack-", 9, 0, 9, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx909:xnack+", 9, 0, 9, IF.unsupported, IF.enabled, 64),
    # AMD Instinct MI210, MI250, MI250X Accelerator (CDNA2)
    # PyTorch has gfx90a with xnack-, xnack+
    IsaEntry("gfx90a", 9, 0, 10, IF.any, IF.any, 64, True),
    IsaEntry("gfx90a:xnack-", 9, 0, 10, IF.any, IF.disabled, 64, True),
    IsaEntry("gfx90a:xnack+", 9, 0, 10, IF.any, IF.enabled, 64, True),
    IsaEntry("gfx90a:sramecc-", 9, 0, 10, IF.disabled, IF.any, 64),
    IsaEntry("gfx90a:sramecc+", 9, 0, 10, IF.enabled, IF.any, 64),
    IsaEntry("gfx90a:sramecc-:xnack-", 9, 0, 10, IF.disabled, IF.disabled, 64, True),
    IsaEntry("gfx90a:sramecc-:xnack+", 9, 0, 10, IF.disabled, IF.enabled, 64, True),
    IsaEntry("gfx90a:sramecc+:xnack-", 9, 0, 10, IF.enabled, IF.disabled, 64, True),
    IsaEntry("gfx90a:sramecc+:xnack+", 9, 0, 10, IF.enabled, IF.enabled, 64, True),
    # Ryzen APU (e.g. Ryzen 7 4700G)
    IsaEntry("gfx90c", 9, 0, 12, IF.unsupported, IF.any, 64),
    IsaEntry("gfx90c:xnack-", 9, 0, 12, IF.unsupported, IF.disabled, 64),
    IsaEntry("gfx90c:xnack+", 9, 0, 12, IF.unsupported, IF.enabled, 64),
    # ???
    IsaEntry("gfx940", 9, 4, 0, IF.any, IF.any, 64),
    IsaEntry("gfx940:xnack-", 9, 4, 0, IF.any, IF.disabled, 64),
    IsaEntry("gfx940:xnack+", 9, 4, 0, IF.any, IF.enabled, 64),
    IsaEntry("gfx940:sramecc-", 9, 4, 0, IF.disabled, IF.any, 64),
    IsaEntry("gfx940:sramecc+", 9, 4, 0, IF.enabled, IF.any, 64),
    IsaEntry("gfx940:sramecc-:xnack-", 9, 4, 0, IF.disabled, IF.disabled, 64),
    IsaEntry("gfx940:sramecc-:xnack+", 9, 4, 0, IF.disabled, IF.enabled, 64),
    IsaEntry("gfx940:sramecc+:xnack-", 9, 4, 0, IF.enabled, IF.disabled, 64),
    IsaEntry("gfx940:sramecc+:xnack+", 9, 4, 0, IF.enabled, IF.enabled, 64),
    # ???
    IsaEntry("gfx941", 9, 4, 1, IF.any, IF.any, 64),
    IsaEntry("gfx941:xnack-", 9, 4, 1, IF.any, IF.disabled, 64),
    IsaEntry("gfx941:xnack+", 9, 4, 1, IF.any, IF.enabled, 64),
    IsaEntry("gfx941:sramecc-", 9, 4, 1, IF.disabled, IF.any, 64),
    IsaEntry("gfx941:sramecc+", 9, 4, 1, IF.enabled, IF.any, 64),
    IsaEntry("gfx941:sramecc-:xnack-", 9, 4, 1, IF.disabled, IF.disabled, 64),
    IsaEntry("gfx941:sramecc-:xnack+", 9, 4, 1, IF.disabled, IF.enabled, 64),
    IsaEntry("gfx941:sramecc+:xnack-", 9, 4, 1, IF.enabled, IF.disabled, 64),
    IsaEntry("gfx941:sramecc+:xnack+", 9, 4, 1, IF.enabled, IF.enabled, 64),
    # AMD Instinct MI300A, MI300X Accelerator (CDNA2)
    # PyTorch 6.0 has gfx942
    IsaEntry("gfx942", 9, 4, 2, IF.any, IF.any, 64, True),
    IsaEntry("gfx942:xnack-", 9, 4, 2, IF.any, IF.disabled, 64),
    IsaEntry("gfx942:xnack+", 9, 4, 2, IF.any, IF.enabled, 64),
    IsaEntry("gfx942:sramecc-", 9, 4, 2, IF.disabled, IF.any, 64),
    IsaEntry("gfx942:sramecc+", 9, 4, 2, IF.enabled, IF.any, 64),
    IsaEntry("gfx942:sramecc-:xnack-", 9, 4, 2, IF.disabled, IF.disabled, 64),
    IsaEntry("gfx942:sramecc-:xnack+", 9, 4, 2, IF.disabled, IF.enabled, 64),
    IsaEntry("gfx942:sramecc+:xnack-", 9, 4, 2, IF.enabled, IF.disabled, 64),
    IsaEntry("gfx942:sramecc+:xnack+", 9, 4, 2, IF.enabled, IF.enabled, 64),
    # Radeon RX 5700, 5600
    IsaEntry("gfx1010", 10, 1, 0, IF.unsupported, IF.any, 32),
    IsaEntry("gfx1010:xnack-", 10, 1, 0, IF.unsupported, IF.disabled, 32),
    IsaEntry("gfx1010:xnack+", 10, 1, 0, IF.unsupported, IF.enabled, 32),
    # Radeon Pro V520
    IsaEntry("gfx1011", 10, 1, 1, IF.unsupported, IF.any, 32),
    IsaEntry("gfx1011:xnack-", 10, 1, 1, IF.unsupported, IF.disabled, 32),
    IsaEntry("gfx1011:xnack+", 10, 1, 1, IF.unsupported, IF.enabled, 32),
    # Radeon RX 5500
    IsaEntry("gfx1012", 10, 1, 2, IF.unsupported, IF.any, 32),
    IsaEntry("gfx1012:xnack-", 10, 1, 2, IF.unsupported, IF.disabled, 32),
    IsaEntry("gfx1012:xnack+", 10, 1, 2, IF.unsupported, IF.enabled, 32),
    # ???
    IsaEntry("gfx1013", 10, 1, 3, IF.unsupported, IF.any, 32),
    IsaEntry("gfx1013:xnack-", 10, 1, 3, IF.unsupported, IF.disabled, 32),
    IsaEntry("gfx1013:xnack+", 10, 1, 3, IF.unsupported, IF.enabled, 32),
    # Radeon RX 6800 to 6900 XT; Radeon PRO W6800 (RDNA2)
    # PyTorch has gfx1030
    IsaEntry("gfx1030", 10, 3, 0, IF.unsupported, IF.unsupported, 32, True),
    # Radeon RX 6700 XT
    IsaEntry("gfx1031", 10, 3, 1, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1032", 10, 3, 2, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1033", 10, 3, 3, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1034", 10, 3, 4, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1035", 10, 3, 5, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1036", 10, 3, 6, IF.unsupported, IF.unsupported, 32),
    # Radeon RX 7900 XT, XTX; Radeon PRO W7800, W7900 (RDNA3)
    # PyTorch has gfx1100
    IsaEntry("gfx1100", 11, 0, 0, IF.unsupported, IF.unsupported, 32, True),
    IsaEntry("gfx1101", 11, 0, 1, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1102", 11, 0, 2, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1103", 11, 0, 3, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1150", 11, 5, 0, IF.unsupported, IF.unsupported, 32),
    IsaEntry("gfx1151", 11, 5, 1, IF.unsupported, IF.unsupported, 32),
]
del IF

ISA_MAP = {e.name: e for e in ISAS}


def get_torchdir() -> typing.Optional[pathlib.Path]:
    spec = importlib.util.find_spec("torch")
    if spec is None:
        return None
    # spec.origin points to .../site-packages/torch/__init__.py
    return pathlib.Path(spec.origin).parent


def parse_gfx(gfx_string: str) -> typing.Tuple[set, set]:
    # https://github.com/ROCm/ROCR-Runtime/blob/master/src/core/runtime/isa.cpp
    shortversions = set()
    shortisas = set()
    # ; separated list
    for gfx in gfx_string.split(";"):
        isa = ISA_MAP[gfx]
        logger.debug("%s", isa)
        shortversions.add(isa.shortgfx)
        shortisas.add(isa.shortisa)
    return shortisas, shortversions


def test():
    targets = "gfx900;gfx906:xnack-;gfx908:xnack-;gfx90a:xnack-;gfx90a:xnack+;gfx942;gfx1030;gfx1100"
    print(parse_gfx(targets))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test()
