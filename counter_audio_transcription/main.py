"""Punto de entrada de Cloud Run functions (desplegar desde esta carpeta)."""
import functions_framework
from cf_trigger.handler import handle


@functions_framework.http
def trigger_counter(request):
    return handle(request)
