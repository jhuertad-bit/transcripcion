"""Entrada GPU: falla explícitamente si no puede usar CUDA; no recurre a CPU."""
import json
import os


def check_gpu(torch_module=None, ct2_module=None):
    if os.environ.get('MODEL_DEVICE') != 'cuda':
        raise ValueError('El Job GPU requiere MODEL_DEVICE=cuda')
    if torch_module is None:
        import torch as torch_module
    if ct2_module is None:
        import ctranslate2 as ct2_module
    if not torch_module.cuda.is_available():
        raise RuntimeError('CUDA no disponible: revisar imagen y GPU asignada')
    compute = os.environ.get('WHISPER_COMPUTE_TYPE', 'float16')
    if compute not in ct2_module.get_supported_compute_types('cuda'):
        raise RuntimeError('Tipo de cómputo no soportado por CTranslate2 en GPU')
    print(json.dumps({'accelerator': torch_module.cuda.get_device_name(0),
                      'cuda': torch_module.version.cuda, 'compute_type': compute}), flush=True)


if __name__ == '__main__':
    check_gpu()
    from worker.main import main
    main()
