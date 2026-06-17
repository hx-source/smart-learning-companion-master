"""Compatibility helpers for third-party packages on NumPy 2.x."""


def patch_numpy_sctypes():
    """Restore the small np.sctypes mapping expected by older OCR/image deps."""
    try:
        import numpy as np
    except Exception:
        return

    if hasattr(np, 'sctypes'):
        return

    np.sctypes = {
        'int': [np.int8, np.int16, np.int32, np.int64],
        'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
        'float': [np.float16, np.float32, np.float64],
        'complex': [np.complex64, np.complex128],
        'others': [np.bool_, np.bytes_, np.str_, np.object_],
    }
