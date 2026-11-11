# Verificación GPU

Ejecutar `python -m unittest discover -s tests` desde esta carpeta.
Estas pruebas no ejecutan modelos ni certifican CUDA real.
El build verifica dependencias. Una ejecución L4 verifica CUDA antes del worker.
Validar con un audio conocido, revisar calidad y tiempos antes de ampliar volumen.
No se ha desplegado ni ejecutado inferencia GPU durante esta preparación.
