"""Entrada Counter GPU: no permite fallback silencioso a CPU."""
import json
import logging
import os


def check_gpu(torch_module=None, ct2_module=None):
    if os.environ.get('MODEL_DEVICE') != 'cuda' or os.environ.get('WHISPER_COMPUTE_TYPE') != 'float16':
        raise ValueError('Counter GPU requiere cuda y float16.')
    if torch_module is None:
        import torch as torch_module
    if ct2_module is None:
        import ctranslate2 as ct2_module
    if not torch_module.cuda.is_available():
        raise RuntimeError('CUDA no disponible; no se ejecuta en CPU.')
    if 'float16' not in ct2_module.get_supported_compute_types('cuda'):
        raise RuntimeError('CTranslate2 no soporta float16 en esta GPU.')
    print(json.dumps({'accelerator': torch_module.cuda.get_device_name(0),
                      'cuda': torch_module.version.cuda, 'compute_type': 'float16'}), flush=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    check_gpu()
    from worker.main import main
    main()
